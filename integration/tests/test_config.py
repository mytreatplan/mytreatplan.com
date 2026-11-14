"""The configuration snapshot (epic-admin-interface entry 4): `config.pull()` (job, command
and task), the signed nudge endpoint, `show_config` and the outage warning. The admin site
is a fake at mytp_core.interface._open that checks the portal's signature and answers
with a snapshot signed by the admin's key (fixed, non-secret test seeds). No test reaches
the network or a KMS."""
import io
import json
import logging
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core.management import CommandError, call_command
from django.db import connection
from django.utils import timezone

from integration import config
from integration.models import AdminDirectoryEntry, ConfigState
from integration.tests.snapshot_rows import (ADA, ADMIN, ADMIN_SEED, BOB, snapshot,
                                             state)
from mytp_core import interface, snapshots

# The root conftest's `interface_settings`, module-wide; its `admin` is the fake admin site.
pytestmark = pytest.mark.usefixtures('interface_settings')

DEPLOY = Path(django_settings.BASE_DIR) / 'deploy'
OTHER_SEED = b'test-only-interface-seed-other!!'
NUDGE = '/api/v1/config/nudge'
SECRET_NAME = 'Admin-Name-That-Must-Not-Leak'


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
        if kwargs.get('update_fields') == config.APPLIED_FIELDS:
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


# --- The release that applied the snapshot ----------------------------------------------

RELEASE_A = '6b27169cc0f014905d7dbfe73335ef7aa2123d26'
RELEASE_B = 'c0ffee69cc0f014905d7dbfe73335ef7aa2123d2'
RENAMED = [{'id': ADA, 'display_name': 'Ada Renamed', 'title': 'Sysadmin', 'active': True,
            'time_zone': 'Europe/Madrid'}]


def previous_release_applies(version):
    """What a release from before the mark writes when it applies a snapshot: the
    version and the time (`save(update_fields=['version', 'applied_at'])`), never the
    mark."""
    ConfigState.objects.filter(pk=ConfigState.SINGLETON).update(
        version=version, applied_at=timezone.now())


def mark():
    row = state()
    return row.version, row.applied_release, row.applied_release_version


@pytest.fixture
def probe():
    """A section applier that counts its calls: every section is applied again."""
    calls = []
    config._SECTION_APPLIERS['catalogue_probe'] = lambda value: calls.append(value)
    yield calls
    del config._SECTION_APPLIERS['catalogue_probe']


@pytest.mark.django_db
def test_the_same_release_and_version_rewrites_nothing(admin, settings, probe):
    settings.SENTRY_RELEASE = RELEASE_A
    applied(admin, 3)
    assert mark() == (3, RELEASE_A, 3) and len(probe) == 1
    before = state().applied_at
    admin.snapshot = snapshot(version=3, admin_directory=[])
    assert config.pull() == ('unchanged', 3)
    assert len(directory()) == 2 and len(probe) == 1
    assert state().applied_at == before and mark() == (3, RELEASE_A, 3)


@pytest.mark.django_db
def test_a_new_release_applies_the_same_version_again_once(admin, settings, probe, caplog):
    """The acceptance criterion: version N applied by the previous release, this release
    pulls N: the directory and every section are applied again; a second pull is
    unchanged."""
    caplog.set_level(logging.INFO)
    settings.SENTRY_RELEASE = RELEASE_A
    applied(admin, 3)
    before = state().applied_at
    settings.SENTRY_RELEASE = RELEASE_B
    admin.snapshot = snapshot(version=3, admin_directory=RENAMED)
    out = io.StringIO()
    call_command('pull_config', stdout=out)
    assert out.getvalue() == 'pull_config: v3 applied\n'
    assert directory() == [(ADA, 'Ada Renamed', 'Sysadmin', True, 'Europe/Madrid')]
    assert len(probe) == 2 and mark() == (3, RELEASE_B, 3)
    assert state().applied_at > before and state().last_error == ''
    assert caplog.text.count('config snapshot v3 applied') == 2
    admin.snapshot = snapshot(version=3, admin_directory=[])
    assert config.pull() == ('unchanged', 3)
    assert len(directory()) == 1 and len(probe) == 2
    # And back to the release before: it applies it again too.
    settings.SENTRY_RELEASE = RELEASE_A
    assert config.pull() == ('applied', 3)
    assert directory() == [] and mark() == (3, RELEASE_A, 3)


@pytest.mark.django_db
def test_applying_again_that_fails_keeps_the_previous_rows(admin, settings, caplog):
    settings.SENTRY_RELEASE = RELEASE_A
    applied(admin, 3)
    settings.SENTRY_RELEASE = RELEASE_B
    admin.snapshot = snapshot(version=3, admin_directory=RENAMED)

    def failing(value):
        raise RuntimeError(f'applier broke ({SECRET_NAME})')
    config._SECTION_APPLIERS['catalogue_probe'] = failing
    try:
        refused('apply_failed')
    finally:
        del config._SECTION_APPLIERS['catalogue_probe']
    assert mark() == (3, RELEASE_A, 3) and state().last_error == 'apply_failed'
    assert len(directory()) == 2 and SECRET_NAME not in caplog.text
    # Still to do: the next pull applies it.
    assert config.pull() == ('applied', 3)
    assert mark() == (3, RELEASE_B, 3) and len(directory()) == 1


@pytest.mark.django_db
def test_a_version_the_previous_release_applied_in_between_is_applied_again(
        admin, settings, probe):
    """This release applied N; a release from before the mark (a rollback) applied N+1
    and left the mark; this release pulls N+1."""
    settings.SENTRY_RELEASE = RELEASE_A
    applied(admin, 3)
    previous_release_applies(4)
    assert mark() == (4, RELEASE_A, 3)
    admin.snapshot = snapshot(version=4, admin_directory=RENAMED)
    assert config.pull() == ('applied', 4)
    assert len(directory()) == 1 and len(probe) == 2 and mark() == (4, RELEASE_A, 4)
    assert config.pull() == ('unchanged', 4)


@pytest.mark.django_db
def test_a_pull_of_the_previous_release_during_the_deploy_is_applied_again(
        admin, settings, probe):
    """After `migrate` and before the switch the previous release still pulls: it
    creates the row without knowing the new columns (the database fills them) and
    applies N. The new release applies N again at its first pull."""
    with connection.cursor() as cursor:
        cursor.execute(
            'INSERT INTO integration_configstate (id, version, applied_at, last_pull_at, '
            'last_success_at, last_error, warned_at, newer_format_version, created_at) '
            "VALUES (1, NULL, NULL, NULL, NULL, '', NULL, NULL, %s)", [timezone.now()])
    assert mark() == (None, '', None)
    previous_release_applies(5)
    settings.SENTRY_RELEASE = RELEASE_B
    admin.snapshot = snapshot(version=5)
    assert config.pull() == ('applied', 5)
    assert len(directory()) == 2 and len(probe) == 1 and mark() == (5, RELEASE_B, 5)
    assert config.pull() == ('unchanged', 5)
    # Also when the site has no release identity: a version without a mark is not its own.
    previous_release_applies(6)
    settings.SENTRY_RELEASE = ''
    admin.snapshot = snapshot(version=6)
    assert config.pull() == ('applied', 6)
    assert config.pull() == ('unchanged', 6)


@pytest.mark.django_db
def test_a_lower_version_is_stale_whatever_the_release(admin, settings, probe):
    settings.SENTRY_RELEASE = RELEASE_A
    applied(admin, 5)
    settings.SENTRY_RELEASE = RELEASE_B
    admin.snapshot = snapshot(version=4, admin_directory=[])
    refused('stale')
    assert mark() == (5, RELEASE_A, 5) and len(directory()) == 2 and len(probe) == 1


@pytest.mark.django_db
def test_without_a_release_identity_the_site_is_one_unnamed_release(admin, settings, probe):
    settings.SENTRY_RELEASE = ''
    assert config.current_release() == ''
    applied(admin, 2)
    assert mark() == (2, '', 2)
    admin.snapshot = snapshot(version=2, admin_directory=[])
    assert config.pull() == ('unchanged', 2)
    assert len(directory()) == 2 and len(probe) == 1


@pytest.mark.django_db  # `interface_settings` clears the database cache
def test_the_release_is_the_deployed_sha(settings, tmp_path):
    settings.BASE_DIR = tmp_path  # no REVISION file: Sentry's release (RELEASE_SHA)
    settings.SENTRY_RELEASE = RELEASE_A
    assert config.current_release() == RELEASE_A
    settings.SENTRY_RELEASE = 'x' * 100  # a RELEASE_SHA that is not a SHA: cut to the column
    assert config.current_release() == 'x' * 64
    settings.SENTRY_RELEASE = ''
    assert config.current_release() == ''


@pytest.mark.django_db
def test_the_revision_file_wins_over_a_release_sha_shared_by_every_deploy(
        admin, settings, tmp_path, probe):
    """RELEASE_SHA in the shared settings.env is the same for every release (and wins
    for Sentry's release); each release directory has its own REVISION file."""
    settings.SENTRY_RELEASE = RELEASE_A
    first, second = tmp_path / 'first', tmp_path / 'second'
    for folder, sha in ((first, RELEASE_A), (second, RELEASE_B)):
        folder.mkdir()
        (folder / 'REVISION').write_text(sha + '\n')
    settings.BASE_DIR = first
    applied(admin, 3)
    assert mark() == (3, RELEASE_A, 3)
    settings.BASE_DIR = second
    assert config.current_release() == RELEASE_B
    assert config.pull() == ('applied', 3)
    assert mark() == (3, RELEASE_B, 3) and len(probe) == 2
    assert config.pull() == ('unchanged', 3)


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
    assert lines[2] == 'applied by release: unnamed (no REVISION file or RELEASE_SHA)'
    assert lines[5] == 'last error: -' and lines[6] == 'admin directory: 2'
    assert lines[7] == f'  {ADA}  Ada Admin  Sysadmin  active  Europe/Madrid'
    assert lines[8] == f'  {BOB}  Bob Gone  -  inactive  Asia/Dubai'


@pytest.mark.django_db
def test_show_config_prints_the_release_that_applied_the_version(admin, settings):
    def applied_by():
        out = io.StringIO()
        call_command('show_config', stdout=out)
        return out.getvalue().splitlines()[2]

    settings.SENTRY_RELEASE = RELEASE_A
    applied(admin, 7)
    assert applied_by() == f'applied by release: {RELEASE_A}'
    previous_release_applies(8)
    assert applied_by() == ('applied by release: another release (not recorded; the next '
                            'pull applies it again)')
    ConfigState.objects.update(version=None)
    assert applied_by() == 'applied by release: -'


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
