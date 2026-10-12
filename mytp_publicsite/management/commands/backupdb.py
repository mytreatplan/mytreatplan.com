"""Back up the database off-region (story 1.14).

    python manage.py backupdb

Run nightly by the `backupdb` job in deploy/jobs.conf (through `manage.py runjob`, so a
failed or missed run raises a Sentry alert). It:

1. checks the BACKUP_* settings, the tools (`pg_dump`, `age`), the temp directory, and
   that `pg_dump` is not older (major version) than the database server;
2. runs `pg_dump --format=custom --no-owner --no-privileges` against
   DATABASES['default'] (connection details and password through libpq environment
   variables only, never arguments) and pipes it straight into `age -r
   <BACKUP_AGE_RECIPIENT>`, so only ciphertext is ever written to disk;
3. uploads the encrypted file to BACKUP_S3_BUCKET as
   `<BACKUP_PREFIX>mytreatplan-<UTC yyyymmddThhmmssZ>.dump.age`, then checks the object
   exists with the expected size;
4. removes the temp file whatever happens.

It never deletes objects: the bucket's lifecycle rule expires old dumps (see
deploy/README.md, "Backups and restore"). Error messages never contain the database
password or the S3 keys, and the original exceptions are not chained onto them, so
Sentry gets only the scrubbed text.
"""
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

REQUIRED_SETTINGS = (
    'BACKUP_S3_ENDPOINT',
    'BACKUP_S3_REGION',
    'BACKUP_S3_BUCKET',
    'BACKUP_S3_ACCESS_KEY',
    'BACKUP_S3_SECRET_KEY',
    'BACKUP_PREFIX',
    'BACKUP_AGE_RECIPIENT',
)
OBJECT_BASENAME = 'mytreatplan'
OBJECT_SUFFIX = '.dump.age'
TEMP_PREFIX = f'{OBJECT_BASENAME}-'
TEMP_SUFFIX = f'{OBJECT_SUFFIX}.part'
PG_DUMP_ARGS = ('--format=custom', '--no-owner', '--no-privileges')
_AGE_RECIPIENT_RE = re.compile(r'^age1[0-9a-z]{20,}$')
_VERSION_RE = re.compile(r'\(PostgreSQL\)\s+(\d+)')
REDACTED = '[redacted]'
# Part files older than this are left over from a killed run (the job's max runtime
# is 60 minutes; see runjob and mytp-job@.service).
STALE_PART_SECONDS = 2 * 60 * 60


def object_key(prefix, when=None):
    when = when or datetime.now(timezone.utc)
    return f'{prefix}{OBJECT_BASENAME}-{when.strftime("%Y%m%dT%H%M%SZ")}{OBJECT_SUFFIX}'


def make_s3_client():
    """The S3-compatible client for the backup bucket (OVHcloud Object Storage)."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        's3',
        endpoint_url=settings.BACKUP_S3_ENDPOINT,
        region_name=settings.BACKUP_S3_REGION,
        aws_access_key_id=settings.BACKUP_S3_ACCESS_KEY,
        aws_secret_access_key=settings.BACKUP_S3_SECRET_KEY,
        config=Config(
            signature_version='s3v4',
            # Newer botocore adds CRC checksums by default; S3-compatible stores don't
            # all accept them. Only send/validate them where the API requires it.
            request_checksum_calculation='when_required',
            response_checksum_validation='when_required',
            retries={'max_attempts': 5, 'mode': 'standard'},
            connect_timeout=30,
            read_timeout=120,
        ),
    )


def server_major_version():
    """The database server's major version (e.g. 17)."""
    from django.db import connection

    connection.ensure_connection()
    return connection.pg_version // 10000


def _secrets():
    db = settings.DATABASES['default']
    values = [db.get('PASSWORD'), settings.BACKUP_S3_SECRET_KEY, settings.BACKUP_S3_ACCESS_KEY]
    return [v for v in values if v]


def scrub(text):
    """Remove the database password and the S3 keys from free text."""
    text = str(text)
    for secret in _secrets():
        text = text.replace(secret, REDACTED)
    return text


def _tail(fh, lines=5):
    fh.seek(0)
    text = fh.read().decode('utf-8', 'replace').strip()
    return scrub(' | '.join(text.splitlines()[-lines:])) or 'no output'


def _s3_error(exc):
    """A short description of an S3 failure, without credentials."""
    response = getattr(exc, 'response', None)
    if isinstance(response, dict):
        error = response.get('Error') or {}
        code = error.get('Code') or response.get('ResponseMetadata', {}).get('HTTPStatusCode')
        return f'{type(exc).__name__} {code}: {scrub(error.get("Message") or "")}'.rstrip(': ')
    return f'{type(exc).__name__}: {scrub(exc)}'


def pg_environment():
    """libpq environment for pg_dump from DATABASES['default'] (TLS options included).

    Any PG* variables already in the environment are dropped, so the connection is
    exactly the app's.
    """
    db = settings.DATABASES['default']
    options = db.get('OPTIONS') or {}
    env = {k: v for k, v in os.environ.items() if not k.startswith('PG')}
    values = {
        'PGHOST': db.get('HOST'),
        'PGPORT': db.get('PORT'),
        'PGUSER': db.get('USER'),
        'PGDATABASE': db.get('NAME'),
        'PGPASSWORD': db.get('PASSWORD'),
        'PGSSLMODE': options.get('sslmode'),
        'PGSSLROOTCERT': options.get('sslrootcert'),
    }
    env.update({k: str(v) for k, v in values.items() if v not in (None, '')})
    env['PGCONNECT_TIMEOUT'] = '30'
    env['PGAPPNAME'] = 'mytp-backupdb'
    return env


class Command(BaseCommand):
    help = 'Dump the database, encrypt it with age and upload it to the off-region bucket.'

    def handle(self, **options):
        self.check_settings()
        pg_dump, age = self.check_tools()
        tmp_dir = self.check_tmp_dir()
        self.check_versions(pg_dump)

        previous = None
        fd, tmp_name = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX, dir=tmp_dir)
        tmp_path = Path(tmp_name)
        try:
            if threading.current_thread() is threading.main_thread():
                # systemd stops a run that exceeds its timeout with SIGTERM; turn that
                # into an exception so the temp file is still removed.
                previous = signal.signal(signal.SIGTERM, self._on_sigterm)
            with open(fd, 'wb') as out:
                self.dump_encrypted(pg_dump, age, out, tmp_dir)
            size = tmp_path.stat().st_size
            key = object_key(settings.BACKUP_PREFIX)
            self.upload(tmp_path, key, size)
        finally:
            tmp_path.unlink(missing_ok=True)
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)
        self.stdout.write(f'backupdb: uploaded {key} ({size} bytes) to {settings.BACKUP_S3_BUCKET}')

    @staticmethod
    def _on_sigterm(signum, frame):
        raise CommandError('backupdb: interrupted (SIGTERM)')

    # --- checks ------------------------------------------------------------------------

    def check_settings(self):
        for key in REQUIRED_SETTINGS:
            if not str(getattr(settings, key, '') or '').strip():
                raise CommandError(f'backupdb: setting {key} is missing (shared/settings.env)')
        if not _AGE_RECIPIENT_RE.match(settings.BACKUP_AGE_RECIPIENT.strip()):
            raise CommandError('backupdb: BACKUP_AGE_RECIPIENT must be an age public key (age1...)')
        if settings.BACKUP_PREFIX.startswith('/'):
            raise CommandError('backupdb: BACKUP_PREFIX must not start with "/"')
        if not settings.BACKUP_PREFIX.endswith('/'):
            # The bucket lifecycle rule matches e.g. `staging/`; without the slash the
            # dumps would never expire.
            raise CommandError('backupdb: BACKUP_PREFIX must end with "/" (e.g. staging/)')
        if 'postgresql' not in settings.DATABASES['default'].get('ENGINE', ''):
            raise CommandError('backupdb: the default database is not PostgreSQL')

    def check_tools(self):
        found = []
        for tool, package in (('pg_dump', 'postgresql-client'), ('age', 'age')):
            path = shutil.which(tool)
            if not path:
                raise CommandError(
                    f'backupdb: {tool} not found; install the {package} package (provision.sh)')
            found.append(path)
        return found

    def check_tmp_dir(self):
        path = Path(settings.BACKUP_TMP_DIR)
        try:
            info = path.stat()
        except OSError:
            raise CommandError(
                f'backupdb: temp directory {path} is missing (provision.sh creates it)') from None
        if not stat.S_ISDIR(info.st_mode):
            raise CommandError(f'backupdb: {path} is not a directory')
        if info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise CommandError(
                f'backupdb: temp directory {path} must be mode 0700 and owned by the app user')
        # A run killed outright (SIGKILL, power loss) can leave an encrypted part file.
        # Only files older than any run can last, so a run in progress keeps its file.
        cutoff = time.time() - STALE_PART_SECONDS
        for stale in path.glob(f'{TEMP_PREFIX}*{TEMP_SUFFIX}'):
            try:
                if stale.stat().st_mtime < cutoff:
                    stale.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
        return path

    def check_versions(self, pg_dump):
        try:
            result = subprocess.run([pg_dump, '--version'], capture_output=True, text=True,
                                    timeout=30, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CommandError(f'backupdb: could not run pg_dump --version: {scrub(exc)}') from None
        match = _VERSION_RE.search(result.stdout)
        if result.returncode != 0 or not match:
            raise CommandError(f'backupdb: unexpected pg_dump --version output: {result.stdout.strip()!r}')
        client = int(match.group(1))
        try:
            server = server_major_version()
        except Exception as exc:
            raise CommandError(
                f'backupdb: could not connect to the database: {type(exc).__name__}: {scrub(exc)}'
            ) from None
        if client < server:
            raise CommandError(
                f'backupdb: pg_dump {client} is older than the database server ({server}); '
                f'install postgresql-client {server} or newer')
        return client, server

    # --- dump and encrypt --------------------------------------------------------------

    def dump_encrypted(self, pg_dump, age, out, tmp_dir):
        """pg_dump | age -r <recipient> > out. Nothing unencrypted touches the disk."""
        recipient = settings.BACKUP_AGE_RECIPIENT.strip()
        with tempfile.TemporaryFile(dir=tmp_dir) as dump_err, \
                tempfile.TemporaryFile(dir=tmp_dir) as age_err:
            dump = subprocess.Popen(
                [pg_dump, *PG_DUMP_ARGS], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=dump_err, env=pg_environment())
            try:
                encrypt = subprocess.Popen(
                    [age, '--encrypt', '--recipient', recipient],
                    stdin=dump.stdout, stdout=out, stderr=age_err)
            except BaseException:
                dump.kill()
                dump.wait()
                raise
            dump.stdout.close()
            try:
                age_rc = encrypt.wait()
                dump_rc = dump.wait()
            finally:
                for proc in (dump, encrypt):
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()
            # If age fails, pg_dump dies of a broken pipe; report age's error then.
            if age_rc != 0:
                raise CommandError(f'backupdb: age failed (exit {age_rc}): {_tail(age_err)}')
            if dump_rc != 0:
                raise CommandError(f'backupdb: pg_dump failed (exit {dump_rc}): {_tail(dump_err)}')

    # --- upload ------------------------------------------------------------------------

    def upload(self, path, key, size):
        bucket = settings.BACKUP_S3_BUCKET
        try:
            client = make_s3_client()
            client.upload_file(str(path), bucket, key,
                               ExtraArgs={'ContentType': 'application/octet-stream'})
        except Exception as exc:
            raise CommandError(f'backupdb: upload of {key} failed: {_s3_error(exc)}') from None
        try:
            head = client.head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            raise CommandError(
                f'backupdb: uploaded object {key} not found: {_s3_error(exc)}') from None
        remote = head.get('ContentLength')
        if remote != size:
            raise CommandError(
                f'backupdb: uploaded object {key} has {remote} bytes, expected {size}')
