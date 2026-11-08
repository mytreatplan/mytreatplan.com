"""The upload endpoints the widget (storage/upload.js) calls; JSON in and out, errors as
`{"error": {"code", "message"}}` with the HTTP status. Both need a logged-in, active
user (403 otherwise) and a CSRF token.

- `POST /storage/sign/`: presign one S3 multipart request (storage.s3). Starting an
  upload names its purpose and size: the purpose's `can_upload` hook and size limit are
  checked before anything is signed, and a PendingUpload remembers them. Every later
  request must name the uploader's own pending key, and the S3 upload id the first of
  them bound to it (400 otherwise); the hook is asked again each time.
- `POST /storage/complete/`: after S3 completed the upload, HeadObject must show the
  declared size; then the StoredObject is created (the original file name encrypted) and
  the PendingUpload removed, and its virus scan enqueued when that commits
  (storage.tasks.scan_object_task). A wrong size deletes the object (400).

Nothing here logs a key, URL, file name or credential (AD-23).
"""
import json
import logging
import posixpath
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from mytp_core.tasks import enqueue_on_commit

from . import purposes, s3
from .models import ORIGINAL_NAME_MAX_LENGTH, PendingUpload, StoredObject
from .tasks import scan_object_task

logger = logging.getLogger(__name__)


def _error(status, code, message):
    return JsonResponse({'error': {'code': code, 'message': message}}, status=status)


def _json_body(request):
    try:
        return json.loads(request.body or b'null')
    except (ValueError, UnicodeDecodeError):
        return None


def active_user_required(view):
    """403 (JSON) unless the user is logged in and active: these endpoints are called by
    script, so no redirect to the login page."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not (user.is_authenticated and user.is_active):
            return _error(403, 'forbidden', 'log in to upload files')
        return view(request, *args, **kwargs)
    return wrapper


def _not_configured():
    name = s3.missing_setting()
    return _error(503, 'not_configured', f'{name} is not set') if name else None


def _forbidden():
    return _error(403, 'forbidden', 'you may not upload files for this purpose')


def _purge_abandoned():
    """Forget pending uploads older than the bucket's lifecycle period (their parts are
    gone from the bucket by then)."""
    cutoff = timezone.now() - timedelta(days=settings.STORAGE_ABORT_INCOMPLETE_DAYS)
    PendingUpload.objects.filter(created_at__lt=cutoff).delete()


def _start(request, body):
    purpose = purposes.get(body.get('purpose'))
    if purpose is None:
        return _error(400, 'bad_request', 'purpose is not a known purpose')
    size = body.get('size')
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        return _error(400, 'bad_request', 'size must be a positive number of bytes')
    try:
        content_type = s3.content_type_of(body)
    except s3.BadSignRequest as exc:
        return _error(400, 'bad_request', str(exc))
    if not purposes.may_upload(purpose, request.user):
        return _forbidden()
    if size > purpose.max_size:
        return _error(413, 'too_large',
                      f'the file is larger than the limit of {purpose.max_size} bytes')
    if (refusal := _not_configured()):
        return refusal
    _purge_abandoned()
    pending = PendingUpload.objects.create(
        id=s3.new_object_id(), purpose=purpose.name, user=request.user, size=size,
        content_type=content_type)
    key = s3.quarantine_key(pending.pk)
    url = s3.presign_operation(s3.CREATE, {'Key': key})
    return JsonResponse({'url': url, 'key': key})


def _bind_upload_id(pending, upload_id):
    """True when `upload_id` is the pending upload's S3 upload (bound now if it has none
    yet)."""
    if pending.upload_id:
        return pending.upload_id == upload_id
    PendingUpload.objects.filter(pk=pending.pk, upload_id='').update(upload_id=upload_id)
    pending.refresh_from_db(fields=['upload_id'])
    return pending.upload_id == upload_id


def _continue(request, body, name):
    try:
        object_id = s3.key_of(body)
        upload_id = s3.upload_id_of(body)
        part_number = s3.part_number_of(body) if name == s3.UPLOAD_PART else None
    except s3.BadSignRequest as exc:
        return _error(400, 'bad_request', str(exc))
    pending = PendingUpload.objects.filter(pk=object_id, user=request.user).first()
    if pending is None:
        return _error(400, 'bad_request', 'key is not an upload of yours in progress')
    if not purposes.may_upload(purposes.get(pending.purpose), request.user):
        return _forbidden()
    if part_number is not None and part_number > s3.max_part_number(pending.size):
        return _error(400, 'bad_request', 'partNumber is beyond the declared size')
    if (refusal := _not_configured()):
        return refusal
    if not _bind_upload_id(pending, upload_id):
        return _error(400, 'bad_request', 'uploadId is not the one of this upload')
    params = {'Key': s3.quarantine_key(object_id), 'UploadId': upload_id}
    if part_number is not None:
        params['PartNumber'] = part_number
    url = s3.presign_operation(name, params)
    if name == s3.ABORT:
        pending.delete()
    return JsonResponse({'url': url})


@never_cache
@require_POST
@active_user_required
def sign(request):
    """Presign one S3 multipart request for Uppy's `signRequest` (see the module
    docstring)."""
    body = _json_body(request)
    try:
        name = s3.operation_name(body)
    except s3.BadSignRequest as exc:
        return _error(400, 'bad_request', str(exc))
    if name == s3.CREATE:
        return _start(request, body)
    return _continue(request, body, name)


def clean_original_name(name):
    """The file's own name (no folders), or None when it is not a usable name."""
    if not isinstance(name, str):
        return None
    name = posixpath.basename(name.replace('\\', '/')).strip()
    if not name or len(name) > ORIGINAL_NAME_MAX_LENGTH or not name.isprintable():
        return None
    return name


@never_cache
@require_POST
@active_user_required
def complete(request):
    """Record a completed upload as a StoredObject (see the module docstring). Answers
    `{"id", "size", "purpose"}`; a second call for the same upload answers the same."""
    from botocore.exceptions import BotoCoreError, ClientError

    body = _json_body(request)
    if not isinstance(body, dict):
        return _error(400, 'bad_request', 'the body must be a JSON object')
    object_id = s3.object_id_from_key(body.get('key'))
    if object_id is None:
        return _error(400, 'bad_request', 'key must be a quarantine/<uuid> key made by this endpoint')
    name = clean_original_name(body.get('name'))
    if name is None:
        return _error(400, 'bad_request', f'name must be a file name of 1-{ORIGINAL_NAME_MAX_LENGTH} characters')
    pending = PendingUpload.objects.filter(pk=object_id, user=request.user).first()
    if pending is None:
        done = StoredObject.objects.filter(pk=object_id, uploaded_by=request.user).first()
        if done is not None:
            return JsonResponse({'id': str(done.pk), 'size': done.size, 'purpose': done.purpose})
        return _error(400, 'bad_request', 'key is not an upload of yours in progress')
    purpose = purposes.get(pending.purpose)
    if not purposes.may_upload(purpose, request.user):
        return _forbidden()
    if (refusal := _not_configured()):
        return refusal
    key = s3.quarantine_key(object_id)
    try:
        size = s3.head_size(key)
    except ClientError as exc:
        if s3.is_not_found(exc):
            return _error(400, 'not_uploaded', 'the bucket has no such object yet')
        logger.warning('storage: HeadObject failed for %s (%s)', object_id, s3.describe_error(exc))
        return _error(502, 's3_error', 'the bucket could not be read')
    except BotoCoreError as exc:
        logger.warning('storage: HeadObject failed for %s (%s)', object_id, type(exc).__name__)
        return _error(502, 's3_error', 'the bucket could not be read')
    if size != pending.size or size > purpose.max_size:
        logger.warning('storage: upload %s has %s bytes, %s declared; deleted',
                       object_id, size, pending.size)
        try:
            s3.delete_keys([key])
        except (ClientError, BotoCoreError) as exc:
            # The object stays until someone deletes it; it has no row, so it is never served.
            logger.warning('storage: deleting %s failed (%s)', object_id, s3.describe_error(exc))
        pending.delete()
        return _error(400, 'size_mismatch', 'the stored file does not have the declared size')
    try:
        with transaction.atomic():
            stored = StoredObject.objects.create(
                id=object_id, purpose=pending.purpose, uploaded_by=request.user, size=size,
                content_type=pending.content_type, original_name=name)
            pending.delete()
            # The virus scan (epic-platform entry 12), once the row is committed; only
            # the object's UUID goes into the task.
            enqueue_on_commit(scan_object_task, str(object_id))
    except IntegrityError:
        # A concurrent call for the same upload recorded it first: answer as it did.
        stored = StoredObject.objects.filter(pk=object_id, uploaded_by=request.user).first()
        if stored is None:
            raise
    return JsonResponse({'id': str(stored.pk), 'size': stored.size, 'purpose': stored.purpose})
