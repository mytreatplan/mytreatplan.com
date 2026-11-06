"""The region's file bucket (AD-9): its S3 client, the object keys, and the presigning
of the browser's S3 requests and of downloads.

Keys are made here only: `quarantine/<uuid4>` for an upload, `clean/<uuid4>` once entry
12's scan has passed it (this module only defines it). The UUID is the StoredObject's
primary key. The browser's key and the file name never reach a key (AD-2, AD-5).

The browser (Uppy `@uppy/aws-s3`, `signRequest`) asks for one S3 request at a time;
`operation_for` maps it to exactly one multipart operation (storage.views.sign checks
who may ask for it first):

    POST   no uploadId          -> CreateMultipartUpload  (new key)
    PUT    uploadId, partNumber -> UploadPart
    GET    uploadId             -> ListParts
    POST   uploadId             -> CompleteMultipartUpload
    DELETE uploadId             -> AbortMultipartUpload

A single PutObject is never signed: every upload is multipart (one code path, and the
size is checked at completion by HeadObject). Anything else is refused (`BadSignRequest`).
No key, URL or credential is ever logged here (AD-23). Sentry's boto3 and stdlib
integrations record each S3 request's URL (bucket and object key) as a breadcrumb, which
a later error event would carry; so every S3 call goes through `out_of_sentry()`.
"""
import re
import uuid
from contextlib import contextmanager

import sentry_sdk
from django.conf import settings

QUARANTINE_PREFIX = 'quarantine/'
CLEAN_PREFIX = 'clean/'
PREFIXES = (QUARANTINE_PREFIX, CLEAN_PREFIX)
_UUID4 = r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'
QUARANTINE_KEY_RE = re.compile(rf'^quarantine/({_UUID4})$')

# Long enough for a slow part (16 MiB at ~20 kB/s); each request is signed just before it
# is sent.
SIGNED_URL_EXPIRES = 15 * 60
# AD-9: presigned GETs valid at most 5 minutes.
DOWNLOAD_URL_EXPIRES = 5 * 60
MAX_PART_NUMBER = 10_000
# S3's smallest part (all but the last); bounds the part numbers a declared size allows.
MIN_PART_SIZE = 5 * 1024 * 1024
MAX_UPLOAD_ID_LENGTH = 1024
MAX_CONTENT_TYPE_LENGTH = 255
_CONTENT_TYPE_RE = re.compile(r'^[\w.+-]+/[\w.+-]+(;[ \w.+=-]*)*$')

REQUIRED_SETTINGS = (
    'STORAGE_ENDPOINT',
    'STORAGE_REGION',
    'STORAGE_BUCKET',
    'STORAGE_ACCESS_KEY',
    'STORAGE_SECRET_KEY',
)


class BadSignRequest(ValueError):
    """The browser asked for something that is not one of the allowed S3 requests."""


def missing_setting():
    """The first empty STORAGE_* setting the bucket needs, or None when all are set."""
    for name in REQUIRED_SETTINGS:
        if not getattr(settings, name, ''):
            return name
    return None


def make_client():
    """The S3-compatible client for the region's file bucket (OVHcloud Object Storage)."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        's3',
        endpoint_url=settings.STORAGE_ENDPOINT,
        region_name=settings.STORAGE_REGION,
        aws_access_key_id=settings.STORAGE_ACCESS_KEY,
        aws_secret_access_key=settings.STORAGE_SECRET_KEY,
        config=Config(
            signature_version='s3v4',
            # OVHcloud documents virtual-hosted URLs (https://<bucket>.s3.<region>.io.cloud.ovh.net).
            s3={'addressing_style': 'virtual'},
            # No CRC checksums unless the API requires them (as in backupdb): the browser
            # could not send matching checksum headers on a presigned request.
            request_checksum_calculation='when_required',
            response_checksum_validation='when_required',
            retries={'max_attempts': 3, 'mode': 'standard'},
            connect_timeout=10,
            read_timeout=30,
        ),
    )


@contextmanager
def out_of_sentry():
    """Breadcrumbs recorded inside (the S3 request URLs) are dropped on the way out: they
    go to a forked isolation scope (AD-23)."""
    with sentry_sdk.isolation_scope():
        yield


def quarantine_key(object_id):
    return f'{QUARANTINE_PREFIX}{object_id}'


def clean_key(object_id):
    return f'{CLEAN_PREFIX}{object_id}'


def object_keys(object_id):
    """Every key an object may have (both prefixes), for deletion."""
    return [f'{prefix}{object_id}' for prefix in PREFIXES]


def new_object_id():
    return uuid.uuid4()


def object_id_from_key(key):
    """The UUID of a `quarantine/<uuid4>` key, or None for anything else."""
    if not isinstance(key, str):
        return None
    match = QUARANTINE_KEY_RE.match(key)
    return uuid.UUID(match.group(1)) if match else None


def key_of(request):
    """The upload's object UUID named by a later request; raises BadSignRequest."""
    object_id = object_id_from_key(request.get('key'))
    if object_id is None:
        raise BadSignRequest('key must be a quarantine/<uuid> key made by this endpoint')
    return object_id


def upload_id_of(request):
    upload_id = request.get('uploadId')
    if not isinstance(upload_id, str) or not upload_id.strip():
        raise BadSignRequest('uploadId is required')
    if len(upload_id) > MAX_UPLOAD_ID_LENGTH or not upload_id.isprintable() or ' ' in upload_id:
        raise BadSignRequest('uploadId is malformed')
    return upload_id


def part_number_of(request):
    number = request.get('partNumber')
    # bool is an int in Python; a JSON true is not a part number.
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= MAX_PART_NUMBER:
        raise BadSignRequest(f'partNumber must be an integer from 1 to {MAX_PART_NUMBER}')
    return number


def content_type_of(request):
    content_type = request.get('contentType')
    if content_type in (None, ''):
        return ''
    if (not isinstance(content_type, str) or len(content_type) > MAX_CONTENT_TYPE_LENGTH
            or not _CONTENT_TYPE_RE.match(content_type)):
        raise BadSignRequest('contentType is malformed')
    return content_type


def max_part_number(size):
    """The highest part number an upload of `size` bytes can need (parts of at least
    MIN_PART_SIZE, but the last)."""
    return min(MAX_PART_NUMBER, max(1, -(-size // MIN_PART_SIZE)))


# The operation names: storage.views.sign and the tests use them.
CREATE = 'create_multipart_upload'
UPLOAD_PART = 'upload_part'
LIST_PARTS = 'list_parts'
COMPLETE = 'complete_multipart_upload'
ABORT = 'abort_multipart_upload'

HTTP_METHODS = {
    CREATE: 'POST',
    UPLOAD_PART: 'PUT',
    LIST_PARTS: 'GET',
    COMPLETE: 'POST',
    ABORT: 'DELETE',
}


def operation_name(request):
    """Which multipart operation a browser request is; raises BadSignRequest."""
    if not isinstance(request, dict):
        raise BadSignRequest('the body must be a JSON object')
    method = request.get('method')
    has_upload = request.get('uploadId') not in (None, '')
    if method == 'POST':
        return COMPLETE if has_upload else CREATE
    if method == 'PUT':
        if not has_upload:
            raise BadSignRequest('single PUT uploads are not signed; use multipart')
        return UPLOAD_PART
    if method in ('GET', 'DELETE'):
        if not has_upload:
            raise BadSignRequest(f'{method} needs an uploadId')
        return LIST_PARTS if method == 'GET' else ABORT
    raise BadSignRequest('method must be POST, PUT, GET or DELETE')


def presign_operation(name, params, client=None):
    """A presigned URL for one multipart operation on the bucket (`params` without
    Bucket)."""
    client = client or make_client()
    with out_of_sentry():
        return client.generate_presigned_url(
            name,
            Params={'Bucket': settings.STORAGE_BUCKET, **params},
            ExpiresIn=SIGNED_URL_EXPIRES,
            HttpMethod=HTTP_METHODS[name],
        )


def head_size(key, client=None):
    """The stored size of `key` (HeadObject); botocore's ClientError when it is missing."""
    client = client or make_client()
    with out_of_sentry():
        head = client.head_object(Bucket=settings.STORAGE_BUCKET, Key=key)
    return head.get('ContentLength')


def delete_keys(keys, client=None):
    """Delete each key (S3 answers success for a key that is already gone)."""
    client = client or make_client()
    with out_of_sentry():
        for key in keys:
            client.delete_object(Bucket=settings.STORAGE_BUCKET, Key=key)


def presign_download(key, *, content_type='', filename='', client=None):
    """A presigned GET for `key`, valid DOWNLOAD_URL_EXPIRES (5 minutes at most), served
    as an attachment named `filename` (never the original file name: see
    storage.service)."""
    client = client or make_client()
    params = {'Bucket': settings.STORAGE_BUCKET, 'Key': key}
    if filename:
        params['ResponseContentDisposition'] = f'attachment; filename="{filename}"'
    if content_type:
        params['ResponseContentType'] = content_type
    with out_of_sentry():
        return client.generate_presigned_url(
            'get_object', Params=params, ExpiresIn=DOWNLOAD_URL_EXPIRES, HttpMethod='GET')


def scrub(text):
    """`text` with the bucket keys replaced (for command output)."""
    text = str(text)
    for secret in (settings.STORAGE_SECRET_KEY, settings.STORAGE_ACCESS_KEY):
        if secret:
            text = text.replace(secret, '[redacted]')
    return text


def describe_error(exc, *, with_message=False):
    """A short, credential-free description of an S3 failure: its code and HTTP status
    (and S3's message, for the bucket command), or its class name (never the exception
    text, which may quote a URL or key)."""
    response = getattr(exc, 'response', None)
    if isinstance(response, dict):
        error = response.get('Error', {})
        status = response.get('ResponseMetadata', {}).get('HTTPStatusCode')
        message = error.get('Message', '') if with_message else ''
        return scrub(f"{error.get('Code', 'error')} (HTTP {status})"
                     + (f': {message}' if message else ''))
    return type(exc).__name__


def is_not_found(exc):
    response = getattr(exc, 'response', None)
    if not isinstance(response, dict):
        return False
    return str(response.get('Error', {}).get('Code', '')) in ('404', 'NoSuchKey', 'NotFound')
