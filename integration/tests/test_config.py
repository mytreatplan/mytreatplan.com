"""The configuration snapshot (epic-admin-interface entry 4): `config.pull()` (job, command
and task), the signed nudge endpoint, `show_config` and the outage warning. The admin site
is a fake at mytp_core.interface._open that checks the portal's signature and answers
with a snapshot signed by the admin's key (fixed, non-secret test seeds). No test reaches
the network or a KMS."""
import base64
import copy
import io
import json
import logging
import threading
import urllib.error
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.conf import settings as django_settings
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import connection
from django.utils import timezone

from integration import config
from integration.models import AdminDirectoryEntry, ConfigState
from mytp_core import interface, snapshots

DEPLOY = Path(django_settings.BASE_DIR) / 'deploy'
ADMIN_SEED = b'test-only-interface-seed-admin!!'
PORTAL_SEED = b'test-only-interface-seed-portal!'
OTHER_SEED = b'test-only-interface-seed-other!!'
ADMIN = interface.SigningKey('a1', ADMIN_SEED)
PORTAL = interface.SigningKey('p1', PORTAL_SEED)
CONFIG_URL = 'https://admindev.example.test/api/v1/config'
NUDGE = '/api/v1/config/nudge'
ADA = '0b6f3d1e-5a7c-4f2e-9b1a-2c3d4e5f6a7b'
BOB = '8f14e45f-ceea-4e7a-9f1d-5b6c7d8e9f01'
SECRET_NAME = 'Admin-Name-That-Must-Not-Leak'


def snapshot(version=1, min_reader=1, **changes):
    data = {
        'version': version, 'min_reader': min_reader,
        'generated_at': '2026-11-05T10:00:00Z',
        'admin_directory': [
            {'id': ADA, 'display_name': 'Ada Admin', 'title': 'Sysadmin', 'active': True,
             'time_zone': 'Europe/Madrid'},
            {'id': BOB, 'display_name': 'Bob Gone', 'title': '', 'active': False,
             'time_zone': 'Asia/Dubai'},
        ],
    }
    data.update(changes)
    return data


@pytest.fixture(autouse=True)
def interface_settings(settings):
    settings.SITE = 'dev'
    settings.REGION = 'eu'
    settings.INTERFACE_SIGNING_KEY = f'p1:{base64.b64encode(PORTAL_SEED).decode()}'
    settings.INTERFACE_PEERS = 'admindev=https://admindev.example.test'
    settings.INTERFACE_PEER_KEYS = f'admindev:a1:{ADMIN.public_text}'
    settings.INTERFACE_ADMIN_SITE = ''
    interface.reset_signing_key()
    cache.clear()
    yield settings
    interface.reset_signing_key()
    cache.clear()


class FakeAdmin:
    """The admin's GET /api/v1/config: verifies the portal's signature, then answers the
    current `snapshot` signed for dev by the admin's key. `down` makes it unreachable,
    `status` answers that HTTP error, `tamper(answer)` changes the answer after signing,
    `key` signs with another key."""

    def __init__(self):
        self.ring = interface.Keyring.from_entries([('dev', 'p1', PORTAL.public_text)])
        self.snapshot = snapshot(version=1)
        self.down = False
        self.status = None
        self.tamper = None
        self.key = ADMIN
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, request, timeout):
        with self.lock:
            self.calls += 1
        if self.down:
            raise urllib.error.URLError(ConnectionRefusedError(61, 'refused'))
        parts = urlsplit(request.full_url)
        assert request.full_url == CONFIG_URL and request.method == 'GET'
        claims = interface.verify_request(
            request.get_header('Authorization'), 'GET',
            interface.request_target(parts.path, parts.query), b'',
            keyring=self.ring, audience='admindev')
        if self.status:
            raise urllib.error.HTTPError(request.full_url, self.status, 'x', {},
                                         io.BytesIO(b'<html>bad gateway</html>'))
        answer = snapshots.sign_snapshot(copy.deepcopy(self.snapshot),
                                         audience=claims['iss'], issuer='admindev',
                                         signing_key=self.key)
        if self.tamper:
            self.tamper(answer)
        response = io.BytesIO(json.dumps(answer).encode())
        response.status = 200
        return response


@pytest.fixture
def admin(monkeypatch):
    fake = FakeAdmin()
    monkeypatch.setattr(interface, '_open', fake)
    return fake


def state():
    return ConfigState.objects.get()


def directory():
    return sorted((str(e.id), e.display_name, e.title, e.active, e.time_zone)
                  for e in AdminDirectoryEntry.objects.all())


def refused(code):
    with pytest.raises(config.PullFailed) as error:
        config.pull()
    assert error.value.code == code
    return error.value


def applied(admin, version):
    admin.snapshot = snapshot(version=version)
    assert config.pull() == ('applied', version)


# --- Matrix -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_first_pull_applies_whatever_version(admin, caplog):
    caplog.set_level(logging.INFO)
    admin.snapshot = snapshot(version=7)
    assert not ConfigState.objects.exists()
    out = io.StringIO()
    call_command('pull_config', stdout=out)
    assert out.getvalue() == 'pull_config: v7 applied\n'
    row = state()
    assert (row.version, row.last_error) == (7, '')
    assert row.applied_at and row.last_pull_at and row.last_success_at
    assert directory() == [(ADA, 'Ada Admin', 'Sysadmin', True, 'Europe/Madrid'),
                           (BOB, 'Bob Gone', '', False, 'Asia/Dubai')]
    assert 'config snapshot v7 applied (2 admin directory entries)' in caplog.text


@pytest.mark.django_db
def test_a_higher_version_replaces_the_directory(admin):
    applied(admin, 1)
    admin.snapshot = snapshot(version=2, admin_directory=[
        {'id': ADA, 'display_name': 'Ada Renamed', 'title': 'Sysadmin', 'active': True,
         'time_zone': 'Europe/Madrid'}])
    assert config.pull() == ('applied', 2)
    assert directory() == [(ADA, 'Ada Renamed', 'Sysadmin', True, 'Europe/Madrid')]
    assert state().version == 2


@pytest.mark.django_db
def test_the_same_version_changes_nothing(admin):
    applied(admin, 3)
    before = state().applied_at
    admin.snapshot = snapshot(version=3, admin_directory=[])
    out = io.StringIO()
    call_command('pull_config', stdout=out)
    assert out.getvalue() == 'pull_config: v3 unchanged\n'
    assert len(directory()) == 2 and state().applied_at == before
    assert state().last_error == ''


@pytest.mark.django_db
def test_an_older_snapshot_is_not_applied(admin, caplog):
    applied(admin, 5)
    admin.snapshot = snapshot(version=4, admin_directory=[])
    out = io.StringIO()
    call_command('pull_config', stdout=out)  # exits 0: one Sentry warning per outage
    assert out.getvalue() == 'pull_config: refused (stale); the applied snapshot is kept\n'
    assert (state().version, state().last_error) == (5, 'stale')
    assert len(directory()) == 2
    assert 'config pull refused: stale' in caplog.text


@pytest.mark.django_db
@pytest.mark.parametrize('setup, code', [
    (lambda a: setattr(a, 'down', True), 'network'),
    (lambda a: setattr(a, 'status', 502), 'http_502'),
    (lambda a: setattr(a, 'status', 503), 'http_503'),
])
def test_admin_down_keeps_the_current_snapshot(admin, setup, code):
    applied(admin, 2)
    success = state().last_success_at
    setup(admin)
    admin.snapshot = snapshot(version=3, admin_directory=[])
    refused(code)
    row = state()
    assert (row.version, row.last_error, row.last_success_at) == (2, code, success)
    assert len(directory()) == 2
    # The admin site is back: the next run applies.
    admin.down, admin.status = False, None
    assert config.pull() == ('applied', 3)
    assert state().last_error == ''


def _drop_signature(answer):
    del answer['signature']


def _sha_mismatch(answer):
    answer['snapshot']['generated_at'] = '2026-11-06T00:00:00Z'


def _ver_mismatch(answer):
    answer['snapshot']['version'] = 99


def _garbage(answer):
    answer['signature'] = 'not.a.token'


@pytest.mark.django_db
@pytest.mark.parametrize('tamper, key, code', [
    (_drop_signature, ADMIN, 'bad_signature'),
    (_garbage, ADMIN, 'bad_signature'),
    (None, interface.SigningKey('a1', OTHER_SEED), 'bad_signature'),
    (None, interface.SigningKey('a9', ADMIN_SEED), 'bad_signature'),
    (_sha_mismatch, ADMIN, 'bad_signature'),
    (_ver_mismatch, ADMIN, 'bad_signature'),
])
def test_unsigned_or_tampered_snapshots_are_not_applied(admin, tamper, key, code):
    applied(admin, 1)
    admin.snapshot = snapshot(version=2, admin_directory=[])
    admin.tamper, admin.key = tamper, key
    refused(code)
    assert (state().version, state().last_error) == (1, code)
    assert len(directory()) == 2


@pytest.mark.django_db
def test_a_snapshot_for_another_site_or_expired_is_refused(admin, monkeypatch):
    applied(admin, 1)
    admin.snapshot = snapshot(version=2, admin_directory=[])

    def for_dev_ae(answer):
        answer.update(snapshots.sign_snapshot(answer['snapshot'], audience='dev-ae',
                                              issuer='admindev', signing_key=ADMIN))
    admin.tamper = for_dev_ae
    refused('audience')

    def expired(answer):
        answer.update(snapshots.sign_snapshot(answer['snapshot'], audience='dev',
                                              issuer='admindev', signing_key=ADMIN,
                                              now=1_000_000_000))
    admin.tamper = expired
    refused('expired')
    assert state().version == 1


@pytest.mark.django_db
def test_an_invalid_body_is_refused(admin):
    import jwt
    applied(admin, 1)
    admin.snapshot = snapshot(version=2, admin_directory=[])

    def invalid(answer):
        # Signed correctly over a snapshot that breaks the contract.
        bad = snapshot(version=2, admin_directory=[{'id': 'not-a-uuid'}])
        claims = jwt.decode(answer['signature'], options={'verify_signature': False})
        claims['sha'] = snapshots.snapshot_sha(bad)
        answer['snapshot'] = bad
        answer['signature'] = ADMIN.sign(claims)
    admin.tamper = invalid
    refused('invalid_body')
    admin.tamper = lambda answer: answer.update(snapshot='x')
    refused('invalid_body')
    assert (state().version, len(directory())) == (1, 2)


@pytest.mark.django_db
def test_a_newer_format_is_kept_out_with_one_warning_per_version(admin, caplog):
    applied(admin, 1)
    caplog.set_level(logging.WARNING, logger='integration.config')
    admin.snapshot = snapshot(version=2, min_reader=2, admin_directory=[])
    refused('newer_format')
    refused('newer_format')
    assert caplog.text.count('config snapshot v2 kept out') == 1
    admin.snapshot = snapshot(version=3, min_reader=2, admin_directory=[])
    refused('newer_format')
    assert caplog.text.count('config snapshot v3 kept out') == 1
    assert 'config pull refused' not in caplog.text
    assert (state().version, state().last_error, state().newer_format_version) == \
        (1, 'newer_format', 3)
    assert len(directory()) == 2


@pytest.mark.django_db
@pytest.mark.parametrize('name, value', [
    ('INTERFACE_PEERS', ''), ('INTERFACE_SIGNING_KEY', ''), ('INTERFACE_PEER_KEYS', 'x'),
])
def test_without_configuration_the_pull_fails_and_records_it(admin, settings, name, value):
    setattr(settings, name, value)
    interface.reset_signing_key()
    with pytest.raises(CommandError, match='not_configured'):
        call_command('pull_config', stdout=io.StringIO())
    assert state().last_error == 'not_configured' and admin.calls == 0


@pytest.mark.django_db
def test_a_signing_key_the_kms_cannot_unwrap_is_recorded_without_failing_the_job(
        admin, monkeypatch):
    applied(admin, 1)

    def unavailable():
        raise interface.SigningKeyUnavailable('the KMS did not answer')
    monkeypatch.setattr(interface, 'get_signing_key', unavailable)
    out = io.StringIO()
    call_command('pull_config', stdout=out)
    assert out.getvalue() == ('pull_config: refused (signing_key); the applied snapshot '
                              'is kept\n')
    assert (state().version, state().last_error) == (1, 'signing_key')


@pytest.mark.django_db
def test_an_outage_does_not_fail_the_job(admin):
    admin.down = True
    out = io.StringIO()
    call_command('runjob', 'pullconfig', stdout=out, stderr=io.StringIO())
    assert out.getvalue().startswith('job pullconfig: ok')
    assert state().last_error == 'network'


# --- Acceptance: rollback and concurrency -----------------------------------------------

@pytest.mark.django_db
def test_a_pull_failing_halfway_through_applying_keeps_the_previous_one(admin, monkeypatch,
                                                                         caplog):
    applied(admin, 1)
    admin.snapshot = snapshot(version=2, admin_directory=[
        {'id': ADA, 'display_name': SECRET_NAME, 'title': '', 'active': True,
         'time_zone': 'UTC'}])
    real = ConfigState.save

    def failing_save(self, *args, **kwargs):
        if kwargs.get('update_fields') == ['version', 'applied_at']:
            raise RuntimeError(f'database gone ({SECRET_NAME})')
        return real(self, *args, **kwargs)
    monkeypatch.setattr(ConfigState, 'save', failing_save)
    refused('apply_failed')
    monkeypatch.setattr(ConfigState, 'save', real)
    assert (state().version, state().last_error) == (1, 'apply_failed')
    assert directory() == [(ADA, 'Ada Admin', 'Sysadmin', True, 'Europe/Madrid'),
                           (BOB, 'Bob Gone', '', False, 'Asia/Dubai')]
    assert 'config snapshot v2 not applied (RuntimeError)' in caplog.text
    assert SECRET_NAME not in caplog.text


@pytest.mark.django_db(transaction=True)
def test_two_pulls_at_once_apply_a_new_version_once(admin):
    if connection.vendor != 'postgresql':
        pytest.skip('row locks need PostgreSQL')
    from django.db import connections
    applied(admin, 1)
    for version in (2, 3, 4):
        admin.snapshot = snapshot(version=version)
        barrier = threading.Barrier(2)
        outcomes, errors = [], []

        def run():
            try:
                barrier.wait()
                outcomes.append(config.pull()[0])
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert sorted(outcomes) == ['applied', 'unchanged']
        assert state().version == version
        assert len(directory()) == 2


@pytest.mark.django_db
def test_the_version_never_goes_down(admin):
    applied(admin, 4)
    for version in (3, 1, 4):
        admin.snapshot = snapshot(version=version, admin_directory=[])
        if version < 4:
            refused('stale')
        else:
            assert config.pull() == ('unchanged', 4)
        assert state().version == 4 and len(directory()) == 2


# --- The outage warning -----------------------------------------------------------------

@pytest.mark.django_db
def test_one_sentry_warning_per_outage_without_contents(admin, sentry_capture, caplog,
                                                        django_capture_on_commit_callbacks):
    caplog.set_level(logging.INFO)
    admin.snapshot = snapshot(version=1, admin_directory=[
        {'id': ADA, 'display_name': SECRET_NAME, 'title': '', 'active': True,
         'time_zone': 'UTC'}])
    assert config.pull()[0] == 'applied'
    admin.down = True
    with django_capture_on_commit_callbacks(execute=True):
        refused('network')
    assert sentry_capture.events == []
    ConfigState.objects.update(last_success_at=timezone.now() - timedelta(minutes=61))
    for _ in range(3):
        with django_capture_on_commit_callbacks(execute=True):
            refused('network')
    assert len(sentry_capture.events) == 1
    sent = sentry_capture.events[0]
    assert sent['level'] == 'warning' and sent['tags']['config.error'] == 'network'
    assert SECRET_NAME not in json.dumps(sent, default=str)
    assert state().warned_at is not None
    # The next good pull resets it; the next outage warns again.
    admin.down = False
    assert config.pull() == ('unchanged', 1)
    assert state().warned_at is None
    admin.down = True
    ConfigState.objects.update(last_success_at=timezone.now() - timedelta(hours=2))
    with django_capture_on_commit_callbacks(execute=True):
        refused('network')
    assert len(sentry_capture.events) == 2
    assert SECRET_NAME not in caplog.text


@pytest.mark.django_db
def test_a_site_that_never_pulled_warns_an_hour_after_its_first_attempt(
        admin, sentry_capture, django_capture_on_commit_callbacks):
    admin.down = True
    with django_capture_on_commit_callbacks(execute=True):
        refused('network')
    ConfigState.objects.update(created_at=timezone.now() - timedelta(hours=1))
    with django_capture_on_commit_callbacks(execute=True):
        refused('network')
    assert len(sentry_capture.events) == 1


# --- The nudge --------------------------------------------------------------------------

def nudge(client, key=ADMIN, issuer='admindev', body=b'{}'):
    token = interface.sign_request('POST', NUDGE, body, audience='dev', issuer=issuer,
                                   signing_key=key)
    return client.post(NUDGE, data=body, content_type='application/json',
                       HTTP_AUTHORIZATION=interface.authorization_header(token))


def pull_tasks():
    from django_tasks_db.models import DBTaskResult
    return DBTaskResult.objects.filter(task_path='integration.tasks.pull_config_task')


@pytest.mark.django_db
def test_a_nudge_is_202_and_queues_one_pull(client, admin):
    response = nudge(client)
    assert response.status_code == 202
    assert response.json() == {'status': 'queued'}
    # Repeated nudges coalesce into the queued pull.
    assert nudge(client).json() == {'status': 'already_queued'}
    assert nudge(client).status_code == 202
    assert pull_tasks().count() == 1
    assert admin.calls == 0  # the pull runs on the worker, not in the request


@pytest.mark.django_db
def test_the_nudge_must_be_signed_by_the_admin_site(client, admin):
    assert client.post(NUDGE, data=b'{}', content_type='application/json').status_code == 401
    other = interface.SigningKey('a1', OTHER_SEED)
    assert nudge(client, key=other).status_code == 401
    assert nudge(client, issuer='dev-ae').status_code == 401
    assert not pull_tasks().exists()


@pytest.mark.django_db
def test_a_nudge_from_another_signed_peer_is_refused(client, admin, settings):
    from integration.models import InterfaceCall
    other = interface.SigningKey('o1', OTHER_SEED)
    settings.INTERFACE_PEER_KEYS = (f'admindev:a1:{ADMIN.public_text},'
                                    f'dev-ae:o1:{other.public_text}')
    response = nudge(client, key=other, issuer='dev-ae')
    assert response.status_code == 401
    assert response.json() == interface.refusal_body('unknown_key')
    call = InterfaceCall.objects.get()
    assert (call.accepted, call.reason, call.iss, call.kid) == (False, 'unknown_key',
                                                               'dev-ae', 'o1')
    assert client.get(NUDGE).status_code == 405
    assert not pull_tasks().exists()


@pytest.mark.django_db(transaction=True)
def test_change_and_nudge_applies_through_the_worker(client, admin):
    applied(admin, 1)
    admin.snapshot = snapshot(version=2, admin_directory=[
        {'id': ADA, 'display_name': 'Ada Renamed', 'title': '', 'active': True,
         'time_zone': 'UTC'}])
    assert nudge(client).status_code == 202
    call_command('db_worker', '--batch', '--no-startup-delay', '--no-reload', verbosity=0)
    assert state().version == 2
    assert directory() == [(ADA, 'Ada Renamed', '', True, 'UTC')]
    task = pull_tasks().get()
    assert task.return_value == {'outcome': 'applied', 'version': 2}
    # The task cleared the coalescing flag: the next nudge queues a new pull.
    assert nudge(client).json() == {'status': 'queued'}


@pytest.mark.django_db
def test_the_task_records_a_refusal_without_raising(admin):
    from integration.tasks import pull_config_task
    admin.down = True
    assert pull_config_task.call() == {'error': 'network'}
    assert state().last_error == 'network'


@pytest.mark.django_db
def test_a_lost_nudge_is_covered_by_the_next_scheduled_pull(admin):
    """The portal was down during the nudge: the 5-minute job applies the version."""
    applied(admin, 1)
    admin.snapshot = snapshot(version=2)
    call_command('runjob', 'pullconfig', stdout=io.StringIO(), stderr=io.StringIO())
    assert state().version == 2


# --- show_config and the job ------------------------------------------------------------

@pytest.mark.django_db
def test_show_config_prints_the_version_and_the_directory(admin):
    out = io.StringIO()
    call_command('show_config', stdout=out)
    assert out.getvalue().splitlines() == ['version: none (never pulled)',
                                           'admin directory: 0']
    assert not ConfigState.objects.exists()  # reading does not start the outage clock
    applied(admin, 7)
    out = io.StringIO()
    call_command('show_config', stdout=out)
    lines = out.getvalue().splitlines()
    assert lines[0] == 'version: 7' and lines[1].endswith('Z')
    assert lines[4] == 'last error: -' and lines[5] == 'admin directory: 2'
    assert lines[6] == f'  {ADA}  Ada Admin  Sysadmin  active  Europe/Madrid'
    assert lines[7] == f'  {BOB}  Bob Gone  -  inactive  Asia/Dubai'


@pytest.mark.django_db
def test_the_pull_job_runs_every_5_minutes():
    from mytp_core import jobs
    declared = jobs.load_jobs(DEPLOY / 'jobs.conf')
    pull = declared['pullconfig']
    assert (pull.command, pull.args) == ('pull_config', ())
    assert pull.schedule == '*-*-* *:00/5:00 UTC'
    assert jobs.oncalendar_to_crontab(pull.schedule) == '*/5 * * * *'


@pytest.mark.django_db
def test_the_admin_site_url_is_split_out(settings):
    from integration import outbox
    assert outbox.admin_site_url() == ('admindev', 'https://admindev.example.test')
    assert outbox.admin_site() == ('admindev',
                                   'https://admindev.example.test/api/v1/events')
