"""The contact message sync (epic-admin-interface entry 9): `contact_message.updated`
recorded in the transaction that stores a message, `contact_message.deleted` from the
Django admin's delete, the signed re-sync endpoint and its task, and the backfill command
`sync_contact_messages`. Events land in the outbox (integration.outbox); no test reaches
the network or a KMS (fixed, non-secret test seeds)."""
import base64
import io
import json
import logging
import smtplib
import time
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.core.mail import EmailMessage
from django.core.management import CommandError, call_command
from django.db import transaction
from django.test import Client

from integration.models import OutboxEvent
from mytp_core import interface
from mytp_core.contracts import ContactMessageUpdated, check_central_payload
from mytp_publicsite import contact, contact_sync
from mytp_publicsite.models import ContactMessage, MailStatus

pytestmark = pytest.mark.django_db

ADMIN_SEED = b'test-only-interface-seed-admin!!'
PORTAL_SEED = b'test-only-interface-seed-portal!'
OTHER_SEED = b'test-only-interface-seed-other!!'
ADMIN = interface.SigningKey('a1', ADMIN_SEED)
RESYNC = '/api/v1/contact-messages/resync'
INBOX = 'webform@example.test'
VALID = {
    'first_name': 'Marisol',
    'middle_name': 'Q.',
    'surname': 'Quintero-Secret',
    'country': 'Secretland',
    'phone': '+34 (611) 222-333',
    'email': 'marisol.secret@example.com',
    'aligners': 'yes',
    'decision': 'no',
}
# What must never reach a log, a report, a FailedEvent or a task.
VALUES = ('Marisol', 'Quintero', 'Secretland', '222-333', 'marisol.secret@example.com')


@pytest.fixture(autouse=True)
def interface_settings(settings, monkeypatch):
    settings.SITE = 'dev'
    settings.REGION = 'eu'
    settings.CONTACT_INBOX = INBOX
    settings.INTERFACE_SIGNING_KEY = f'p1:{base64.b64encode(PORTAL_SEED).decode()}'
    settings.INTERFACE_PEERS = 'admindev=https://admindev.example.test'
    settings.INTERFACE_PEER_KEYS = f'admindev:a1:{ADMIN.public_text}'
    settings.INTERFACE_ADMIN_SITE = ''
    monkeypatch.setattr(contact, 'in_background', lambda function: function())
    interface.reset_signing_key()
    yield settings
    interface.reset_signing_key()


@pytest.fixture
def post(client, django_capture_on_commit_callbacks):
    """POST the contact form as the page's script would, the on-commit callbacks run."""
    def send(fields=None, ip='203.0.113.7'):
        data = {**VALID, **(fields or {}), 'cf_js': '1', 'website': '',
                'cf_ts': contact.make_stamp(time.time() - 10)}
        with django_capture_on_commit_callbacks(execute=True):
            return client.post('/en/contact/', data, REMOTE_ADDR=ip,
                               HTTP_HX_REQUEST='true')
    return send


def events(message=None):
    rows = OutboxEvent.objects.order_by('id')
    if message is not None:
        rows = rows.filter(aggregate_id=message.uid)
    return [(row.type, row.aggregate_version) for row in rows]


def stored(**fields):
    """A message stored outside the sync (as before this release), sync_version 0."""
    return ContactMessage.objects.create(**{**VALID, **fields})


def emit(message):
    with transaction.atomic():
        return contact_sync.emit(message)


@pytest.fixture
def staff_client():
    user = get_user_model().objects.create_superuser('staff@example.net',
                                                     'amber-glacier-meadow-77')
    browser = Client()
    browser.force_login(user)
    return browser


# --- Matrix: sent -----------------------------------------------------------------------

def test_a_sent_message_records_its_copy_and_mails_the_inbox_once(post, mailoutbox,
                                                                  caplog):
    caplog.set_level(logging.DEBUG)
    post()
    message = ContactMessage.objects.get()
    assert message.sync_version == 1
    assert events() == [('contact_message.updated', 1)]
    event = OutboxEvent.objects.get()
    assert (event.aggregate_id, event.region) == (message.uid, 'eu')
    assert event.payload == {
        'first_name': 'Marisol', 'middle_name': 'Q.', 'surname': 'Quintero-Secret',
        'email': 'marisol.secret@example.com', 'phone': '+34 (611) 222-333',
        'country': 'Secretland', 'aligners': 'yes', 'club': '', 'decision': 'no',
        'sent_at': event.payload['sent_at']}
    assert ContactMessageUpdated.model_validate(event.payload).sent_at == message.sent_at
    assert check_central_payload(event.payload)
    assert 'mail_status' not in event.payload
    # The inbox mail is unchanged: sent once.
    assert len(mailoutbox) == 1 and mailoutbox[0].to == [INBOX]
    assert message.mail_status == MailStatus.SENT
    assert 'outbox event recorded' in caplog.text
    for value in VALUES:
        assert value not in caplog.text


def test_the_email_does_not_gate_the_copy(post, mailoutbox, monkeypatch, caplog):
    def refuse(self, *args, **kwargs):
        raise smtplib.SMTPException('down')
    monkeypatch.setattr(EmailMessage, 'send', refuse)
    post()
    message = ContactMessage.objects.get()
    assert message.mail_status == MailStatus.FAILED
    assert events() == [('contact_message.updated', 1)]
    assert OutboxEvent.objects.get().state == OutboxEvent.PENDING


def test_a_failed_save_records_nothing_and_mails_nothing(monkeypatch, mailoutbox,
                                                         django_capture_on_commit_callbacks):
    class Boom(Exception):
        pass

    def fail(message):
        raise Boom
    # A failure after the row is written (not a contract refusal): both roll back.
    monkeypatch.setattr(contact_sync, 'emit', fail)
    with django_capture_on_commit_callbacks(execute=True), pytest.raises(Boom):
        contact.accept(VALID)
    assert not ContactMessage.objects.exists()
    assert events() == [] and mailoutbox == []


def test_a_failing_outbox_callback_after_commit_does_not_drop_the_mail(
        monkeypatch, mailoutbox, django_capture_on_commit_callbacks):
    from integration import outbox

    def broken_nudge():
        raise RuntimeError('task queue down')
    monkeypatch.setattr(outbox, '_enqueue_nudge', broken_nudge)
    with pytest.raises(RuntimeError), django_capture_on_commit_callbacks(execute=True):
        contact.accept(VALID)
    assert len(mailoutbox) == 1 and mailoutbox[0].to == [INBOX]
    assert ContactMessage.objects.get().mail_status == MailStatus.SENT
    assert events() == [('contact_message.updated', 1)]


def test_an_unrelated_value_error_in_emit_rolls_back_the_message(
        monkeypatch, mailoutbox, django_capture_on_commit_callbacks):
    def broken(message):
        raise ValueError('not a contract refusal')
    monkeypatch.setattr(contact_sync, 'payload_of', broken)
    with django_capture_on_commit_callbacks(execute=True), pytest.raises(ValueError):
        contact.accept(VALID)
    assert not ContactMessage.objects.exists()
    assert events() == [] and mailoutbox == []


def test_an_event_the_outbox_refuses_for_its_size_keeps_the_message(
        monkeypatch, mailoutbox, sentry_capture, django_capture_on_commit_callbacks):
    from integration import outbox

    def too_large(*args, **kwargs):
        raise outbox.OutboxError('the event is larger than 262144 bytes')
    monkeypatch.setattr(outbox, 'record', too_large)
    with django_capture_on_commit_callbacks(execute=True):
        contact.accept(VALID)
    message = ContactMessage.objects.get()
    assert message.sync_version == 0 and events() == []
    assert len(mailoutbox) == 1
    reports = [event.get('logentry', {}).get('message') or event.get('message')
               for event in sentry_capture.events]
    assert reports == ['Contact form error: sync (OutboxError)']


def test_an_accept_in_a_rolled_back_transaction_leaves_nothing():
    with pytest.raises(RuntimeError), transaction.atomic():
        contact.accept(VALID)
        assert events() == [('contact_message.updated', 1)]
        raise RuntimeError
    assert not ContactMessage.objects.exists() and events() == []


def test_a_message_the_contract_refuses_is_kept_and_mailed_without_its_copy(
        post, mailoutbox, sentry_capture, caplog):
    # An email the form accepts but the central checks refuse (a signed-link marker).
    post({'email': 'x-amz-signature@example.com'})
    message = ContactMessage.objects.get()
    assert message.sync_version == 0 and events() == []
    assert len(mailoutbox) == 1
    reports = [event.get('logentry', {}).get('message') or event.get('message')
               for event in sentry_capture.events]
    assert reports == ['Contact form error: sync (ContractViolation)']
    text = json.dumps(sentry_capture.events, default=str) + caplog.text
    for value in VALUES[:-1] + ('x-amz-signature@example.com',):
        assert value not in text


@pytest.mark.django_db(transaction=True)
def test_emit_and_delete_outside_a_transaction_are_refused():
    message = stored()
    with pytest.raises(contact_sync.ContactSyncError):
        contact_sync.emit(message)
    with pytest.raises(contact_sync.ContactSyncError):
        contact_sync.delete([message])
    assert events() == [] and ContactMessage.objects.exists()


# --- Matrix: deleted ----------------------------------------------------------------------

def test_an_admin_delete_sends_the_tombstone_and_drops_the_pending_update(post,
                                                                          staff_client):
    post()
    message = ContactMessage.objects.get()
    assert OutboxEvent.objects.get().state == OutboxEvent.PENDING
    response = staff_client.post(
        f'/admin/mytp_publicsite/contactmessage/{message.pk}/delete/', {'post': 'yes'})
    assert response.status_code == 302
    assert not ContactMessage.objects.exists()
    # The update not sent yet is gone: the admin site gets the tombstone only.
    assert events() == [('contact_message.deleted', 2)]
    event = OutboxEvent.objects.get()
    assert (event.aggregate_id, event.payload) == (message.uid, {})


def test_a_delivered_update_stays_and_the_tombstone_follows(staff_client):
    message = stored()
    emit(message)
    OutboxEvent.objects.update(state=OutboxEvent.DELIVERED)
    staff_client.post(f'/admin/mytp_publicsite/contactmessage/{message.pk}/delete/',
                      {'post': 'yes'})
    assert events() == [('contact_message.updated', 1), ('contact_message.deleted', 2)]


def test_a_message_never_sent_is_deleted_without_an_event(staff_client):
    message = stored()
    staff_client.post(f'/admin/mytp_publicsite/contactmessage/{message.pk}/delete/',
                      {'post': 'yes'})
    assert not ContactMessage.objects.exists() and events() == []


def test_a_bulk_delete_sends_one_tombstone_per_sent_message(staff_client):
    sent = [stored(first_name=f'Sent{index}') for index in range(3)]
    for message in sent:
        emit(message)
    emit(sent[2])  # re-sent once: v2
    never = stored(first_name='Never')
    kept = stored(first_name='Kept')
    emit(kept)
    response = staff_client.post('/admin/mytp_publicsite/contactmessage/', {
        'action': 'delete_selected', 'post': 'yes',
        '_selected_action': [message.pk for message in (*sent, never)]})
    assert response.status_code == 302
    assert list(ContactMessage.objects.values_list('pk', flat=True)) == [kept.pk]
    assert [events(message) for message in sent] == [
        [('contact_message.deleted', 2)], [('contact_message.deleted', 2)],
        [('contact_message.deleted', 3)]]
    assert events(never) == []
    assert events(kept) == [('contact_message.updated', 1)]


def test_a_bulk_delete_failing_on_the_second_message_rolls_back_the_first(
        staff_client, monkeypatch):
    first, second = stored(first_name='First'), stored(first_name='Second')
    emit(first)
    emit(second)
    OutboxEvent.objects.update(state=OutboxEvent.DELIVERED)
    original = ContactMessage.delete
    deleted = []

    def fail_on_second(self, *args, **kwargs):
        if deleted:
            raise RuntimeError('database down')
        deleted.append(self.pk)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(ContactMessage, 'delete', fail_on_second)
    with pytest.raises(RuntimeError):
        staff_client.post('/admin/mytp_publicsite/contactmessage/', {
            'action': 'delete_selected', 'post': 'yes',
            '_selected_action': [first.pk, second.pk]})
    assert deleted == [first.pk]  # the first one was deleted, then rolled back
    assert set(ContactMessage.objects.values_list('pk', flat=True)) == {first.pk, second.pk}
    assert events(first) == [('contact_message.updated', 1)]
    assert events(second) == [('contact_message.updated', 1)]


def test_a_failed_delete_keeps_the_message_and_records_nothing(monkeypatch):
    message = stored()
    emit(message)
    original = ContactMessage.delete

    def fail(self, *args, **kwargs):
        raise RuntimeError('database down')
    monkeypatch.setattr(ContactMessage, 'delete', fail)
    with pytest.raises(RuntimeError), transaction.atomic():
        contact_sync.delete([message])
    monkeypatch.setattr(ContactMessage, 'delete', original)
    assert ContactMessage.objects.exists()
    assert events() == [('contact_message.updated', 1)]


# --- Re-sync ----------------------------------------------------------------------------

def resync(client, body, key=ADMIN, issuer='admindev'):
    data = body if isinstance(body, bytes) else interface.json_body(body)
    token = interface.sign_request('POST', RESYNC, data, audience='dev', issuer=issuer,
                                   signing_key=key)
    return client.post(RESYNC, data=data, content_type='application/json',
                       HTTP_AUTHORIZATION=interface.authorization_header(token))


def resync_tasks():
    from django_tasks_db.models import DBTaskResult
    return DBTaskResult.objects.filter(
        task_path='integration.tasks.resync_contact_message_task')


def test_a_resync_is_202_and_queues_the_task_with_the_uid_only(client, caplog):
    caplog.set_level(logging.INFO)
    message = stored()
    emit(message)
    response = resync(client, {'aggregate_id': str(message.uid)})
    assert response.status_code == 202
    assert response.json() == {'status': 'queued'}
    task = resync_tasks().get()
    assert task.args_kwargs == {'args': [str(message.uid)], 'kwargs': {}}
    assert events(message) == [('contact_message.updated', 1)]  # sent by the worker
    assert f'contact message resync from admindev: {message.uid} queued' in caplog.text


@pytest.mark.django_db(transaction=True)
def test_a_resync_through_the_worker_sends_the_state_again_at_the_next_version(client):
    message = stored()
    emit(message)
    assert resync(client, {'aggregate_id': str(message.uid)}).status_code == 202
    call_command('db_worker', '--batch', '--no-startup-delay', '--no-reload', verbosity=0)
    assert resync_tasks().get().return_value == 'contact_message.updated'
    assert events(message) == [('contact_message.updated', 1),
                               ('contact_message.updated', 2)]
    assert OutboxEvent.objects.latest('id').payload['country'] == 'Secretland'


def test_a_resync_of_a_message_never_sent_sends_it(client):
    from integration.tasks import resync_contact_message_task
    message = stored()
    assert resync(client, {'aggregate_id': str(message.uid)}).status_code == 202
    assert resync_contact_message_task.call(str(message.uid)) == 'contact_message.updated'
    assert events(message) == [('contact_message.updated', 1)]


def test_the_resync_task_of_a_deleted_message():
    from integration.tasks import resync_contact_message_task
    assert resync_contact_message_task.call(str(uuid.uuid4())) == 'unknown'


def test_a_resync_of_an_unknown_or_deleted_message_is_404(client, staff_client):
    response = resync(client, {'aggregate_id': str(uuid.uuid4())})
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'unknown_contact_message'
    message = stored()
    emit(message)
    staff_client.post(f'/admin/mytp_publicsite/contactmessage/{message.pk}/delete/',
                      {'post': 'yes'})
    assert resync(client, {'aggregate_id': str(message.uid)}).status_code == 404
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
    message = stored()
    body = {'aggregate_id': str(message.uid)}
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


@pytest.mark.parametrize('body', [b'not json', {'aggregate_id': 'nope'}, {}])
def test_another_signed_peer_with_a_bad_body_is_401_not_400(client, settings, body):
    other = interface.SigningKey('o1', OTHER_SEED)
    settings.INTERFACE_PEER_KEYS = (f'admindev:a1:{ADMIN.public_text},'
                                    f'dev-ae:o1:{other.public_text}')
    response = resync(client, body, key=other, issuer='dev-ae')
    assert response.status_code == 401
    assert response.json() == interface.refusal_body('unknown_key')
    assert not resync_tasks().exists()


# --- Backfill ---------------------------------------------------------------------------

def test_sync_contact_messages_all_sends_every_message_once(caplog):
    caplog.set_level(logging.INFO)
    first = stored()
    second = stored(first_name='Second')
    out = io.StringIO()
    call_command('sync_contact_messages', '--all', stdout=out)
    assert out.getvalue() == 'sync_contact_messages: 2 contact_message.updated\n'
    assert events(first) == [('contact_message.updated', 1)]
    assert events(second) == [('contact_message.updated', 1)]
    for value in VALUES:
        assert value not in caplog.text and value not in out.getvalue()


def test_sync_contact_messages_by_uid():
    message = stored()
    out = io.StringIO()
    call_command('sync_contact_messages', str(message.uid), stdout=out)
    assert events(message) == [('contact_message.updated', 1)]
    unknown = str(uuid.uuid4())
    with pytest.raises(CommandError, match=f'unknown: nope, {unknown}'):
        call_command('sync_contact_messages', str(message.uid), unknown, 'nope',
                     stdout=out)
    assert events(message) == [('contact_message.updated', 1),
                               ('contact_message.updated', 2)]
    with pytest.raises(CommandError, match='--all or one or more uids'):
        call_command('sync_contact_messages', stdout=out)
    with pytest.raises(CommandError, match='--all or one or more uids'):
        call_command('sync_contact_messages', '--all', str(message.uid), stdout=out)


def test_sync_contact_messages_goes_on_after_a_message_the_contract_refuses():
    bad = stored()
    good = stored(first_name='Good')
    ContactMessage.objects.filter(pk=bad.pk).update(aligners='maybe')  # not yes/no
    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError, match=f'failed: {bad.uid}'):
        call_command('sync_contact_messages', '--all', stdout=out, stderr=err)
    assert f'{bad.uid} failed' in err.getvalue()
    for value in VALUES:
        assert value not in err.getvalue()
    assert events(good) == [('contact_message.updated', 1)]
    assert events(bad) == []
    bad.refresh_from_db()
    assert bad.sync_version == 0
