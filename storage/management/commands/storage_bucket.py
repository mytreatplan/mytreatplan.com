"""Set up and inspect the region's file bucket (AD-9; deploy/README.md "Object storage").

    python manage.py storage_bucket --apply     # set CORS, lifecycle, encryption
    python manage.py storage_bucket --inspect   # print them and the open uploads
    python manage.py storage_bucket             # same as --inspect

Run --apply with the bucket **owner's** keys in STORAGE_ACCESS_KEY / STORAGE_SECRET_KEY
in the environment for that one command (they override settings.env; only the owner may
set a lifecycle rule). The site's own S3 user never gets those rights.

--apply sets, one by one, so a refusal by OVHcloud names exactly the setting it refused:

1. CORS: the origins in STORAGE_CORS_ORIGINS (default SITE_URL); methods GET, PUT, POST,
   DELETE; the Content-Type request header; ETag exposed (Uppy needs it).
2. Lifecycle: abort incomplete multipart uploads STORAGE_ABORT_INCOMPLETE_DAYS (default 7)
   days after they start.
3. Default encryption: SSE-S3 (AES256).

    python manage.py storage_bucket --archive --apply    # the account archive bucket
    python manage.py storage_bucket --archive --inspect

--archive works on the ARCHIVE_BUCKET (9.12; deploy/README.md, "Account archive") with the
archive bucket owner's keys in ARCHIVE_ACCESS_KEY / ARCHIVE_SECRET_KEY: --apply sets a
lifecycle Expiration of ceil(ARCHIVE_RETENTION_YEARS x 365.25) + 1 days (plus noncurrent-version
expiration after 1 day and removal of expired delete markers: the bucket is versioned) and SSE-S3;
--inspect also prints the object-lock configuration (set when the bucket was created).

Then it reads each back. It exits with an error naming every refused setting. Messages
never contain the bucket keys. Uses the STORAGE_* settings, never the backup ones.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from storage import s3

CORS_METHODS = ['GET', 'PUT', 'POST', 'DELETE']
CORS_HEADERS = ['Content-Type']
CORS_EXPOSE = ['ETag']
CORS_MAX_AGE = 3000
LIFECYCLE_RULE_ID = 'abort-incomplete-multipart-uploads'
SSE_ALGORITHM = 'AES256'


def cors_origins():
    return [o.strip().rstrip('/') for o in settings.STORAGE_CORS_ORIGINS if o.strip()] \
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
        'AbortIncompleteMultipartUpload': {
            'DaysAfterInitiation': settings.STORAGE_ABORT_INCOMPLETE_DAYS},
    }]}


ARCHIVE_LIFECYCLE_RULE_ID = 'expire-account-archives'
ARCHIVE_MARKER_RULE_ID = 'remove-expired-delete-markers'


def archive_lifecycle_configuration(days):
    """An Object Lock bucket is versioned, so the Expiration `days` days after the write
    (the retention plus one day: the lock ends first) only adds a delete marker. Hence also
    NoncurrentVersionExpiration (the locked version is removed the day after it becomes
    noncurrent, its lock having ended) and a rule that removes the expired delete markers."""
    return {'Rules': [
        {'ID': ARCHIVE_LIFECYCLE_RULE_ID,
         'Filter': {'Prefix': ''},
         'Status': 'Enabled',
         'Expiration': {'Days': days},
         'NoncurrentVersionExpiration': {'NoncurrentDays': 1}},
        {'ID': ARCHIVE_MARKER_RULE_ID,
         'Filter': {'Prefix': ''},
         'Status': 'Enabled',
         'Expiration': {'ExpiredObjectDeleteMarker': True}},
    ]}


def encryption_configuration():
    return {'Rules': [{
        'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': SSE_ALGORITHM},
    }]}


def describe_error(exc):
    return s3.describe_error(exc, with_message=True)


class Command(BaseCommand):
    help = "Set (--apply) and print (--inspect) the file bucket's CORS, lifecycle and encryption."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Set CORS, the abort-incomplete lifecycle rule and default '
                                 'encryption (with the bucket owner\'s keys).')
        parser.add_argument('--archive', action='store_true',
                            help='Work on the account archive bucket (ARCHIVE_*): --apply sets '
                                 'its lifecycle Expiration and encryption.')
        parser.add_argument('--inspect', action='store_true',
                            help='Print the settings and the incomplete multipart uploads (the default).')

    def handle(self, *args, **options):
        if options['archive']:
            return self.handle_archive(options)
        missing = s3.missing_setting()
        if missing:
            raise CommandError(f'{missing} is not set (deploy/README.md, "Object storage").')
        client = s3.make_client()
        bucket = settings.STORAGE_BUCKET
        self.stdout.write(f'Bucket {bucket} at {settings.STORAGE_ENDPOINT}')

        refused = []
        with s3.out_of_sentry():
            if options['apply']:
                refused = self.apply(client, bucket)
            # Always: on its own (--inspect or no flag), or to read back what --apply set.
            self.inspect(client, bucket)
        if refused:
            raise CommandError('OVHcloud refused: ' + ', '.join(refused))

    # --- the account archive bucket --------------------------------------------------

    def handle_archive(self, options):
        missing = s3.missing_archive_setting()
        if missing:
            raise CommandError(f'{missing} is not set (deploy/README.md, "Account archive").')
        from archive import service as archive_service

        client = s3.make_archive_client()
        bucket = settings.ARCHIVE_BUCKET
        self.stdout.write(f'Archive bucket {bucket} at {settings.STORAGE_ENDPOINT}')
        refused = []
        with s3.out_of_sentry():
            if options['apply']:
                steps = [
                    ('lifecycle', lambda: client.put_bucket_lifecycle_configuration(
                        Bucket=bucket, LifecycleConfiguration=archive_lifecycle_configuration(
                            archive_service.expiration_days()))),
                    ('encryption', lambda: client.put_bucket_encryption(
                        Bucket=bucket,
                        ServerSideEncryptionConfiguration=encryption_configuration())),
                ]
                for name, call in steps:
                    try:
                        call()
                    except Exception as exc:  # noqa: BLE001
                        refused.append(name)
                        self.stdout.write(self.style.ERROR(
                            f'set {name}: REFUSED {describe_error(exc)}'))
                    else:
                        self.stdout.write(self.style.SUCCESS(f'set {name}: ok'))
            self._read('lifecycle', lambda: client.get_bucket_lifecycle_configuration(Bucket=bucket),
                       self._show_archive_lifecycle)
            self._read('encryption', lambda: client.get_bucket_encryption(Bucket=bucket),
                       self._show_encryption)
            self._read('object lock', lambda: client.get_object_lock_configuration(Bucket=bucket),
                       self._show_object_lock)
        if refused:
            raise CommandError('OVHcloud refused: ' + ', '.join(refused))

    def _show_archive_lifecycle(self, result):
        rules = result.get('Rules', [])
        self.stdout.write(f'lifecycle: {len(rules)} rule(s)')
        for rule in rules:
            expiration = rule.get('Expiration', {})
            self.stdout.write(
                f"  {rule.get('ID', '(no id)')} {rule.get('Status')}"
                f" expiration {expiration.get('Days', '-')} day(s)"
                f" noncurrent {rule.get('NoncurrentVersionExpiration', {}).get('NoncurrentDays', '-')}"
                f" day(s)" + (' delete-markers' if expiration.get('ExpiredObjectDeleteMarker') else ''))

    def _show_object_lock(self, result):
        config = result.get('ObjectLockConfiguration', {})
        default = config.get('Rule', {}).get('DefaultRetention', {})
        self.stdout.write(f"object lock: {config.get('ObjectLockEnabled', 'none')}"
                          + (f" default {default.get('Mode')} {default.get('Days') or default.get('Years')}"
                             if default else ''))

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
            # The key is a server-made UUID key (no file name); the upload id is omitted.
            self.stdout.write(f"  {upload.get('Key')} started {when}")
