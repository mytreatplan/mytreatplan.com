"""The client record sync (epic-admin-interface entry 8): `accounts.client_sync.emit` in
each client write (signup, the Django admin's add and change, erasure), the staff rule,
the writes that send nothing, the signed re-sync endpoint and its task, and the backfill
command `sync_clients`. Events land in the outbox (integration.outbox); no test reaches
the network or a KMS (fixed, non-secret test seeds)."""
import base64
import io
import logging
import threading
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import connection, transaction
from django.test import Client

from accounts import client_sync
from accounts.erasure import erase_user, purge_erased
from integration.models import OutboxEvent
from mytp_core import interface
from mytp_core.contracts import ClientUpdated, check_central_payload
from signup.tests.helpers import (FIRST_NAME, LAST_NAME, MOBILE_E164, NORMALIZED, PASSWORD,
                                  _complete)

pytestmark = [pytest.mark.django_db,
              pytest.mark.usefixtures('capture_codes', 'clear_cache', 'portal_urls')]

User = get_user_model()
ADMIN_SEED = b'test-only-interface-seed-admin!!'
PORTAL_SEED = b'test-only-interface-seed-portal!'
OTHER_SEED = b'test-only-interface-seed-other!!'
ADMIN = interface.SigningKey('a1', ADMIN_SEED)
RESYNC = '/api/v1/clients/resync'
OTHER_TEXT = 'Found-us-through-a-secret-friend'


@pytest.fixture(autouse=True)
def interface_settings(settings):
    settings.SITE = 'dev'
    settings.REGION = 'eu'
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 30
    settings.INTERFACE_SIGNING_KEY = f'p1:{base64.b64encode(PORTAL_SEED).decode()}'
    settings.INTERFACE_PEERS = 'admindev=https://admindev.example.test'
    settings.INTERFACE_PEER_KEYS = f'admindev:a1:{ADMIN.public_text}'
    settings.INTERFACE_ADMIN_SITE = ''
    interface.reset_signing_key()
    yield settings
    interface.reset_signing_key()


def events(user=None):
    rows = OutboxEvent.objects.order_by('id')
    if user is not None:
        rows = rows.filter(aggregate_id=user.uid)
    return [(row.type, row.aggregate_version) for row in rows]


def last_payload(user):
    return OutboxEvent.objects.filter(aggregate_id=user.uid).latest('id').payload


def doctor(email='bea@example.com', **fields):
    """A client account written outside the sync (as before this release), sync_version 0."""
    defaults = dict(title='dr', first_name='Bea', last_name='Lopez', country='FR',
                    phone='+33612345678', how_found='others', how_found_other=OTHER_TEXT)
    return User.objects.create_user(email, PASSWORD, **{**defaults, **fields})


def emit(user):
    with transaction.atomic():
        return client_sync.emit(user)


@pytest.fixture
def staff_client():
    staff = User.objects.create_superuser('staff@example.net', PASSWORD)
    client = Client()
    client.force_login(staff)
    return client


def change_form(user, **changes):
    """The Django admin's change form data of `user`, with `changes`."""
    data = {'email': user.email, 'title': user.title, 'first_name': user.first_name,
            'middle_name': user.middle_name, 'last_name': user.last_name,
            'country': user.country.code if user.country else '',
            'how_found': user.how_found, 'how_found_other': user.how_found_other,
            'date_joined_0': '2026-10-13', 'date_joined_1': '10:00:00'}
    if user.is_active:
        data['is_active'] = 'on'
    if user.is_staff:
        data['is_staff'] = 'on'
    data.update(changes)
    return {key: value for key, value in data.items() if value is not None}


# --- Matrix: signup -----------------------------------------------------------------------

def test_signup_sends_client_updated_v1(client, caplog, django_capture_on_commit_callbacks):
    caplog.set_level(logging.INFO)
    with django_capture_on_commit_callbacks(execute=True):
        assert _complete(client)['Location'] == '/en/welcome/'
    user = User.objects.get()
    assert user.sync_version == 1
    assert events() == [('client.updated', 1)]
    event = OutboxEvent.objects.get()
    assert (event.aggregate_id, event.region) == (user.uid, 'eu')
    assert event.payload == {
        'title': 'dr', 'first_name': FIRST_NAME, 'middle_name': '', 'last_name': LAST_NAME,
        'email': NORMALIZED, 'phone': MOBILE_E164, 'country': 'ES', 'home_region': 'eu',
        'active': True, 'joined_at': event.payload['joined_at'], 'how_found': 'linkedin'}
    assert ClientUpdated.model_validate(event.payload).joined_at == user.date_joined
    assert check_central_payload(event.payload)
    # The outbox's lines (the fake verification provider logs the contacts on purpose).
    lines = ' '.join(record.getMessage() for record in caplog.records
                     if record.name.startswith(('integration', 'accounts')))
    assert 'outbox event recorded' in lines
    for secret in (NORMALIZED, FIRST_NAME, LAST_NAME, MOBILE_E164):
        assert secret not in lines


def test_the_payload_carries_the_accounts_home_region(settings):
    settings.REGION = 'ae'
    user = doctor(home_region='ae', country='AE')
    emit(user)
    event = OutboxEvent.objects.get()
    assert (event.region, event.payload['home_region'], event.payload['country']) == (
        'ae', 'ae', 'AE')
    # A row the previous release inserted during a deploy ('') gets this instance's.
    other = doctor('old@example.com')
    User.objects.filter(pk=other.pk).update(home_region='')
    other.refresh_from_db()
    emit(other)
    assert last_payload(other)['home_region'] == settings.REGION_PROFILE.code


def test_a_signup_rolled_back_sends_nothing(client, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('the login failed')

    monkeypatch.setattr('signup.views.login', fail)
    with pytest.raises(RuntimeError):
        _complete(client)
    assert not User.objects.exists()
    assert not OutboxEvent.objects.exists()


def test_an_emit_in_a_rolled_back_transaction_leaves_nothing():
    user = doctor()
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            client_sync.emit(user)
            raise RuntimeError('the caller failed')
    user.refresh_from_db()
    assert user.sync_version == 0 and not OutboxEvent.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_emit_outside_a_transaction_is_refused():
    user = doctor()
    with pytest.raises(client_sync.ClientSyncError):
        client_sync.emit(user)
    assert not OutboxEvent.objects.exists()


# --- Matrix: the Django admin -------------------------------------------------------------

def test_an_admin_change_sends_the_next_version(staff_client):
    user = doctor()
    emit(user)
    url = f'/admin/accounts/user/{user.pk}/change/'
    response = staff_client.post(url, change_form(user, first_name='Beatriz', country='DE',
                                                  is_active=None))
    assert response.status_code == 302
    user.refresh_from_db()
    assert (user.first_name, user.is_active, user.sync_version) == ('Beatriz', False, 2)
    assert events(user) == [('client.updated', 1), ('client.updated', 2)]
    payload = last_payload(user)
    assert (payload['first_name'], payload['country'], payload['active']) == (
        'Beatriz', 'DE', False)
    assert 'how_found_other' not in payload and OTHER_TEXT not in str(payload)
    assert payload['how_found'] == 'others'


def test_an_admin_add_sends_client_updated(staff_client):
    response = staff_client.post('/admin/accounts/user/add/', {
        'email': 'new@example.com', 'usable_password': 'true',
        'password1': PASSWORD, 'password2': PASSWORD})
    assert response.status_code == 302
    user = User.objects.by_email('new@example.com').get()
    assert events(user) == [('client.updated', 1)]
    assert last_payload(user) == {
        'title': '', 'first_name': '', 'middle_name': '', 'last_name': '',
        'email': 'new@example.com', 'phone': '', 'country': '', 'home_region': 'eu',
        'active': True, 'joined_at': last_payload(user)['joined_at'], 'how_found': ''}


def test_an_ordinary_save_never_writes_back_an_old_sync_version():
    user = doctor()
    stale = User.objects.get(pk=user.pk)  # loaded before the sync below
    emit(user)
    stale.first_name = 'Changed'
    stale.save()
    stale.refresh_from_db()
    assert (stale.first_name, stale.sync_version) == ('Changed', 1)
    assert emit(stale) == 'client.updated'
    assert events(user) == [('client.updated', 1), ('client.updated', 2)]


# --- Matrix: erasure ------------------------------------------------------------------------

def test_erase_sends_client_erased_in_its_transaction():
    staff = User.objects.create_superuser('staff@example.net', PASSWORD)
    user = doctor()
    emit(user)
    assert erase_user(user, by=staff) is True
    user.refresh_from_db()
    assert user.sync_version == 2
    # v1 was still pending: dropped, only the tombstone goes.
    assert events(user) == [('client.erased', 2)]
    assert last_payload(user) == {}
    # Already erased: nothing more.
    assert erase_user(user, by=staff) is False
    assert events(user) == [('client.erased', 2)]


def test_a_failed_erasure_sends_nothing(monkeypatch):
    staff = User.objects.create_superuser('staff@example.net', PASSWORD)
    user = doctor()

    def fail(*args, **kwargs):
        raise RuntimeError('the log failed')

    monkeypatch.setattr('accounts.erasure.LogEntry.objects.log_actions', fail)
    with pytest.raises(RuntimeError):
        erase_user(user, by=staff)
    user.refresh_from_db()
    assert not user.is_erased and user.sync_version == 0
    assert not OutboxEvent.objects.exists()


def test_an_erasure_drops_the_pending_updates_first():
    staff = User.objects.create_superuser('staff@example.net', PASSWORD)
    user = doctor()
    emit(user)
    emit(user)
    OutboxEvent.objects.filter(aggregate_id=user.uid, aggregate_version=1).update(
        state=OutboxEvent.DELIVERED)
    erase_user(user, by=staff)
    # v1 was delivered (kept); v2 was still pending (deleted): only the tombstone goes.
    assert events(user) == [('client.updated', 1), ('client.erased', 3)]


def test_erasing_an_account_never_sent_records_nothing():
    staff = User.objects.create_superuser('staff@example.net', PASSWORD)
    user = doctor()
    assert erase_user(user, by=staff) is True
    user.refresh_from_db()
    assert user.is_erased and user.sync_version == 0
    assert not OutboxEvent.objects.exists()


def test_the_purge_sends_nothing_and_deletes_the_accounts_outbox_rows():
    from datetime import timedelta

    from django.utils import timezone
    staff = User.objects.create_superuser('staff@example.net', PASSWORD)
    user = doctor()
    other = doctor('other@example.com')
    emit(user)
    emit(other)
    OutboxEvent.objects.filter(aggregate_id=user.uid).update(state=OutboxEvent.DELIVERED)
    erase_user(user, by=staff, now=timezone.now() - timedelta(days=31))
    OutboxEvent.objects.filter(aggregate_id=user.uid, type='client.erased').update(
        state=OutboxEvent.REJECTED)
    assert events(user) == [('client.updated', 1), ('client.erased', 2)]
    assert purge_erased() == 1
    assert not OutboxEvent.objects.filter(aggregate_id=user.uid).exists()
    assert events() == [('client.updated', 1)]  # the other account's, untouched


# --- Matrix: writes that are not client writes ------------------------------------------------

def test_password_login_and_reencryption_send_nothing(client):
    user = doctor()
    user.set_password('another-long-passphrase-77')
    user.save(update_fields=['password'])
    assert client.login(username='bea@example.com', password='another-long-passphrase-77')
    call_command('reencrypt_fields', stdout=io.StringIO())
    user.refresh_from_db()
    assert user.sync_version == 0
    assert not OutboxEvent.objects.exists()


# --- Matrix: staff ----------------------------------------------------------------------------

def test_a_superuser_is_never_sent(staff_client):
    staff = User.objects.create_superuser('root@example.net', PASSWORD)
    assert emit(staff) is None
    response = staff_client.post(f'/admin/accounts/user/{staff.pk}/change/',
                                 change_form(staff, first_name='Root', is_superuser='on'))
    assert response.status_code == 302
    staff.refresh_from_db()
    assert staff.sync_version == 0
    assert events(staff) == []


def test_a_client_made_staff_is_sent_as_erased_and_back_as_updated(staff_client):
    user = doctor()
    emit(user)
    url = f'/admin/accounts/user/{user.pk}/change/'
    assert staff_client.post(url, change_form(user, is_staff='on')).status_code == 302
    assert events(user) == [('client.erased', 2)]  # the pending v1 dropped
    user.refresh_from_db()
    assert staff_client.post(url, change_form(user, is_staff=None)).status_code == 302
    assert events(user) == [('client.erased', 2), ('client.updated', 3)]


def test_a_staff_account_never_sent_stays_unsent(staff_client):
    staff = User.objects.create_user('team@example.net', PASSWORD, is_staff=True)
    assert emit(staff) is None
    url = f'/admin/accounts/user/{staff.pk}/change/'
    assert staff_client.post(url, change_form(staff, first_name='Team')).status_code == 302
    staff.refresh_from_db()
    assert (staff.first_name, staff.sync_version) == ('Team', 0)
    assert not OutboxEvent.objects.exists()


# --- Concurrency (PostgreSQL only) --------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_two_concurrent_changes_get_distinct_versions():
    if connection.vendor != 'postgresql':
        pytest.skip('row locks need PostgreSQL')
    from django.db import connections
    user = doctor()
    barrier = threading.Barrier(4)
    errors = []

    def run(name):
        try:
            barrier.wait()
            with transaction.atomic():
                account = User.objects.get(pk=user.pk)
                account.first_name = name
                account.save()
                client_sync.emit(account)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=run, args=(f'n{index}',)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    versions = sorted(version for _type, version in events(user))
    assert versions == [1, 2, 3, 4]
    user.refresh_from_db()
    assert user.sync_version == 4
    # The highest version carries the state that committed last.
    newest = OutboxEvent.objects.filter(aggregate_id=user.uid).order_by(
        '-aggregate_version').first()
    assert newest.payload['first_name'] == user.first_name


# --- Re-sync ----------------------------------------------------------------------------------

def resync(client, body, key=ADMIN, issuer='admindev'):
    data = body if isinstance(body, bytes) else interface.json_body(body)
    token = interface.sign_request('POST', RESYNC, data, audience='dev', issuer=issuer,
                                   signing_key=key)
    return client.post(RESYNC, data=data, content_type='application/json',
                       HTTP_AUTHORIZATION=interface.authorization_header(token))


def resync_tasks():
    from django_tasks_db.models import DBTaskResult
    return DBTaskResult.objects.filter(task_path='integration.tasks.resync_client_task')


def test_a_resync_is_202_and_queues_the_task(client, caplog):
    caplog.set_level(logging.INFO)
    user = doctor()
    emit(user)
    response = resync(client, {'aggregate_id': str(user.uid)})
    assert response.status_code == 202
    assert response.json() == {'status': 'queued'}
    task = resync_tasks().get()
    assert task.args_kwargs == {'args': [str(user.uid)], 'kwargs': {}}
    assert events(user) == [('client.updated', 1)]  # sent by the worker, not the request
    assert f'client resync from admindev: {user.uid} queued' in caplog.text


@pytest.mark.django_db(transaction=True)
def test_a_resync_through_the_worker_sends_the_current_state(client):
    user = doctor()
    emit(user)
    assert resync(client, {'aggregate_id': str(user.uid)}).status_code == 202
    call_command('db_worker', '--batch', '--no-startup-delay', '--no-reload', verbosity=0)
    assert resync_tasks().get().return_value == 'client.updated'
    assert events(user) == [('client.updated', 1), ('client.updated', 2)]
    assert last_payload(user)['first_name'] == 'Bea'


def test_a_resync_of_an_erased_account_sends_client_erased_again():
    from integration.tasks import resync_client_task
    staff = User.objects.create_superuser('staff@example.net', PASSWORD)
    user = doctor()
    emit(user)
    erase_user(user, by=staff)
    assert resync_client_task.call(str(user.uid)) == 'client.erased'
    assert events(user) == [('client.erased', 2), ('client.erased', 3)]


def test_a_resync_of_an_erased_account_never_sent_is_404(client):
    staff = User.objects.create_superuser('staff@example.net', PASSWORD)
    user = doctor()
    erase_user(user, by=staff)
    response = resync(client, {'aggregate_id': str(user.uid)})
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'unknown_client'
    assert not resync_tasks().exists()


def test_the_resync_task_of_a_purged_account(caplog):
    from integration.tasks import resync_client_task
    assert resync_client_task.call(str(uuid.uuid4())) == 'unknown'


def test_a_resync_of_an_unknown_uid_or_a_staff_account_is_404(client):
    response = resync(client, {'aggregate_id': str(uuid.uuid4())})
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'unknown_client'
    staff = User.objects.create_superuser('root@example.net', PASSWORD)
    assert resync(client, {'aggregate_id': str(staff.uid)}).status_code == 404
    assert not resync_tasks().exists()


@pytest.mark.parametrize('body', [b'not json', b'[]', {'aggregate_id': 'nope'},
                                  {'aggregate_id': 'AAAAAAAA-0000-4000-8000-000000000000'},
                                  {}, {'aggregate_id': 7},
                                  {'aggregate_id': '3f2b8c4d-1e6a-4b7c-9d8e-0a1b2c3d4e5f',
                                   'extra': 1}])
def test_a_resync_with_a_bad_body_is_400(client, body):
    response = resync(client, body)
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'invalid_body'
    assert not resync_tasks().exists()


def test_a_resync_must_be_signed_by_the_admin_site(client, settings):
    user = doctor()
    body = {'aggregate_id': str(user.uid)}
    assert client.post(RESYNC, data=interface.json_body(body),
                       content_type='application/json').status_code == 401
    other = interface.SigningKey('o1', OTHER_SEED)
    settings.INTERFACE_PEER_KEYS = (f'admindev:a1:{ADMIN.public_text},'
                                    f'dev-ae:o1:{other.public_text}')
    response = resync(client, body, key=other, issuer='dev-ae')
    assert response.status_code == 401
    assert response.json() == interface.refusal_body('unknown_key')
    assert client.get(RESYNC).status_code == 405
    assert not resync_tasks().exists()


# --- Backfill ---------------------------------------------------------------------------------

def test_sync_clients_all_sends_every_account_once(caplog):
    caplog.set_level(logging.INFO)
    staff = User.objects.create_superuser('staff@example.net', PASSWORD)
    bea = doctor()
    carl = doctor('carl@example.com', first_name='Carl')
    erased = doctor('dan@example.com')
    emit(erased)
    erase_user(erased, by=staff)
    never_sent = doctor('eve@example.com')
    erase_user(never_sent, by=staff)
    OutboxEvent.objects.all().delete()
    out = io.StringIO()
    call_command('sync_clients', '--all', stdout=out)
    assert out.getvalue() == ('sync_clients: 2 client.updated, 1 client.erased, '
                              '2 skipped (not sent: staff or erased)\n')
    assert events(bea) == [('client.updated', 1)]
    assert events(carl) == [('client.updated', 1)]
    assert events(erased) == [('client.erased', 3)]
    assert events(never_sent) == []
    assert events(staff) == []
    assert 'carl@example.com' not in caplog.text and 'Carl' not in out.getvalue()


def test_sync_clients_by_uid():
    bea = doctor()
    out = io.StringIO()
    call_command('sync_clients', str(bea.uid), stdout=out)
    assert events(bea) == [('client.updated', 1)]
    unknown = str(uuid.uuid4())
    with pytest.raises(CommandError, match=f'unknown: nope, {unknown}'):
        call_command('sync_clients', str(bea.uid), unknown, 'nope', stdout=out)
    assert events(bea) == [('client.updated', 1), ('client.updated', 2)]
    with pytest.raises(CommandError, match='--all or one or more uids'):
        call_command('sync_clients', stdout=out)
    with pytest.raises(CommandError, match='--all or one or more uids'):
        call_command('sync_clients', '--all', str(bea.uid), stdout=out)


def test_sync_clients_goes_on_after_an_account_the_contract_refuses():
    bad = doctor()
    good = doctor('carl@example.com')
    User.objects.filter(pk=bad.pk).update(title='Dr.')  # not a choice code
    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError, match=f'failed: {bad.uid}'):
        call_command('sync_clients', '--all', stdout=out, stderr=err)
    assert f'{bad.uid} failed' in err.getvalue() and 'Bea' not in err.getvalue()
    assert events(good) == [('client.updated', 1)]
    assert events(bad) == []
    bad.refresh_from_db()
    assert bad.sync_version == 0


# --- User.save() never writes sync_version back -----------------------------------------------

def test_a_password_reset_save_keeps_the_sync_version():
    from accounts.password_reset import NewPasswordForm
    user = doctor()
    stale = User.objects.get(pk=user.pk)
    emit(user)
    form = NewPasswordForm(stale, data={})
    form.cleaned_data = {'password1': 'another-long-passphrase-77'}
    form.save()
    stale.refresh_from_db()
    assert stale.check_password('another-long-passphrase-77')
    assert stale.sync_version == 1
    assert events(user) == [('client.updated', 1)]


def test_erase_user_saves_everything_and_moves_the_version_only_through_emit():
    staff = User.objects.create_superuser('staff@example.net', PASSWORD)
    user = doctor()
    emit(user)
    stale = User.objects.get(pk=user.pk)  # sync_version 1, as loaded
    User.objects.filter(pk=user.pk).update(sync_version=5)  # e.g. later syncs
    assert erase_user(stale, by=staff) is True
    stale.refresh_from_db()
    assert stale.is_erased and not stale.is_active and not stale.has_usable_password()
    assert stale.how_found == '' and stale.email_index.startswith('erased:')
    assert stale.sync_version == 6
    assert events(user)[-1] == ('client.erased', 6)
