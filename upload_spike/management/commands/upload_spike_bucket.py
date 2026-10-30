"""Set up and inspect the upload spike's bucket (epic-platform entry 2).

    python manage.py upload_spike_bucket --apply     # set CORS, lifecycle, encryption
    python manage.py upload_spike_bucket --inspect   # print them and the open uploads
    python manage.py upload_spike_bucket             # same as --inspect

--apply sets, one by one, so a refusal by OVHcloud names exactly the setting it refused:

1. CORS: the origins in UPLOAD_SPIKE_CORS_ORIGINS (default SITE_URL); methods GET, PUT,
   POST, DELETE; the Content-Type request header; ETag exposed (Uppy needs it).
2. Lifecycle: abort incomplete multipart uploads 1 day after they start.
3. Default encryption: SSE-S3 (AES256).

Then it reads each back. It exits with an error naming every refused setting. Messages
never contain the bucket keys. Uses the UPLOAD_SPIKE_S3_* settings, never the backup ones.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from upload_spike import s3

CORS_METHODS = ['GET', 'PUT', 'POST', 'DELETE']
CORS_HEADERS = ['Content-Type']
CORS_EXPOSE = ['ETag']
CORS_MAX_AGE = 3000
LIFECYCLE_RULE_ID = 'abort-incomplete-multipart-uploads'
ABORT_AFTER_DAYS = 1
SSE_ALGORITHM = 'AES256'


def cors_origins():
    return [o.strip().rstrip('/') for o in settings.UPLOAD_SPIKE_CORS_ORIGINS if o.strip()] \
        or [settings.SITE_URL]


def cors_configuration():
    return {'CORSRules': [{
        'AllowedOrigins': cors_origins(),
        'AllowedMethods': CORS_METHODS,
        'AllowedHeaders': CORS_HEADERS,
        'ExposeHeaders': CORS_EXPOSE,
        'MaxAgeSeconds': CORS_MAX_AGE,
    }]}


def lifecycle_configuration():
    return {'Rules': [{
        'ID': LIFECYCLE_RULE_ID,
        'Filter': {'Prefix': ''},
        'Status': 'Enabled',
        'AbortIncompleteMultipartUpload': {'DaysAfterInitiation': ABORT_AFTER_DAYS},
    }]}


def encryption_configuration():
    return {'Rules': [{
        'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': SSE_ALGORITHM},
    }]}


def _scrub(text):
    text = str(text)
    for secret in (settings.UPLOAD_SPIKE_S3_SECRET_KEY, settings.UPLOAD_SPIKE_S3_ACCESS_KEY):
        if secret:
            text = text.replace(secret, '[redacted]')
    return text


def describe_error(exc):
    """A short, credential-free description of an S3 failure."""
    response = getattr(exc, 'response', None)
    if isinstance(response, dict):
        error = response.get('Error', {})
        status = response.get('ResponseMetadata', {}).get('HTTPStatusCode')
        code = error.get('Code', 'error')
        message = error.get('Message', '')
        return _scrub(f'{code} (HTTP {status})' + (f': {message}' if message else ''))
    return _scrub(f'{type(exc).__name__}: {exc}')


class Command(BaseCommand):
    help = "Set (--apply) and print (--inspect) the upload spike bucket's CORS, lifecycle and encryption."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Set CORS, the abort-incomplete lifecycle rule and default encryption.')
        parser.add_argument('--inspect', action='store_true',
                            help='Print the settings and the incomplete multipart uploads (the default).')

    def handle(self, *args, **options):
        missing = s3.missing_setting()
        if missing:
            raise CommandError(f'{missing} is not set (see deploy/staging.env.example).')
        client = s3.make_client()
        bucket = settings.UPLOAD_SPIKE_S3_BUCKET
        self.stdout.write(f'Bucket {bucket} at {settings.UPLOAD_SPIKE_S3_ENDPOINT}')

        refused = []
        if options['apply']:
            refused = self.apply(client, bucket)
        # Always: on its own (--inspect or no flag), or to read back what --apply set.
        self.inspect(client, bucket)
        if refused:
            raise CommandError('OVHcloud refused: ' + ', '.join(refused))

    # --- apply -----------------------------------------------------------------------

    def apply(self, client, bucket):
        steps = [
            ('CORS', lambda: client.put_bucket_cors(
                Bucket=bucket, CORSConfiguration=cors_configuration())),
            ('lifecycle', lambda: client.put_bucket_lifecycle_configuration(
                Bucket=bucket, LifecycleConfiguration=lifecycle_configuration())),
            ('encryption', lambda: client.put_bucket_encryption(
                Bucket=bucket, ServerSideEncryptionConfiguration=encryption_configuration())),
        ]
        refused = []
        for name, call in steps:
            try:
                call()
            except Exception as exc:  # noqa: BLE001 - every failure is reported by name
                refused.append(name)
                self.stdout.write(self.style.ERROR(f'set {name}: REFUSED {describe_error(exc)}'))
            else:
                self.stdout.write(self.style.SUCCESS(f'set {name}: ok'))
        return refused

    # --- inspect ---------------------------------------------------------------------

    def _read(self, name, call, show):
        try:
            result = call()
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(f'{name}: {describe_error(exc)}')
            return
        show(result)

    def inspect(self, client, bucket):
        self._read('CORS', lambda: client.get_bucket_cors(Bucket=bucket), self._show_cors)
        self._read('lifecycle', lambda: client.get_bucket_lifecycle_configuration(Bucket=bucket),
                   self._show_lifecycle)
        self._read('encryption', lambda: client.get_bucket_encryption(Bucket=bucket),
                   self._show_encryption)
        self._read('incomplete uploads', lambda: client.list_multipart_uploads(Bucket=bucket),
                   self._show_uploads)

    def _show_cors(self, result):
        rules = result.get('CORSRules', [])
        self.stdout.write(f'CORS: {len(rules)} rule(s)')
        for rule in rules:
            self.stdout.write(
                f"  origins {rule.get('AllowedOrigins', [])} methods {rule.get('AllowedMethods', [])}"
                f" headers {rule.get('AllowedHeaders', [])} expose {rule.get('ExposeHeaders', [])}"
                f" max-age {rule.get('MaxAgeSeconds')}")

    def _show_lifecycle(self, result):
        rules = result.get('Rules', [])
        self.stdout.write(f'lifecycle: {len(rules)} rule(s)')
        for rule in rules:
            abort = rule.get('AbortIncompleteMultipartUpload', {})
            self.stdout.write(
                f"  {rule.get('ID', '(no id)')} {rule.get('Status')} filter {rule.get('Filter', rule.get('Prefix'))}"
                f" abort-incomplete-after {abort.get('DaysAfterInitiation', '-')} day(s)")

    def _show_encryption(self, result):
        rules = result.get('ServerSideEncryptionConfiguration', {}).get('Rules', [])
        algorithms = [r.get('ApplyServerSideEncryptionByDefault', {}).get('SSEAlgorithm')
                      for r in rules]
        self.stdout.write(f'encryption: {", ".join(a or "?" for a in algorithms) or "none"}')

    def _show_uploads(self, result):
        uploads = result.get('Uploads', [])
        self.stdout.write(f'incomplete uploads: {len(uploads)}'
                          + (' (more not listed)' if result.get('IsTruncated') else ''))
        for upload in uploads:
            initiated = upload.get('Initiated')
            when = initiated.isoformat() if hasattr(initiated, 'isoformat') else initiated
            self.stdout.write(f"  {upload.get('Key')} started {when} uploadId {upload.get('UploadId')}")
