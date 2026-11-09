"""Story 1.14: off-region database backups. `manage.py backupdb` with pg_dump, age and
S3 replaced by fakes (fake `pg_dump` and `age` executables first on PATH, a fake S3
client), the restore script's guards with fake `psql`, `pg_restore` and `age`, and
the deploy files. No database is needed."""
import json
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest
import sentry_sdk
from botocore.exceptions import ClientError
from django.conf import settings as django_settings
from django.core.management import CommandError, call_command

from conftest import _assert_no_secrets, _runjob_with
from mytp_core import jobs
from mytp_core.management.commands import backupdb
from mytp_publicsite.tests.test_monitoring import _FakeSentry

REPO = Path(django_settings.BASE_DIR)
DEPLOY = REPO / 'deploy'
RESTORE = DEPLOY / 'restore-db.sh'

DB_PASSWORD = 'db-pass-Zq81xT'
S3_ACCESS = 'AKIAFAKEACCESS0042'
S3_SECRET = 'sEcReT/Key+9fakefakefake'
RECIPIENT = 'age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p'
SECRETS = (DB_PASSWORD, S3_ACCESS, S3_SECRET)

needs_sh = pytest.mark.skipif(shutil.which('bash') is None or shutil.which('sh') is None,
                              reason='bash not available')

FAKE_PG_DUMP = r'''#!/bin/sh
if [ "$1" = "--version" ]; then
    echo "pg_dump (PostgreSQL) ${FAKE_PG_DUMP_VERSION:-17.6} (Debian 17.6-0+deb13u1)"
    exit 0
fi
printf '%s\n' "$@" > "$FAKE_LOG/pg_dump.args"
env | grep '^PG' | sort > "$FAKE_LOG/pg_dump.env"
if [ -n "$FAKE_PG_DUMP_FAIL" ]; then
    echo "pg_dump: error: connection failed: password \"$PGPASSWORD\" rejected" >&2
    exit 1
fi
printf 'PGDMP plaintext dump of %s' "$PGDATABASE"
'''

# "Encrypts" by rot13 behind an age-style header, so the tests can tell ciphertext.
FAKE_AGE = r'''#!/bin/sh
printf '%s\n' "$@" > "$FAKE_LOG/age.args"
if [ -n "$FAKE_AGE_FAIL" ]; then cat >/dev/null; echo "age: error: bad recipient" >&2; exit 1; fi
printf 'age-encryption.org/v1\n'
tr 'A-Za-z' 'N-ZA-Mn-za-m'
'''


def _write_tool(directory, name, body):
    path = directory / name
    path.write_text(body)
    path.chmod(0o755)
    return path


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.fail_upload = False
        self.drop_object = False
        self.size_delta = 0

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        self.calls.append(('upload_file', Bucket, Key))
        if self.fail_upload:
            raise ClientError(
                {'Error': {'Code': 'InvalidAccessKeyId',
                           'Message': f'The access key {S3_ACCESS} does not exist'}},
                'PutObject')
        if not self.drop_object:
            self.objects[(Bucket, Key)] = Path(Filename).read_bytes()

    def head_object(self, Bucket, Key):
        self.calls.append(('head_object', Bucket, Key))
        if (Bucket, Key) not in self.objects:
            raise ClientError({'Error': {'Code': '404', 'Message': 'Not Found'}}, 'HeadObject')
        return {'ContentLength': len(self.objects[(Bucket, Key)]) + self.size_delta}

    def delete_object(self, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError('backupdb must never delete objects')

    delete_objects = delete_object


@pytest.fixture
def backup_env(tmp_path, monkeypatch, settings):
    """Settings, fake pg_dump/age on PATH, a 0700 temp dir and a fake S3 client."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    log = tmp_path / 'log'
    log.mkdir()
    _write_tool(bin_dir, 'pg_dump', FAKE_PG_DUMP)
    _write_tool(bin_dir, 'age', FAKE_AGE)
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')
    monkeypatch.setenv('FAKE_LOG', str(log))
    monkeypatch.setenv('PGSERVICE', 'somewhere-else')  # must not leak into pg_dump
    for name in ('FAKE_PG_DUMP_FAIL', 'FAKE_PG_DUMP_VERSION', 'FAKE_AGE_FAIL'):
        monkeypatch.delenv(name, raising=False)

    tmp_dir = tmp_path / 'backup-tmp'
    tmp_dir.mkdir(mode=0o700)
    tmp_dir.chmod(0o700)
    settings.BACKUP_TMP_DIR = str(tmp_dir)
    settings.BACKUP_S3_ENDPOINT = 'https://s3.sbg.io.cloud.ovh.net'
    settings.BACKUP_S3_REGION = 'sbg'
    settings.BACKUP_S3_BUCKET = 'mytp-backups-sbg'
    settings.BACKUP_S3_ACCESS_KEY = S3_ACCESS
    settings.BACKUP_S3_SECRET_KEY = S3_SECRET
    settings.BACKUP_PREFIX = 'staging/'
    settings.BACKUP_AGE_RECIPIENT = RECIPIENT
    monkeypatch.setitem(django_settings.DATABASES, 'default', {
        'ENGINE': 'django.db.backends.postgresql', 'NAME': 'mytreatplan', 'USER': 'mytp',
        'PASSWORD': DB_PASSWORD, 'HOST': 'db.gra.example', 'PORT': 20184,
        'OPTIONS': {'sslmode': 'verify-full', 'sslrootcert': '/srv/mytp-dev/shared/db-ca.pem'},
    })

    s3 = FakeS3()
    monkeypatch.setattr(backupdb, 'make_s3_client', lambda: s3)
    monkeypatch.setattr(backupdb, 'server_major_version', lambda: 17)
    return s3, log, tmp_dir


# --- backupdb: the nightly run --------------------------------------------------------

def test_backup_uploads_one_encrypted_object(backup_env, capsys):
    s3, log, tmp_dir = backup_env

    call_command('backupdb')

    [(bucket, key)] = s3.objects
    assert bucket == 'mytp-backups-sbg'
    assert re.fullmatch(r'staging/mytreatplan-\d{8}T\d{6}Z\.dump\.age', key)
    body = s3.objects[(bucket, key)]
    assert body.startswith(b'age-encryption.org/v1\n')
    assert b'PGDMP' not in body and b'plaintext' not in body  # never the plain dump
    assert ('head_object', bucket, key) in s3.calls
    assert list(tmp_dir.iterdir()) == []
    out = capsys.readouterr().out
    assert key in out
    _assert_no_secrets(SECRETS, out)


def test_backup_runs_pg_dump_with_tls_and_password_only_in_env(backup_env):
    s3, log, tmp_dir = backup_env

    call_command('backupdb')

    args = (log / 'pg_dump.args').read_text().split()
    assert args == ['--format=custom', '--no-owner', '--no-privileges']
    env = dict(line.split('=', 1) for line in (log / 'pg_dump.env').read_text().splitlines())
    assert env['PGPASSWORD'] == DB_PASSWORD
    assert env['PGHOST'] == 'db.gra.example'
    assert env['PGPORT'] == '20184'
    assert env['PGUSER'] == 'mytp'
    assert env['PGDATABASE'] == 'mytreatplan'
    assert env['PGSSLMODE'] == 'verify-full'
    assert env['PGSSLROOTCERT'] == '/srv/mytp-dev/shared/db-ca.pem'
    assert 'PGSERVICE' not in env
    age_args = (log / 'age.args').read_text().split()
    assert age_args == ['--encrypt', '--recipient', RECIPIENT]
    _assert_no_secrets(SECRETS, *args, *age_args)


@pytest.mark.parametrize('key', backupdb.REQUIRED_SETTINGS)
def test_backup_missing_setting_names_key_and_dumps_nothing(backup_env, settings, key):
    s3, log, tmp_dir = backup_env
    setattr(settings, key, '')

    with pytest.raises(CommandError, match=key) as exc:
        call_command('backupdb')

    assert not (log / 'pg_dump.args').exists()
    assert s3.calls == []
    _assert_no_secrets(SECRETS, exc.value)


def test_backup_rejects_a_non_age_recipient(backup_env, settings):
    s3, log, tmp_dir = backup_env
    settings.BACKUP_AGE_RECIPIENT = 'AGE-SECRET-KEY-1QQQ'

    with pytest.raises(CommandError, match='age public key'):
        call_command('backupdb')

    assert s3.calls == []


def test_backup_dump_failure_uploads_nothing_and_hides_password(backup_env, monkeypatch):
    s3, log, tmp_dir = backup_env
    monkeypatch.setenv('FAKE_PG_DUMP_FAIL', '1')

    with pytest.raises(CommandError, match='pg_dump failed') as exc:
        call_command('backupdb')

    assert s3.calls == []
    assert list(tmp_dir.iterdir()) == []
    assert '[redacted]' in str(exc.value)
    _assert_no_secrets(SECRETS, exc.value)
    assert 'postgres://' not in str(exc.value)


def test_backup_encryption_failure_uploads_nothing(backup_env, monkeypatch):
    s3, log, tmp_dir = backup_env
    monkeypatch.setenv('FAKE_AGE_FAIL', '1')

    with pytest.raises(CommandError, match='age failed'):
        call_command('backupdb')

    assert s3.calls == []
    assert list(tmp_dir.iterdir()) == []


def test_backup_refuses_older_pg_dump(backup_env, monkeypatch):
    s3, log, tmp_dir = backup_env
    monkeypatch.setenv('FAKE_PG_DUMP_VERSION', '16.4')
    monkeypatch.setattr(backupdb, 'server_major_version', lambda: 17)

    with pytest.raises(CommandError, match=r'pg_dump 16 is older than the database server \(17\)'):
        call_command('backupdb')

    assert not (log / 'pg_dump.args').exists()
    assert s3.calls == []


def test_backup_accepts_newer_pg_dump(backup_env, monkeypatch):
    s3, log, tmp_dir = backup_env
    monkeypatch.setenv('FAKE_PG_DUMP_VERSION', '18.0')

    call_command('backupdb')

    assert len(s3.objects) == 1


def test_backup_database_unreachable_hides_password(backup_env, monkeypatch):
    s3, log, tmp_dir = backup_env

    def unreachable():
        raise RuntimeError(f'connection failed for mytp:{DB_PASSWORD}@db.gra.example')
    monkeypatch.setattr(backupdb, 'server_major_version', unreachable)

    with pytest.raises(CommandError, match='could not connect') as exc:
        call_command('backupdb')

    _assert_no_secrets(SECRETS, exc.value)
    assert exc.value.__suppress_context__  # the original error is not chained
    assert s3.calls == []


def test_backup_upload_failure_hides_credentials_and_cleans_up(backup_env):
    s3, log, tmp_dir = backup_env
    s3.fail_upload = True

    with pytest.raises(CommandError, match='upload of .* failed: ClientError InvalidAccessKeyId') as exc:
        call_command('backupdb')

    assert list(tmp_dir.iterdir()) == []
    _assert_no_secrets(SECRETS, exc.value)
    assert exc.value.__suppress_context__


def test_backup_missing_object_after_upload_fails(backup_env):
    s3, log, tmp_dir = backup_env
    s3.drop_object = True

    with pytest.raises(CommandError, match='not found'):
        call_command('backupdb')

    assert list(tmp_dir.iterdir()) == []


def test_backup_size_mismatch_fails(backup_env):
    s3, log, tmp_dir = backup_env
    s3.size_delta = -1

    with pytest.raises(CommandError, match='expected'):
        call_command('backupdb')

    assert list(tmp_dir.iterdir()) == []


def test_backup_refuses_open_temp_dir(backup_env):
    s3, log, tmp_dir = backup_env
    tmp_dir.chmod(0o755)

    with pytest.raises(CommandError, match='0700'):
        call_command('backupdb')

    assert not (log / 'pg_dump.args').exists()


def test_backup_missing_temp_dir(backup_env, settings, tmp_path):
    settings.BACKUP_TMP_DIR = str(tmp_path / 'nope')

    with pytest.raises(CommandError, match='temp directory .* is missing'):
        call_command('backupdb')


def test_backup_removes_stale_part_files(backup_env):
    s3, log, tmp_dir = backup_env
    stale = tmp_dir / 'mytreatplan-old.dump.age.part'
    stale.write_bytes(b'age-encryption.org/v1\n')
    old = time.time() - backupdb.STALE_PART_SECONDS - 60
    os.utime(stale, (old, old))

    call_command('backupdb')

    assert list(tmp_dir.iterdir()) == []


def test_backup_keeps_part_file_of_a_run_in_progress(backup_env):
    s3, log, tmp_dir = backup_env
    running = tmp_dir / 'mytreatplan-running.dump.age.part'
    running.write_bytes(b'age-encryption.org/v1\n')

    call_command('backupdb')

    assert list(tmp_dir.iterdir()) == [running]
    assert len(s3.objects) == 1


def test_backup_prefix_must_end_with_slash(backup_env, settings):
    s3, log, tmp_dir = backup_env
    settings.BACKUP_PREFIX = 'staging'

    with pytest.raises(CommandError, match='BACKUP_PREFIX must end with'):
        call_command('backupdb')

    assert not (log / 'pg_dump.args').exists()
    assert s3.calls == []


def test_backup_missing_tool(backup_env, monkeypatch):
    real_which = shutil.which
    monkeypatch.setattr(backupdb.shutil, 'which',
                        lambda name: None if name == 'age' else real_which(name))

    with pytest.raises(CommandError, match='age not found'):
        call_command('backupdb')


def test_object_key_format():
    from datetime import datetime, timezone
    when = datetime(2026, 10, 12, 2, 30, 5, tzinfo=timezone.utc)
    assert backupdb.object_key('staging/', when) == 'staging/mytreatplan-20261012T023005Z.dump.age'


def test_s3_client_uses_settings_without_network(settings):
    settings.BACKUP_S3_ENDPOINT = 'https://s3.sbg.io.cloud.ovh.net'
    settings.BACKUP_S3_REGION = 'sbg'
    settings.BACKUP_S3_ACCESS_KEY = S3_ACCESS
    settings.BACKUP_S3_SECRET_KEY = S3_SECRET

    client = backupdb.make_s3_client()

    assert client.meta.endpoint_url == 'https://s3.sbg.io.cloud.ovh.net'
    assert client.meta.region_name == 'sbg'


def test_backupdb_never_deletes():
    source = Path(backupdb.__file__).read_text()
    assert 'delete_object' not in source and 'delete_objects' not in source


# --- Through runjob: Sentry --------------------------------------------------------------

def test_restoretest_job_weekly_and_staggered_per_site():
    """Epic-platform entry 13: Sunday night, dev and dev-ae an hour apart (one jobs.conf,
    one line per site; each site's own Sentry monitor)."""
    dev = jobs.load_jobs(jobs.release_jobs_file(), 'dev')['restoretest']
    dev_ae = jobs.load_jobs(jobs.release_jobs_file(), 'dev-ae')['restoretest']
    assert (dev.command, dev.args) == ('restoretest', ())
    assert dev.crontab == '30 21 * * 0'
    assert dev_ae.crontab == '30 22 * * 0'
    # A site without its own line (production, for now) has no restore test.
    assert 'restoretest' not in jobs.load_jobs(jobs.release_jobs_file(), 'portal-eu')


RESTORE_ENV = ('RESTORE_TEST_DATABASE_URL', 'BACKUP_AGE_RESTORE_RECIPIENT',
               'RESTORE_AGE_IDENTITY_FILE', 'DB_SSLMODE', 'DB_SSLROOTCERT')


@pytest.fixture
def reload_settings():
    """Load the settings module again under a given environment (a MonkeyPatch of its
    own), and reload it once more after the environment is restored."""
    import importlib

    import mytreatplan.settings as site_settings
    patch = pytest.MonkeyPatch()

    def load(**env):
        for name in RESTORE_ENV:
            patch.delenv(name, raising=False)
        for name, value in env.items():
            patch.setenv(name, value)
        return importlib.reload(site_settings)
    yield load
    patch.undo()
    importlib.reload(site_settings)


def test_restore_test_settings_default_to_not_set_up(reload_settings):
    loaded = reload_settings()

    assert loaded.RESTORE_TEST_DATABASE == {}
    assert loaded.BACKUP_AGE_RESTORE_RECIPIENT == ''
    assert loaded.RESTORE_AGE_IDENTITY_FILE == str(
        loaded.SITE_HOME / 'shared' / 'restore-identity.json')
    assert loaded.RESTORE_TEST_USER_TABLE == 'accounts_user'
    from django.contrib.auth import get_user_model
    assert get_user_model()._meta.db_table == loaded.RESTORE_TEST_USER_TABLE


def test_restore_test_settings_from_the_environment(reload_settings):
    """The scratch database URL is parsed like DATABASE_URL, with DB_SSLMODE and
    DB_SSLROOTCERT on top (the same managed service)."""
    loaded = reload_settings(
        RESTORE_TEST_DATABASE_URL='postgres://x_restore_test:pw-not-secret@db.example:5432/x_restore_test',
        BACKUP_AGE_RESTORE_RECIPIENT='age1' + 'q' * 58,
        RESTORE_AGE_IDENTITY_FILE='/tmp/elsewhere.json',
        DB_SSLMODE='verify-full', DB_SSLROOTCERT='/srv/ca.pem')

    db = loaded.RESTORE_TEST_DATABASE
    assert (db['NAME'], db['USER'], db['HOST'], db['PORT']) == (
        'x_restore_test', 'x_restore_test', 'db.example', 5432)
    assert 'postgresql' in db['ENGINE']
    assert db['OPTIONS'] == {'sslmode': 'verify-full', 'sslrootcert': '/srv/ca.pem'}
    assert loaded.BACKUP_AGE_RESTORE_RECIPIENT == 'age1' + 'q' * 58
    assert loaded.RESTORE_AGE_IDENTITY_FILE == '/tmp/elsewhere.json'


def test_restore_test_url_without_tls_settings(reload_settings):
    loaded = reload_settings(
        RESTORE_TEST_DATABASE_URL='postgres://x_restore_test:pw@db.example:5432/x_restore_test')

    assert loaded.RESTORE_TEST_DATABASE['OPTIONS'] == {}


def test_backupdb_job_declared_nightly():
    job = jobs.load_jobs(jobs.release_jobs_file())['backupdb']
    assert (job.command, job.args) == ('backupdb', ())
    assert job.schedule == '*-*-* 02:30:00 UTC'
    assert job.crontab == '30 2 * * *'


def test_runjob_backupdb_ok_checks_in(backup_env, monkeypatch, settings):
    settings.SENTRY_ENVIRONMENT = 'staging'
    settings.SITE = 'dev'  # the slug names the site, not the environment
    fake = _FakeSentry(active=True)
    checkins = _runjob_with(monkeypatch, fake)

    call_command('runjob', 'backupdb')

    assert [c['status'] for c in checkins] == ['in_progress', 'ok']
    assert checkins[0]['monitor_slug'] == 'mytp-dev-backupdb'
    assert fake.exceptions == 0


def test_runjob_backupdb_wrong_credentials_reaches_sentry_without_secrets(
        backup_env, monkeypatch, sentry_capture):
    s3, log, tmp_dir = backup_env
    s3.fail_upload = True
    checkins = _runjob_with(monkeypatch, sentry_sdk)

    with pytest.raises(CommandError):
        call_command('runjob', 'backupdb')
    sentry_sdk.flush()

    assert [c['status'] for c in checkins] == ['in_progress', 'error']
    [event] = [e for e in sentry_capture.events if e.get('exception')]
    values = event['exception']['values']
    assert len(values) == 1  # nothing chained from botocore
    assert 'InvalidAccessKeyId' in values[0]['value']
    for frame in values[0].get('stacktrace', {}).get('frames', []):
        assert 'vars' not in frame
    text = json.dumps([{k: v for k, v in x.items() if k != 'stacktrace'} for x in values])
    _assert_no_secrets(SECRETS, text)


# --- restore-db.sh ------------------------------------------------------------------------

FAKE_PSQL = r'''#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_LOG/psql.calls"
[ -n "$FAKE_PSQL_FAIL" ] && { echo "psql: error: database does not exist" >&2; exit 2; }
case "$*" in
    *pg_class*) echo "${FAKE_RELATIONS:-0}" ;;
    *"FROM django_migrations ORDER"*) echo "accounts.0002_last" ;;
    *django_migrations*) echo 42 ;;
    *accounts_user*) echo 7 ;;
esac
'''

FAKE_PG_RESTORE = r'''#!/bin/sh
if [ "$1" = "--version" ]; then echo "pg_restore (PostgreSQL) ${FAKE_PG_RESTORE_VERSION:-17.6}"; exit 0; fi
printf '%s\n' "$@" > "$FAKE_LOG/pg_restore.args"
cat > "$FAKE_LOG/pg_restore.stdin"
'''

FAKE_AWS = r'''#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_LOG/aws.calls"
case "$*" in
    *list-objects-v2*) printf '%s\n' "$FAKE_AWS_KEYS" | tr ' ' '\t' ;;
    *" s3 cp "*) for last; do :; done; printf 'age-encryption.org/v1\nCTQZC qhzc' > "$last" ;;
esac
'''

FAKE_AGE_DECRYPT = r'''#!/bin/sh
printf '%s\n' "$@" > "$FAKE_LOG/age.args"
eval "last=\${$#}"
tail -n +2 "$last" | tr 'A-Za-z' 'N-ZA-Mn-za-m'
'''


@pytest.fixture
def restore_env(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    log = tmp_path / 'log'
    log.mkdir()
    _write_tool(bin_dir, 'psql', FAKE_PSQL)
    _write_tool(bin_dir, 'pg_restore', FAKE_PG_RESTORE)
    _write_tool(bin_dir, 'age', FAKE_AGE_DECRYPT)
    _write_tool(bin_dir, 'aws', FAKE_AWS)
    identity = tmp_path / 'identity.txt'
    identity.write_text('AGE-SECRET-KEY-1FAKE\n')
    dump = tmp_path / 'mytreatplan-20261012T023000Z.dump.age'
    dump.write_bytes(b'age-encryption.org/v1\n' + b'CTQZC qhzc')  # rot13 of "PGDMP dump"
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PG', 'FAKE_', 'BACKUP_'))}
    env.update(PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}', FAKE_LOG=str(log), TMPDIR=str(tmp_path))

    def run(*args, **extra_env):
        return subprocess.run(['bash', str(RESTORE), *args], capture_output=True, text=True,
                              env={**env, **extra_env}, timeout=60)
    return run, log, identity, dump


@needs_sh
@pytest.mark.parametrize('db', ['mytreatplan', 'mytp', 'restore_test', 'x_restore_test;drop',
                                'Mytp_Restore_Test', 'mytp_restore_test_2'])
def test_restore_refuses_target_not_named_restore_test(restore_env, db):
    run, log, identity, dump = restore_env

    result = run('--db', db, '--file', str(dump), '--identity', str(identity))

    assert result.returncode != 0
    assert 'must end in _restore_test' in result.stderr
    assert not (log / 'psql.calls').exists()
    assert not (log / 'pg_restore.args').exists()


@needs_sh
def test_restore_refuses_non_empty_database(restore_env):
    run, log, identity, dump = restore_env

    result = run('--file', str(dump), '--identity', str(identity), FAKE_RELATIONS='12')

    assert result.returncode != 0
    assert 'is not empty (12 relations)' in result.stderr
    assert not (log / 'pg_restore.args').exists()


@needs_sh
def test_restore_refuses_missing_database(restore_env):
    run, log, identity, dump = restore_env

    result = run('--file', str(dump), '--identity', str(identity), FAKE_PSQL_FAIL='1')

    assert result.returncode != 0
    assert 'createdb mytp_restore_test' in result.stderr
    assert not (log / 'pg_restore.args').exists()


@needs_sh
def test_restore_refuses_remote_host(restore_env):
    run, log, identity, dump = restore_env

    result = run('--file', str(dump), '--identity', str(identity), PGHOST='db.gra.example')

    assert result.returncode != 0
    assert 'local PostgreSQL' in result.stderr
    assert not (log / 'psql.calls').exists()


@needs_sh
def test_restore_refuses_old_pg_restore(restore_env):
    run, log, identity, dump = restore_env

    result = run('--file', str(dump), '--identity', str(identity), FAKE_PG_RESTORE_VERSION='16.3')

    assert result.returncode != 0
    assert 'pg_restore 16 is older than 17' in result.stderr


@needs_sh
def test_restore_needs_exactly_one_source(restore_env):
    run, log, identity, dump = restore_env

    assert 'exactly one' in run('--identity', str(identity)).stderr
    assert 'exactly one' in run('--identity', str(identity), '--file', str(dump), '--latest').stderr


@needs_sh
def test_restore_dry_run_checks_and_restores_nothing(restore_env):
    run, log, identity, dump = restore_env

    result = run('--dry-run', '--file', str(dump), '--identity', str(identity))

    assert result.returncode == 0, result.stderr
    assert 'Dry run: checks passed' in result.stdout
    assert 'pg_class' in (log / 'psql.calls').read_text()
    assert not (log / 'pg_restore.args').exists()


@needs_sh
def test_restore_pipes_decrypted_dump_and_prints_counts(restore_env):
    run, log, identity, dump = restore_env

    result = run('--file', str(dump), '--identity', str(identity))

    assert result.returncode == 0, result.stderr
    assert (log / 'pg_restore.stdin').read_text() == 'PGDMP dump'
    restore_args = (log / 'pg_restore.args').read_text().split()
    assert restore_args == ['--no-owner', '--no-privileges', '--single-transaction',
                            '--exit-on-error', '-d', 'mytp_restore_test']
    assert (log / 'age.args').read_text().split()[:3] == ['--decrypt', '--identity', str(identity)]
    assert 'applied migrations: 42 (latest: accounts.0002_last)' in result.stdout
    assert 'users:              7' in result.stdout


S3_ENV = dict(BACKUP_S3_ENDPOINT='https://s3.example.invalid', BACKUP_S3_REGION='sbg',
              BACKUP_S3_BUCKET='bucket')


@needs_sh
def test_restore_latest_takes_prefix_and_basename_newest_by_name(restore_env):
    """Epic-platform entry 13: another site's dump in the same prefix is never taken."""
    run, log, identity, dump = restore_env
    keys = ' '.join([
        'staging/mytreatplan-20261101T023000Z.dump.age',
        'staging/mytreatplan-20261103T023000Z.dump.age',
        'staging/mytreatplan-20261102T023000Z.dump.age',
        'staging/mytreatplan-x-20261109T023000Z.dump.age',
        'staging/mytreatplan-20261104T023000Z.dump.age.part',
    ])

    result = run('--identity', str(identity), '--latest', BACKUP_PREFIX='staging/',
                 FAKE_AWS_KEYS=keys, **S3_ENV)

    assert result.returncode == 0, result.stderr
    calls = (log / 'aws.calls').read_text()
    assert '--prefix staging/mytreatplan-' in calls
    assert 's3://bucket/staging/mytreatplan-20261103T023000Z.dump.age' in calls


@needs_sh
def test_restore_latest_for_the_admin_site(restore_env):
    run, log, identity, dump = restore_env

    result = run('--identity', str(identity), '--latest', '--basename', 'mytp-admin',
                 '--user-table', 'staff_user', BACKUP_PREFIX='staging-admindev/',
                 FAKE_AWS_KEYS='staging-admindev/mytp-admin-20261108T024000Z.dump.age', **S3_ENV)

    assert result.returncode == 0, result.stderr
    assert '--prefix staging-admindev/mytp-admin-' in (log / 'aws.calls').read_text()
    assert 'FROM staff_user' in (log / 'psql.calls').read_text()


@needs_sh
def test_restore_latest_without_a_matching_dump(restore_env):
    run, log, identity, dump = restore_env

    result = run('--identity', str(identity), '--latest', BACKUP_PREFIX='staging/',
                 FAKE_AWS_KEYS='staging/mytp-admin-20261108T024000Z.dump.age', **S3_ENV)

    assert result.returncode != 0
    assert 'no mytreatplan dumps' in result.stderr
    assert not (log / 'pg_restore.args').exists()


@needs_sh
def test_restore_latest_dry_run_names_the_dump_and_downloads_nothing(restore_env):
    run, log, identity, dump = restore_env

    result = run('--identity', str(identity), '--latest', '--dry-run', BACKUP_PREFIX='staging-dev/',
                 FAKE_AWS_KEYS='staging-dev/mytreatplan-20261108T023000Z.dump.age', **S3_ENV)

    assert result.returncode == 0, result.stderr
    assert ('Would restore staging-dev/mytreatplan-20261108T023000Z.dump.age (the newest '
            "mytreatplan dump under 'staging-dev/')") in result.stdout
    assert ' s3 cp ' not in (log / 'aws.calls').read_text()
    assert not (log / 'pg_restore.args').exists()


@needs_sh
@pytest.mark.parametrize('prefix', ['staging dev/', 'staging/*', 'a$(x)/', 'staging[a]/'])
def test_restore_refuses_an_odd_prefix(restore_env, prefix):
    run, log, identity, dump = restore_env

    result = run('--identity', str(identity), '--latest', BACKUP_PREFIX=prefix,
                 FAKE_AWS_KEYS='x', **S3_ENV)

    assert result.returncode != 0 and 'BACKUP_PREFIX may hold only' in result.stderr
    assert not (log / 'aws.calls').exists()


@needs_sh
def test_restore_latest_takes_the_prefix_as_text(restore_env):
    """A '.' in the prefix is a dot, not "any character"."""
    run, log, identity, dump = restore_env

    result = run('--identity', str(identity), '--latest', '--dry-run', BACKUP_PREFIX='a.b/',
                 FAKE_AWS_KEYS='axb/mytreatplan-20261109T023000Z.dump.age '
                               'a.b/mytreatplan-20261101T023000Z.dump.age', **S3_ENV)

    assert result.returncode == 0, result.stderr
    assert 'Would restore a.b/mytreatplan-20261101T023000Z.dump.age' in result.stdout


@needs_sh
@pytest.mark.parametrize('args', [['--basename', '../x'], ['--user-table', 'users; drop']])
def test_restore_refuses_bad_basename_or_table(restore_env, args):
    run, log, identity, dump = restore_env

    result = run('--identity', str(identity), '--file', str(dump), *args)

    assert result.returncode != 0
    assert not (log / 'psql.calls').exists()


# --- Deploy files ---------------------------------------------------------------------------

def test_provision_installs_backup_packages_and_temp_dir():
    script = (DEPLOY / 'provision.sh').read_text()
    packages = re.search(r'^PACKAGES=\((.*?)\)', script, re.S | re.M).group(1).split()
    client = re.search(r'^PG_CLIENT_PACKAGES=\((.*?)\)', script, re.S | re.M).group(1).split()
    assert client == ['postgresql-client-18']
    assert 'age' in packages
    assert 'ensure_dir "$APP_HOME/shared/backup-tmp" 0700 "$APP_USER:$APP_USER"' in script


def _provision_job_lines(site, jobs_conf, systemd_dir=None):
    """provision.sh's "Scheduled jobs" loops (read jobs.conf, install the site's timers,
    remove its timers no longer listed), run for `site` against a fake systemd folder
    (`install_file`, `sudo` and `changed` are stubs). stdout: one `name|schedule` per
    timer installed, then the `changed:` lines."""
    script = (DEPLOY / 'provision.sh').read_text()
    trim = script[script.index('trim() {'):]
    trim = trim[:trim.index('\n}\n') + 3]
    loops = script[script.index("# A site name as mytp-core's jobs.SITE_RE"):]
    loops = loops[:loops.index('if ((jobs_changed)); then')]
    loops = loops.replace('    job_names+=("$name")\n',
                          '    job_names+=("$name")\n    printf "%s|%s\\n" "$name" "$schedule"\n')
    systemd_dir = systemd_dir or Path(jobs_conf).parent / 'systemd'
    systemd_dir.mkdir(exist_ok=True)
    loops = loops.replace('/etc/systemd/system', str(systemd_dir))
    work = Path(jobs_conf).parent / 'work'
    work.mkdir(exist_ok=True)
    # No `set -u`: macOS's bash 3.2 calls an empty array unbound (the host runs bash 5).
    body = (f'set -eo pipefail\nSITE={site}\nJOB=mytp-$SITE-job\nWORK={work}\n'
            f'DEPLOY_DIR={Path(jobs_conf).parent}\n{trim}\n'
            'changed() { echo "  changed: $*"; }\n'
            'sudo() { [[ $1 == systemctl ]] && return 0; "$@"; }\n'
            'install_file() { cp "$1" "$2"; }\n'
            "timer_template='OnCalendar=__SCHEDULE__ (__NAME__)'\n"
            'job_names=()\nchanged_timers=()\njobs_changed=0\n'
            f'{loops}')
    body = body.replace('done <"$DEPLOY_DIR/jobs.conf"', 'done <"$1"')
    return subprocess.run(['bash', '-c', body, 'provision', str(jobs_conf)],
                          capture_output=True, text=True, timeout=30)


def _installed(result):
    return dict(line.split('|') for line in result.stdout.splitlines() if '|' in line)


@needs_sh
@pytest.mark.parametrize('site, schedule', [('dev', 'Sun *-*-* 21:30:00 UTC'),
                                            ('dev-ae', 'Sun *-*-* 22:30:00 UTC')])
def test_provision_installs_each_sites_own_restoretest_timer(site, schedule, tmp_path):
    conf = tmp_path / 'jobs.conf'
    conf.write_text((DEPLOY / 'jobs.conf').read_text())

    result = _provision_job_lines(site, conf)

    assert result.returncode == 0, result.stderr
    lines = _installed(result)
    assert lines['restoretest'] == schedule
    assert lines['backupdb'] == '*-*-* 02:30:00 UTC'
    # Exactly the lines mytp-core's runjob reads for that site (the Sentry monitors).
    assert set(lines) == set(jobs.load_jobs(DEPLOY / 'jobs.conf', site))
    assert (tmp_path / 'systemd' / f'mytp-{site}-job@restoretest.timer').read_text() == (
        f'OnCalendar={schedule} (restoretest)\n')


@needs_sh
def test_provision_skips_other_sites_lines(tmp_path):
    conf = tmp_path / 'jobs.conf'
    conf.write_text('a | x | *-*-* 01:00 UTC | dev-ae , admindev\n')

    assert _installed(_provision_job_lines('dev', conf)) == {}
    assert _installed(_provision_job_lines('admindev', conf)) == {'a': '*-*-* 01:00 UTC'}
    assert _installed(_provision_job_lines('dev-ae', conf)) == {'a': '*-*-* 01:00 UTC'}


@needs_sh
@pytest.mark.parametrize('line, message', [
    ('a | x | *-*-* 01:00 UTC | Dev', 'invalid site'),
    ('a | x | *-*-* 01:00 UTC | dev-', 'invalid site'),
    ('a | x | *-*-* 01:00 UTC | dev ae', 'invalid site'),
    ('a | x | *-*-* 01:00 UTC | dev,dev', 'listed twice'),
    ('a | x | *-*-* 01:00 UTC |', 'empty sites'),
    ('a | x | *-*-* 01:00 UTC |  ', 'empty sites'),
    ('a | x | *-*-* 01:00 UTC | dev,', 'empty sites'),
    ('a | x | *-*-* 01:00 UTC | ,dev', 'empty sites'),
    ('a | x | *-*-* 01:00 UTC | dev,,dev-ae', 'empty sites'),
    ('a | x | *-*-* 01:00 UTC | dev | more', 'expected'),
])
def test_provision_refuses_a_bad_sites_field(tmp_path, line, message):
    conf = tmp_path / 'jobs.conf'
    conf.write_text(line + '\n')

    result = _provision_job_lines('dev', conf)

    assert result.returncode != 0 and message in result.stderr, result.stderr
    # The same lines fail mytp-core's parser too (the tests of every jobs.conf).
    with pytest.raises(ValueError):
        jobs.parse_job_lines(line)


@needs_sh
def test_provision_refuses_overlapping_site_lines(tmp_path):
    conf = tmp_path / 'jobs.conf'
    conf.write_text('a | x | *-*-* 01:00 UTC | dev\n'
                    'a | x | *-*-* 02:00 UTC | dev-ae, dev\n')

    result = _provision_job_lines('dev', conf)

    assert result.returncode != 0 and "duplicate job 'a'" in result.stderr
    # A site in neither line is not concerned.
    assert _provision_job_lines('admindev', conf).returncode == 0


@needs_sh
def test_provision_removes_a_timer_whose_line_moved_to_another_site(tmp_path):
    systemd = tmp_path / 'systemd'
    systemd.mkdir()
    for unit in ('mytp-dev-job@restoretest.timer', 'mytp-dev-job@backupdb.timer',
                 'mytp-dev-ae-job@restoretest.timer'):
        (systemd / unit).write_text('old\n')
    conf = tmp_path / 'jobs.conf'
    conf.write_text('backupdb | backupdb | *-*-* 02:30:00 UTC\n'
                    'restoretest | restoretest | Sun *-*-* 22:30:00 UTC | dev-ae\n')

    result = _provision_job_lines('dev', conf, systemd)

    assert result.returncode == 0, result.stderr
    assert 'changed: removed mytp-dev-job@restoretest.timer' in result.stdout
    assert sorted(p.name for p in systemd.iterdir()) == [
        'mytp-dev-ae-job@restoretest.timer', 'mytp-dev-job@backupdb.timer']


ADMIN_JOBS = REPO.parent / 'mytp-admin' / 'deploy' / 'jobs.conf'


@needs_sh
@pytest.mark.parametrize('source', ['sibling', 'inline'])
def test_provision_installs_the_admin_sites_jobs_conf_for_admindev(tmp_path, source):
    """admindev is provisioned with this provision.sh and the admin repo's jobs.conf (one
    site: three-field lines). The sibling checkout's file when there is one (locally),
    and always a copy of its restoretest line."""
    conf = tmp_path / 'jobs.conf'
    if source == 'sibling':
        if not ADMIN_JOBS.is_file():
            pytest.skip('no ../mytp-admin checkout')
        conf.write_text(ADMIN_JOBS.read_text())
    else:
        conf.write_text('backupdb | backupdb | *-*-* 02:40:00 UTC\n'
                        'restoretest | restoretest | Sun *-*-* 23:30:00 UTC\n')

    result = _provision_job_lines('admindev', conf)

    assert result.returncode == 0, result.stderr
    lines = _installed(result)
    assert lines['restoretest'] == 'Sun *-*-* 23:30:00 UTC'
    assert set(lines) == set(jobs.load_jobs(conf, 'admindev'))


def test_backup_tmp_dir_default_matches_provision():
    text = (REPO / 'mytreatplan' / 'settings.py').read_text()
    assert ("config('BACKUP_TMP_DIR', default=str(SITE_HOME / 'shared' / 'backup-tmp'))"
            in text)
    # provision.sh creates "$APP_HOME/shared/backup-tmp" with APP_HOME=/srv/mytp-$SITE.
    assert 'APP_HOME=/srv/mytp-$SITE' in (DEPLOY / 'provision.sh').read_text()


def test_staging_env_example_lists_backup_settings_without_secrets():
    values = dict(
        line.split('=', 1) for line in (DEPLOY / 'staging.env.example').read_text().splitlines()
        if line.strip() and not line.startswith('#')
    )
    for key in backupdb.REQUIRED_SETTINGS:
        assert key in values
    assert values['BACKUP_S3_ACCESS_KEY'] == ''
    assert values['BACKUP_S3_SECRET_KEY'] == ''
    assert values['BACKUP_AGE_RECIPIENT'] == ''
    assert values['BACKUP_PREFIX'] == 'staging-dev/'  # one prefix per site (AD-24)
    assert 'sbg' in values['BACKUP_S3_ENDPOINT']
    # The restore test (epic-platform entry 13): typed on the server only.
    assert values['BACKUP_AGE_RESTORE_RECIPIENT'] == ''
    assert values['RESTORE_TEST_DATABASE_URL'] == ''
    assert values['SENTRY_ENVIRONMENT'] == 'dev'  # the site's own environment


def test_requirements_pin_boto3():
    lines = (REPO / 'requirements.txt').read_text().splitlines()
    assert any(re.fullmatch(r'boto3==\d+\.\d+\.\d+', line) for line in lines)
    assert any(re.fullmatch(r'botocore==\d+\.\d+\.\d+', line) for line in lines)


def test_restore_script_is_executable_and_guarded():
    assert RESTORE.stat().st_mode & stat.S_IXUSR
    text = RESTORE.read_text()
    assert '_restore_test$' in text
    assert '--single-transaction' in text
