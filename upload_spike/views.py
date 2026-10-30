"""The spike's page and its JSON endpoints. Each answers 404 unless UPLOAD_SPIKE_ENABLED is
on and the user is active staff (the routes are also left out of the URLconf while the
setting is off). Errors are `{"error": {"code", "message"}}` with the HTTP status."""
import json
import logging
from functools import wraps

from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import s3

logger = logging.getLogger(__name__)


def spike_staff_only(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not (settings.UPLOAD_SPIKE_ENABLED and user.is_authenticated
                and user.is_active and user.is_staff):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapper


def _error(status, code, message):
    return JsonResponse({'error': {'code': code, 'message': message}}, status=status)


def _json_body(request):
    try:
        return json.loads(request.body or b'null')
    except (ValueError, UnicodeDecodeError):
        return None


def _not_configured():
    name = s3.missing_setting()
    if name:
        return _error(503, 'not_configured', f'{name} is not set')
    return None


@never_cache
@spike_staff_only
@require_GET
def page(request):
    return render(request, 'upload_spike/page.html', {
        'missing_setting': s3.missing_setting(),
        'bucket': settings.UPLOAD_SPIKE_S3_BUCKET,
        'endpoint': settings.UPLOAD_SPIKE_S3_ENDPOINT,
    })


@never_cache
@spike_staff_only
@require_POST
def sign(request):
    """Presign one S3 request for Uppy's `signRequest` (see upload_spike.s3)."""
    body = _json_body(request)
    if body is None:
        return _error(400, 'bad_request', 'the body must be JSON')
    try:
        operation = s3.operation_for(body)  # validated before any credentials are needed
    except s3.BadSignRequest as exc:
        return _error(400, 'bad_request', str(exc))
    if (refusal := _not_configured()):
        return refusal
    return JsonResponse(s3.presign_operation(operation))


@never_cache
@spike_staff_only
@require_POST
def stat(request):
    """Key, size and ETag of an uploaded object, read from the bucket (HeadObject)."""
    from botocore.exceptions import BotoCoreError, ClientError

    body = _json_body(request)
    key = body.get('key') if isinstance(body, dict) else None
    if not s3.is_spike_key(key):
        return _error(400, 'bad_request', 'key must be a spike/<uuid> key made by this endpoint')
    if (refusal := _not_configured()):
        return refusal
    try:
        return JsonResponse(s3.describe(key))
    except ClientError as exc:
        error = exc.response.get('Error', {})
        code = str(error.get('Code', 'error'))
        status = 404 if code in ('404', 'NoSuchKey', 'NotFound') else 502
        return _error(status, 's3_error', f'S3 answered {code}')
    except BotoCoreError as exc:
        logger.warning('upload spike: HeadObject failed: %s', type(exc).__name__)
        return _error(502, 's3_error', f'S3 request failed ({type(exc).__name__})')
