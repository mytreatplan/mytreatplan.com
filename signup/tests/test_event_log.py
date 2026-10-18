"""Story 2.3: the signup event log. Every row of the plan's I/O matrix, through the wizard."""
import json

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection

from eventlog.models import Attempt, Event
from signup.models import PendingRegistration
from signup.views import ATTEMPT_SESSION_KEY

from .helpers import (EMAIL_ADDRESS, ENCRYPTED, FIRST_NAME, LAST_NAME, MOBILE_E164,
                      NORMALIZED, PASSWORD, _code_from_log, _complete, _legal,
                      _mobile_code, _step1, _to_code_step, _to_mobile_step, _wrong)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

# Step 1's logged fields: every field but the passwords.
STEP1_LOGGED = {'title', 'first_name', 'middle_name', 'last_name', 'country', 'email',
                'phone_prefix', 'phone', 'how_found', 'how_found_other'}

User = get_user_model()

CLIENT_IP = '203.0.113.9'
AGENT = 'Mozilla/5.0 (EventLogTest)'


@pytest.fixture
def client(client):
    """Requests as they arrive through Nginx: a forwarded client IP and a user agent."""
    client.defaults.update(HTTP_X_FORWARDED_FOR=f'198.51.100.1, {CLIENT_IP}',
                           HTTP_USER_AGENT=AGENT)
    return client


def _events(attempt=None):
    attempt = attempt or Attempt.objects.get()
    return list(attempt.events.all())


def _summary(events):
    return [(e.event, e.step, e.field, e.reason) for e in events]


def _assert_request_data(event):
    assert event.data['ip'] == CLIENT_IP
    assert event.data['user_agent'] == AGENT


def _assert_no_secret(event, *secrets):
    text = json.dumps(event.data)
    for secret in secrets:
        assert secret not in text


# --- Happy path ------------------------------------------------------------------------

def test_happy_path(client, caplog):
    assert client.get('/en/signup/').status_code == 200
    assert not Attempt.objects.exists()  # a page view is not data received

    _step1(client)
    attempt = Attempt.objects.get()
    assert client.session[ATTEMPT_SESSION_KEY] == str(attempt.pk)
    assert PendingRegistration.objects.get().attempt == attempt
    _legal(client)
    code = _code_from_log(caplog)
    client.post('/en/signup/verify-email/', {'code': code})
    mobile_code = _code_from_log(caplog)
    client.post('/en/signup/verify-mobile/', {'code': mobile_code})

    events = _events()
    assert _summary(events) == [
        ('step_saved', 'account', '', ''),
        ('step_saved', 'legal', '', ''),
        ('code_sent', 'verify-email', '', ''),
        ('code_accepted', 'verify-email', '', ''),
        ('code_sent', 'verify-mobile', '', ''),
        ('code_accepted', 'verify-mobile', '', ''),
        ('completed', 'verify-mobile', '', ''),
    ]
    assert events[0].data['email'] == NORMALIZED
    assert set(events[0].data) == {*STEP1_LOGGED, 'ip', 'user_agent'}
    assert (events[0].data['first_name'], events[0].data['phone']) == (FIRST_NAME, MOBILE_E164)
    assert {k: v for k, v in events[1].data.items() if k not in ('ip', 'user_agent')} == {
        'terms': {'version': 1, 'accepted': True},
        'conditions': {'version': 1, 'accepted': True},
        'ormco_tps': {'version': 1, 'accepted': False},
        'commercial_consent': {'version': 1, 'accepted': False},
    }
    assert events[2].data['email'] == NORMALIZED and events[2].data['channel'] == 'email'
    assert 'resend' not in events[2].data and events[2].data['fallback'] is False
    assert events[4].data['phone'] == MOBILE_E164 and events[4].data['channel'] == 'whatsapp'
    assert events[4].data['fallback'] is False
    for event in events:
        _assert_request_data(event)
        _assert_no_secret(event, PASSWORD, code, mobile_code)
    assert [e.created_at for e in events] == sorted(e.created_at for e in events)

    attempt.refresh_from_db()
    assert attempt.outcome == 'completed' and attempt.ended_at >= events[-1].created_at
    # The attempt outlives the pending registration; the session forgets it.
    assert not PendingRegistration.objects.exists()
    assert ATTEMPT_SESSION_KEY not in client.session


def test_a_new_signup_after_completion_is_a_new_attempt(client, caplog):
    _complete(client)
    client.logout()

    _step1(client, email='bea@example.com')
    assert Attempt.objects.count() == 2
    assert Attempt.objects.filter(ended_at__isnull=True).count() == 1


def test_password_and_its_hash_are_never_logged(client, caplog):
    _step1(client, password2='a-different-password-1')  # mismatch
    _to_code_step(client)
    pending_hash = PendingRegistration.objects.get().password
    client.post('/en/signup/verify-email/', {'code': _code_from_log(caplog)})
    _mobile_code(client)
    for event in Event.objects.all():
        _assert_no_secret(event, PASSWORD, 'a-different-password-1', pending_hash,
                          pending_hash.split('$')[-1])
        assert not {'password', 'password1', 'password2'} & set(event.data)


# --- Registered email ------------------------------------------------------------------

def test_registered_email(client):
    User.objects.create_user(NORMALIZED, 'another-password-123')

    _step1(client)

    [event] = _events()
    assert (event.event, event.step, event.field, event.reason) == (
        'rejected', 'account', 'email', 'email exists')
    assert event.data['email'] == EMAIL_ADDRESS  # as received
    _assert_request_data(event)
    assert Attempt.objects.get().ended_at is None  # the attempt stays open

    # The next try continues the same attempt.
    _step1(client, email='bea@example.com')
    assert Attempt.objects.count() == 1
    assert [e.event for e in _events()] == ['rejected', 'step_saved']


# --- Weak or mismatched password -------------------------------------------------------

def test_mismatched_password(client):
    _step1(client, password2='a-different-password-1')

    [event] = _events()
    assert _summary([event]) == [('rejected', 'account', 'password2', 'password_mismatch')]
    assert event.data['email'] == NORMALIZED
    _assert_no_secret(event, PASSWORD, 'a-different-password-1')


def test_weak_password_logs_one_event_per_error(client):
    _step1(client, password='12345678')  # too short, too common and entirely numeric

    events = _events()
    assert {(e.event, e.step, e.field) for e in events} == {('rejected', 'account', 'password1')}
    assert sorted(e.reason for e in events) == [
        'password_entirely_numeric', 'password_too_common', 'password_too_short']
    for event in events:
        _assert_no_secret(event, '12345678')
        assert set(event.data) == {*STEP1_LOGGED, 'ip', 'user_agent'}


def test_invalid_email_logs_the_value_received(client):
    _step1(client, email='not-an-email')
    [event] = _events()
    assert _summary([event]) == [('rejected', 'account', 'email', 'invalid')]
    assert event.data['email'] == 'not-an-email'


# --- Wrong code, resend ----------------------------------------------------------------

def test_wrong_code(client, caplog):
    _to_code_step(client)
    code = _code_from_log(caplog)

    client.post('/en/signup/verify-email/', {'code': _wrong(code)})

    event = _events()[-1]
    assert _summary([event]) == [('rejected', 'verify-email', 'code', 'OTP verification failed')]
    _assert_no_secret(event, code, _wrong(code))
    assert set(event.data) == {'ip', 'user_agent'}


def test_resend(client, clock):
    _to_code_step(client)
    clock.advance(60)

    client.post('/en/signup/verify-email/', {'action': 'resend'})

    events = _events()
    assert [e.event for e in events] == ['step_saved', 'step_saved', 'code_sent', 'code_sent']
    assert events[-1].data['resend'] is True
    assert events[-1].data['email'] == NORMALIZED
    _assert_request_data(events[-1])


# --- Race at creation ------------------------------------------------------------------

def test_email_registered_meanwhile(client, caplog):
    _to_mobile_step(client)
    User.objects.create_user(NORMALIZED, 'another-password-123')

    _mobile_code(client)

    events = _events()
    assert _summary(events[-2:]) == [
        ('code_accepted', 'verify-mobile', '', ''),
        ('rejected', 'verify-mobile', 'email', 'email exists'),
    ]
    assert events[-1].data['email'] == NORMALIZED
    assert Attempt.objects.get().ended_at is None


# --- Log write fails -------------------------------------------------------------------

def test_signup_completes_when_the_log_cannot_be_written(client, caplog, monkeypatch):
    captured = []
    monkeypatch.setattr('eventlog.service.sentry_sdk.capture_message',
                        lambda message, **kwargs: captured.append(message))

    def fail(*args, **kwargs):
        raise DatabaseError(f'cannot insert {NORMALIZED} from {CLIENT_IP}')

    monkeypatch.setattr(Event.objects, 'create', fail)

    response = _complete(client)

    assert response['Location'] == '/en/welcome/'
    assert User.objects.get().email == NORMALIZED
    assert not Event.objects.exists()
    assert Attempt.objects.get().outcome == 'completed'
    assert any('step_saved' in message for message in captured)
    assert any('completed' in message for message in captured)
    for message in captured:
        assert NORMALIZED not in message and CLIENT_IP not in message


def test_signup_works_when_no_attempt_can_be_started(client, caplog, monkeypatch):
    monkeypatch.setattr('eventlog.service.sentry_sdk.capture_message', lambda *a, **k: None)

    def fail(*args, **kwargs):
        raise DatabaseError('down')

    monkeypatch.setattr(Attempt.objects, 'create', fail)

    _to_code_step(client)
    assert PendingRegistration.objects.get().attempt is None
    assert ATTEMPT_SESSION_KEY not in client.session
    client.post('/en/signup/verify-email/', {'code': _code_from_log(caplog)})
    assert _mobile_code(client)['Location'] == '/en/welcome/'


# --- Raw row ---------------------------------------------------------------------------

def test_raw_rows_hold_ciphertext(client, caplog):
    _complete(client)

    with connection.cursor() as cursor:
        cursor.execute('SELECT data FROM eventlog_event')
        rows = [row[0] for row in cursor.fetchall()]
    assert len(rows) == 7
    for stored in rows:
        assert ENCRYPTED.match(stored)
        for plaintext in (NORMALIZED, CLIENT_IP, 'EventLogTest', FIRST_NAME, LAST_NAME,
                          MOBILE_E164):
            assert plaintext not in stored


def test_pending_attempt_is_set_null_when_the_attempt_goes(client):
    _step1(client)
    Attempt.objects.all().delete()
    assert PendingRegistration.objects.get().attempt is None
