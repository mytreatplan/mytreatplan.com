"""The event log on its own: the API (start, record, end), the encrypted data column, the
client IP, failure handling, key rotation and the read-only admin."""
import io
import json
import re

import pytest
import sentry_sdk
from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.management import call_command
from django.db import DatabaseError, connection
from django.test import RequestFactory

from accounts import crypto
from accounts.crypto import EncryptedJSONField, encrypted_fields
from eventlog import service
from eventlog.models import Attempt, Event

pytestmark = pytest.mark.django_db

ENC_RE = re.compile(r'^enc:v1:(?P<key_id>[\w-]+):[A-Za-z0-9+/=]+$')
ROTATED_KEY = 'cm90YXRlZC10ZXN0LW9ubHkta2V5LW5vdC1zZWNyZXQ='  # b'rotated-test-only-key-not-secret'
EMAIL = 'ana@example.com'
IP = '203.0.113.9'
AGENT = 'Mozilla/5.0 (TestBrowser)'


def _request(**meta):
    meta.setdefault('HTTP_USER_AGENT', AGENT)
    return RequestFactory().post('/en/signup/', **meta)


def _raw_data(event):
    with connection.cursor() as cursor:
        cursor.execute('SELECT data FROM eventlog_event WHERE id = %s', [event.pk])
        return cursor.fetchone()[0]


# --- API ------------------------------------------------------------------------------------

def test_start_record_end():
    attempt = service.start_attempt('signup')
    assert attempt.kind == 'signup' and attempt.started_at and attempt.ended_at is None

    first = service.record(attempt, 'step_saved', step='account', data={'email': EMAIL})
    second = service.record(attempt.pk, 'rejected', step='verify-email', field='code',
                            reason='OTP verification failed')
    service.end_attempt(attempt, 'completed')

    attempt.refresh_from_db()
    assert attempt.outcome == 'completed' and attempt.ended_at >= attempt.started_at
    events = list(attempt.events.all())
    assert events == [first, second]
    assert (events[0].event, events[0].step, events[0].data) == (
        'step_saved', 'account', {'email': EMAIL})
    assert (events[1].field, events[1].reason, events[1].data) == (
        'code', 'OTP verification failed', None)


def test_record_without_attempt_does_nothing():
    assert service.record(None, 'step_saved') is None
    assert not Event.objects.exists()
    service.end_attempt(None, 'completed')


def test_get_attempt():
    attempt = service.start_attempt('signup')
    assert service.get_attempt(str(attempt.pk)) == attempt
    assert service.get_attempt(None) is None
    assert service.get_attempt('not-a-uuid') is None
    assert service.get_attempt('6f1c1d3e-0000-4000-8000-000000000001') is None


def test_request_adds_ip_and_user_agent():
    attempt = service.start_attempt('signup')
    event = service.record(attempt, 'code_sent', request=_request(REMOTE_ADDR='10.0.0.2'),
                           data={'channel': 'email'})
    assert Event.objects.get(pk=event.pk).data == {
        'channel': 'email', 'ip': '10.0.0.2', 'user_agent': AGENT}


@pytest.mark.parametrize('forwarded, expected', [
    (IP, IP),
    # The client can send its own header; Nginx appends the address it saw, last.
    (f'198.51.100.1, {IP}', IP),
    (f'198.51.100.1,{IP} ', IP),
    ('', '10.0.0.2'),
])
def test_client_ip_is_the_last_forwarded_entry(forwarded, expected):
    meta = {'REMOTE_ADDR': '10.0.0.2'}
    if forwarded:
        meta['HTTP_X_FORWARDED_FOR'] = forwarded
    assert service.client_ip(_request(**meta)) == expected


# --- Storage --------------------------------------------------------------------------------

def test_raw_row_holds_ciphertext_only():
    attempt = service.start_attempt('signup')
    event = service.record(attempt, 'rejected', request=_request(HTTP_X_FORWARDED_FOR=IP),
                           step='account', field='email', reason='email exists',
                           data={'email': EMAIL})
    stored = _raw_data(event)
    assert ENC_RE.match(stored)
    for plaintext in (EMAIL, IP, 'TestBrowser'):
        assert plaintext not in stored
    with connection.cursor() as cursor:
        cursor.execute('SELECT step, event, field, reason FROM eventlog_event')
        assert cursor.fetchone() == ('account', 'rejected', 'email', 'email exists')


def test_data_is_bound_to_its_column():
    """Associated data `eventlog.event.data`: ciphertext copied from another field fails."""
    field = Event._meta.get_field('data')
    assert isinstance(field, EncryptedJSONField)
    assert field.associated_data == 'eventlog.event.data'
    attempt = service.start_attempt('signup')
    event = service.record(attempt, 'step_saved', data={'email': EMAIL})
    with connection.cursor() as cursor:
        cursor.execute('UPDATE eventlog_event SET data = %s WHERE id = %s',
                       [crypto.encrypt(json.dumps({'email': EMAIL}), 'signup.x.data'), event.pk])
    with pytest.raises(crypto.DecryptionError):
        Event.objects.get(pk=event.pk)


def test_clearing_data_anonymizes_the_event():
    attempt = service.start_attempt('signup')
    event = service.record(attempt, 'step_saved', step='account', data={'email': EMAIL})
    Event.objects.filter(pk=event.pk).update(data=None)
    assert _raw_data(event) is None
    assert Event.objects.get(pk=event.pk).step == 'account'


def test_serialization_round_trip_keeps_data():
    attempt = service.start_attempt('signup')
    event = service.record(attempt, 'step_saved', data={'email': EMAIL})
    pk = event.pk
    dumped = serializers.serialize('json', [event])
    event.delete()

    [restored] = serializers.deserialize('json', dumped)
    restored.save()

    assert Event.objects.get(pk=pk).data == {'email': EMAIL}


def test_data_cannot_be_queried():
    with pytest.raises(crypto.EncryptedFieldLookupError):
        Event.objects.filter(data__email=EMAIL)


def test_reencrypt_fields_covers_event_data(settings):
    attempt = service.start_attempt('signup')
    event = service.record(attempt, 'step_saved', data={'email': EMAIL})
    assert encrypted_fields(Event) == [Event._meta.get_field('data')]

    settings.FIELD_ENCRYPTION_KEYS = f'test2:{ROTATED_KEY},{settings.FIELD_ENCRYPTION_KEYS}'
    out = io.StringIO()
    call_command('reencrypt_fields', stdout=out)

    assert ENC_RE.match(_raw_data(event))['key_id'] == 'test2'
    assert 'eventlog.event: 1 rows on key test2' in out.getvalue()
    assert EMAIL not in out.getvalue()
    settings.FIELD_ENCRYPTION_KEYS = f'test2:{ROTATED_KEY}'  # the old key removed
    assert Event.objects.get(pk=event.pk).data == {'email': EMAIL}


# --- A failing write ------------------------------------------------------------------------

def test_write_failure_is_reported_without_personal_values(sentry_capture, monkeypatch, caplog):
    attempt = service.start_attempt('signup')

    def fail(*args, **kwargs):
        raise DatabaseError(f'insert failed: Key (email)=({EMAIL}) from {IP}')

    monkeypatch.setattr(Event.objects, 'create', fail)
    result = service.record(attempt, 'step_saved', request=_request(HTTP_X_FORWARDED_FOR=IP),
                            step='account', data={'email': EMAIL})
    sentry_sdk.flush()

    assert result is None
    [event] = sentry_capture.events
    text = json.dumps(event)
    assert 'step_saved' in text and 'DatabaseError' in text
    for secret in (EMAIL, IP, 'TestBrowser'):
        assert secret not in text
        assert secret not in caplog.text
    # The database connection is still usable (the failure was rolled back to a savepoint).
    assert Attempt.objects.filter(pk=attempt.pk).exists()


def test_start_and_end_failures_are_caught(monkeypatch):
    def fail(*args, **kwargs):
        raise DatabaseError('boom')

    monkeypatch.setattr(Attempt.objects, 'create', fail)
    assert service.start_attempt('signup') is None
    monkeypatch.undo()

    attempt = service.start_attempt('signup')
    monkeypatch.setattr('eventlog.models.Attempt.objects', type(
        'Broken', (), {'filter': staticmethod(fail)})())
    service.end_attempt(attempt, 'completed')  # no exception
    assert attempt.ended_at is None


# --- Admin ----------------------------------------------------------------------------------

@pytest.fixture
def admin_client(client):
    admin = get_user_model().objects.create_superuser('admin@example.com',
                                                      'admin-password-long-123')
    client.force_login(admin)
    return client


def test_admin_shows_events_in_order_read_only(admin_client):
    attempt = service.start_attempt('signup')
    service.record(attempt, 'step_saved', step='account', data={'email': EMAIL})
    service.record(attempt, 'code_sent', step='verify-email', data={'channel': 'email'})
    service.record(attempt, 'code_accepted', step='verify-email')
    service.record(attempt, 'completed', step='verify-email')
    service.end_attempt(attempt, 'completed')

    url = f'/admin/eventlog/attempt/{attempt.pk}/change/'
    html = admin_client.get(url).content.decode()

    events = re.findall(r'<td class="field-event">\s*<p>\s*(\w+)', html)
    assert events == ['step_saved', 'code_sent', 'code_accepted', 'completed']
    assert len(re.findall(r'<td class="field-created_at">', html)) == 4  # with their times
    assert EMAIL in html  # decrypted for the admin
    assert 'name="_save"' not in html  # view only
    assert admin_client.post(url, {}).status_code == 403
    assert admin_client.get('/admin/eventlog/attempt/add/').status_code == 403
    assert admin_client.get(f'/admin/eventlog/attempt/{attempt.pk}/delete/').status_code == 403
    assert admin_client.get('/admin/eventlog/attempt/').status_code == 200
    assert admin_client.get('/admin/eventlog/event/').status_code == 404  # inline only
