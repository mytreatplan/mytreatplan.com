"""The spike bucket's S3 client and the signing of the browser's S3 requests.

The browser (Uppy `@uppy/aws-s3` with `signRequest`) asks for one S3 request at a time:
`{method, key, uploadId?, partNumber?, contentType?}`. `presign` maps it to exactly one
S3 operation and returns a presigned URL for it, signed here with the bucket keys from
settings.env (the browser never sees them):

    PUT    no uploadId          -> PutObject              (new key)
    POST   no uploadId          -> CreateMultipartUpload  (new key)
    PUT    uploadId, partNumber -> UploadPart
    GET    uploadId             -> ListParts
    POST   uploadId             -> CompleteMultipartUpload
    DELETE uploadId             -> AbortMultipartUpload

Anything else is refused (`BadSignRequest`). New objects get a server-made key,
`spike/<random UUID>` (AD-5: never a file or patient name); the browser's own key is
ignored there. The later requests must name such a key.
"""
import re
import uuid

from django.conf import settings

KEY_PREFIX = 'spike/'
KEY_RE = re.compile(r'^spike/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
# Long enough for a slow part (8 MiB at ~10 kB/s); each request is signed just before it is sent.
SIGNED_URL_EXPIRES = 15 * 60
# Download links shown on the page (AD-9: presigned GETs valid at most 5 minutes).
DOWNLOAD_URL_EXPIRES = 5 * 60
MAX_PART_NUMBER = 10_000
MAX_UPLOAD_ID_LENGTH = 1024
MAX_CONTENT_TYPE_LENGTH = 255
_CONTENT_TYPE_RE = re.compile(r'^[\w.+-]+/[\w.+-]+(;[ \w.+=-]*)*$')

REQUIRED_SETTINGS = (
    'UPLOAD_SPIKE_S3_ENDPOINT',
    'UPLOAD_SPIKE_S3_REGION',
    'UPLOAD_SPIKE_S3_BUCKET',
    'UPLOAD_SPIKE_S3_ACCESS_KEY',
    'UPLOAD_SPIKE_S3_SECRET_KEY',
)


class BadSignRequest(ValueError):
    """The browser asked for something that is not one of the allowed S3 requests."""


def missing_setting():
    """The first empty UPLOAD_SPIKE_S3_* setting, or None when all are set."""
    for name in REQUIRED_SETTINGS:
        if not getattr(settings, name, ''):
            return name
    return None


def make_client():
    """The S3-compatible client for the spike bucket (OVHcloud Object Storage)."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        's3',
        endpoint_url=settings.UPLOAD_SPIKE_S3_ENDPOINT,
        region_name=settings.UPLOAD_SPIKE_S3_REGION,
        aws_access_key_id=settings.UPLOAD_SPIKE_S3_ACCESS_KEY,
        aws_secret_access_key=settings.UPLOAD_SPIKE_S3_SECRET_KEY,
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


def new_key():
    return f'{KEY_PREFIX}{uuid.uuid4()}'


def is_spike_key(key):
    return isinstance(key, str) and bool(KEY_RE.match(key))


def _key(request):
    key = request.get('key')
    if not is_spike_key(key):
        raise BadSignRequest('key must be a spike/<uuid> key made by this endpoint')
    return key


def _upload_id(request):
    upload_id = request.get('uploadId')
    if not isinstance(upload_id, str) or not upload_id.strip():
        raise BadSignRequest('uploadId is required')
    if len(upload_id) > MAX_UPLOAD_ID_LENGTH or not upload_id.isprintable() or ' ' in upload_id:
        raise BadSignRequest('uploadId is malformed')
    return upload_id


def _part_number(request):
    number = request.get('partNumber')
    # bool is an int in Python; a JSON true is not a part number.
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= MAX_PART_NUMBER:
        raise BadSignRequest(f'partNumber must be an integer from 1 to {MAX_PART_NUMBER}')
    return number


def _content_type(request):
    content_type = request.get('contentType')
    if content_type in (None, ''):
        return None
    if (not isinstance(content_type, str) or len(content_type) > MAX_CONTENT_TYPE_LENGTH
            or not _CONTENT_TYPE_RE.match(content_type)):
        raise BadSignRequest('contentType is malformed')
    return content_type


def operation_for(request):
    """(boto3 operation name, its params without Bucket, the key to return or None,
    headers the URL was signed with) for one browser request; raises BadSignRequest."""
    if not isinstance(request, dict):
        raise BadSignRequest('the body must be a JSON object')
    method = request.get('method')
    has_upload = request.get('uploadId') not in (None, '')

    if method == 'PUT' and not has_upload:
        key = new_key()
        params = {'Key': key}
        headers = {}
        content_type = _content_type(request)
        if content_type:
            params['ContentType'] = content_type
            headers['Content-Type'] = content_type
        return 'put_object', params, key, headers
    if method == 'POST' and not has_upload:
        key = new_key()
        return 'create_multipart_upload', {'Key': key}, key, {}
    if method == 'PUT':
        return ('upload_part', {'Key': _key(request), 'UploadId': _upload_id(request),
                                'PartNumber': _part_number(request)}, None, {})
    if method == 'GET' and has_upload:
        return 'list_parts', {'Key': _key(request), 'UploadId': _upload_id(request)}, None, {}
    if method == 'POST':
        return ('complete_multipart_upload',
                {'Key': _key(request), 'UploadId': _upload_id(request)}, None, {})
    if method == 'DELETE' and has_upload:
        return ('abort_multipart_upload',
                {'Key': _key(request), 'UploadId': _upload_id(request)}, None, {})
    if method in ('GET', 'DELETE'):
        raise BadSignRequest(f'{method} needs an uploadId')
    raise BadSignRequest('method must be PUT, POST, GET or DELETE')


HTTP_METHODS = {
    'put_object': 'PUT',
    'create_multipart_upload': 'POST',
    'upload_part': 'PUT',
    'list_parts': 'GET',
    'complete_multipart_upload': 'POST',
    'abort_multipart_upload': 'DELETE',
}


def presign_operation(operation, client=None):
    """The `PresignedResponse` for Uppy for an `operation_for` result: `{url}`, plus `key`
    for a new object and `headers` when the URL was signed with some."""
    name, params, key, headers = operation
    client = client or make_client()
    url = client.generate_presigned_url(
        name,
        Params={'Bucket': settings.UPLOAD_SPIKE_S3_BUCKET, **params},
        ExpiresIn=SIGNED_URL_EXPIRES,
        HttpMethod=HTTP_METHODS[name],
    )
    response = {'url': url}
    if key:
        response['key'] = key
    if headers:
        response['headers'] = headers
    return response


def presign(request, client=None):
    """Validate one browser request and presign it; raises BadSignRequest."""
    return presign_operation(operation_for(request), client)


def describe(key, client=None):
    """Key, size and ETag of a stored spike object (HeadObject, server-side), plus a
    5-minute download link for the checksum check."""
    if not is_spike_key(key):
        raise BadSignRequest('key must be a spike/<uuid> key made by this endpoint')
    client = client or make_client()
    bucket = settings.UPLOAD_SPIKE_S3_BUCKET
    head = client.head_object(Bucket=bucket, Key=key)
    return {
        'key': key,
        'size': head.get('ContentLength'),
        'etag': (head.get('ETag') or '').strip('"'),
        'serverSideEncryption': head.get('ServerSideEncryption') or '',
        'downloadUrl': client.generate_presigned_url(
            'get_object', Params={'Bucket': bucket, 'Key': key},
            ExpiresIn=DOWNLOAD_URL_EXPIRES, HttpMethod='GET'),
    }
