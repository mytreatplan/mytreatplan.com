"""Login records (epic-login entry 4, CAP-3): every row of the plan's I/O matrix. Every
completed login, and every failed one from the second in a row onwards (wrong passwords
and wrong codes counted apart, accounts.login_failures), is an event-log record of kind
`login` tied to the account's pk."""
import json
from datetime import timedelta

import pytest
import sentry_sdk
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.utils import timezone

from accounts import login_failures
from accounts.erasure import erase_user, purge_erased
from eventlog import retention
from eventlog.models import Attempt, Event
from signup.tests.conftest import capture_codes, clear_cache  # noqa: F401 - autouse
from signup.tests.conftest import clock  # noqa: F401 - fixture
from signup.tests.helpers import NORMALIZED, PASSWORD, _current_code, _wrong
from signup.verification import EMAIL, MAX_CHECK_ATTEMPTS, PURPOSE_LOGIN

from .test_login import _logged_in, _login, _login_with_code, doctor  # noqa: F401
from .test_login_code import login_clock  # noqa: F401 - fixture

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()

IP = '203.0.113.42'
AGENT = 'Mozilla/5.0 (LoginRecordsBrowser)'
META = {'REMOTE_ADDR': IP, 'HTTP_USER_AGENT': AGENT}
WRONG_PASSWORD = 'wrong-password-123'
UNKNOWN_TYPED = '  Nobody.Here@Example.COM '
UNKNOWN_NORMALIZED = 'nobody.here@example.com'
CODE_PAGE = '/en/login/code/'


@pytest.fixture
def records_clock(login_clock, monkeypatch):
    """The login clock, also seen by the failure counters."""
    monkeypatch.setattr('accounts.login_failures._now', login_clock)
    return login_clock


def _records(**filters):
    return list(Event.objects.filter(attempt__kind=Attempt.LOGIN, **filters)
                .select_related('attempt').order_by('created_at', 'id'))


def _failures():
    return _records(event='login_failed')


def _logins():
    return _records(event='login')


def _wrong_password(client, email=NORMALIZED):
    response = _login(client, email=email, password=WRONG_PASSWORD, extra=META)
    assert response.status_code == 200 and not _logged_in(client)
    return response


def _to_code(client):
    assert _login(client, extra=META)['Location'] == '/en/login/verify/'
    assert client.post('/en/login/verify/', {'contact': 'email'})['Location'] == CODE_PAGE


def _right_code():
    return _current_code(EMAIL, NORMALIZED, PURPOSE_LOGIN)


def _post_code(client, code, **data):
    return client.post(CODE_PAGE, {'code': code, **data}, **META)


def _count(counter, doctor):
    return login_failures.failure_count(counter, account_id=doctor.pk)


def _no_secrets(events, *secrets):
    """None of `secrets` (the passwords and codes submitted) in any column of `events`."""
    assert secrets and events
    for event in events:
        text = ' '.join([json.dumps(event.data), event.step, event.event, event.field,
                         event.reason, event.attempt.outcome, event.attempt.kind])
        for secret in secrets:
            assert secret not in text, f'{secret!r} leaked into {text!r}'


def _messages(sentry_capture):
    """The messages of the captured Sentry events (not their breadcrumbs)."""
    return [event.get('logentry', {}).get('message') or event.get('message')
            for event in sentry_capture.events]


# --- Login ------------------------------------------------------------------------------

def test_login_with_code_is_recorded_and_resets_the_counters(client, doctor, records_clock):
    _wrong_password(client)
    assert _count(login_failures.PASSWORD, doctor) == 1

    before = timezone.now()
    _to_code(client)
    response = _post_code(client, _right_code())

    assert response['Location'] == '/en/welcome/'
    [record] = _logins()
    assert record.attempt.account_id == doctor.pk
    assert record.attempt.ended_at is not None and record.attempt.outcome == 'login'
    assert record.created_at >= before
    assert (record.step, record.reason) == ('login-code', 'code')
    assert record.data == {'code_needed': True, 'ip': IP, 'user_agent': AGENT}
    assert _count(login_failures.PASSWORD, doctor) == 0
    assert _count(login_failures.CODE, doctor) == 0
    assert _failures() == []


def test_remembered_login_is_recorded_without_a_code(client, doctor, records_clock):
    _to_code(client)
    assert _post_code(client, _right_code(), remember='on')['Location'] == '/en/welcome/'
    client.post('/en/logout/')
    _wrong_password(client)
    assert _count(login_failures.PASSWORD, doctor) == 1

    response = _login(client, extra=META)

    assert response['Location'] == '/en/welcome/' and _logged_in(client)
    code_login, remembered = _logins()
    assert code_login.reason == 'code'
    assert remembered.attempt.account_id == doctor.pk
    assert (remembered.step, remembered.reason) == ('login', 'remembered_device')
    assert remembered.data == {'code_needed': False, 'ip': IP, 'user_agent': AGENT}
    assert _count(login_failures.PASSWORD, doctor) == 0


# --- Wrong passwords --------------------------------------------------------------------

def test_first_wrong_password_leaves_no_record(client, doctor):
    _wrong_password(client)

    assert _records() == []
    assert _count(login_failures.PASSWORD, doctor) == 1
    assert _count(login_failures.CODE, doctor) == 0


def test_second_and_later_wrong_passwords_are_recorded(client, doctor):
    for _ in range(3):
        _wrong_password(client)

    second, third = _failures()
    for record, count in ((second, 2), (third, 3)):
        assert record.attempt.account_id == doctor.pk
        assert (record.step, record.field, record.reason) == (
            'login', 'password', 'wrong_password')
        assert record.data == {'count': count, 'ip': IP, 'user_agent': AGENT}
    _no_secrets([second, third], WRONG_PASSWORD, PASSWORD)
    assert _count(login_failures.PASSWORD, doctor) == 3


# --- Wrong codes ------------------------------------------------------------------------

def test_wrong_codes_are_counted_apart_from_passwords(client, doctor):
    _wrong_password(client)
    _to_code(client)
    wrong = _wrong(_right_code())

    _post_code(client, wrong)
    assert _failures() == []
    _post_code(client, wrong)

    [record] = _failures()
    assert record.attempt.account_id == doctor.pk
    assert (record.step, record.field, record.reason) == ('login-code', 'code', 'wrong_code')
    assert record.data == {'count': 2, 'ip': IP, 'user_agent': AGENT}
    assert _count(login_failures.CODE, doctor) == 2
    assert _count(login_failures.PASSWORD, doctor) == 1
    _no_secrets(_records(), wrong, WRONG_PASSWORD, PASSWORD)


def test_the_fifth_code_counts_as_code_expired(client, doctor):
    _to_code(client)
    wrong = _wrong(_right_code())
    for _ in range(MAX_CHECK_ATTEMPTS):
        response = _post_code(client, wrong)

    # The 5th wrong code also locks the account (entry 5): back to the login page.
    assert response['Location'] == '/en/login/'
    records = _failures()
    _no_secrets(records, wrong, PASSWORD)
    assert [r.reason for r in records] == ['wrong_code'] * (MAX_CHECK_ATTEMPTS - 2) + [
        'code_expired']
    assert records[-1].field == 'code'
    assert records[-1].data['count'] == MAX_CHECK_ATTEMPTS


# --- Unknown email ----------------------------------------------------------------------

def test_unknown_email_is_recorded_from_the_second_failure(client, doctor):
    _wrong_password(client, email=UNKNOWN_TYPED)
    assert _records() == []

    _wrong_password(client, email=UNKNOWN_TYPED)

    [record] = _failures()
    assert record.attempt.account_id is None
    assert (record.step, record.field, record.reason) == ('login', 'password', 'unknown_email')
    assert record.data == {'count': 2, 'email': UNKNOWN_NORMALIZED, 'ip': IP,
                           'user_agent': AGENT}
    # Counted under the email's blind index, apart from the doctor's account.
    assert login_failures.failure_count(login_failures.PASSWORD, email=UNKNOWN_TYPED) == 2
    assert _count(login_failures.PASSWORD, doctor) == 0


# --- Reset and expiry -------------------------------------------------------------------

def test_a_login_resets_the_run(client, doctor, records_clock):
    _wrong_password(client)
    _login_with_code(client, extra=META)
    client.post('/en/logout/')

    _wrong_password(client)

    assert _failures() == []  # a "first" again
    assert _count(login_failures.PASSWORD, doctor) == 1


def test_a_run_expires_24_hours_after_its_last_failure(client, doctor, records_clock):
    _wrong_password(client)
    records_clock.advance(24 * 60 * 60 + 1)

    _wrong_password(client)

    assert _failures() == []
    assert _count(login_failures.PASSWORD, doctor) == 1


def test_a_run_within_24_hours_goes_on(client, doctor, records_clock):
    _wrong_password(client)
    records_clock.advance(24 * 60 * 60 - 1)

    _wrong_password(client)

    assert len(_failures()) == 1


# --- CAPTCHA unavailable ----------------------------------------------------------------

def test_unavailable_captcha_is_an_event_log_record(client, doctor, turnstile, caplog):
    turnstile.error = TimeoutError()

    assert _login(client, extra=META)['Location'] == '/en/login/verify/'

    [record] = _records(event='check_unavailable')
    assert record.attempt.account_id is None
    assert (record.step, record.field, record.reason) == ('login', 'captcha', 'turnstile')
    assert record.data == {'ip': IP, 'user_agent': AGENT}
    assert not [r for r in caplog.records if 'check_unavailable' in r.getMessage()]


# --- Erased account ---------------------------------------------------------------------

def test_records_outlive_the_purged_account(client, doctor, records_clock, settings):
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 30
    _wrong_password(client)
    _wrong_password(client)
    _login_with_code(client, extra=META)
    client.post('/en/logout/')
    pk = doctor.pk
    staff = User.objects.create_superuser('staff@example.net', PASSWORD)

    erase_user(User.objects.get(pk=pk), by=staff)
    assert purge_erased(now=User.objects.get(pk=pk).purge_after) == 1
    assert not User.objects.filter(pk=pk).exists()

    records = Event.objects.filter(attempt__account_id=pk).order_by('created_at', 'id')
    assert [r.event for r in records] == ['login_failed', 'login']


def test_erased_email_counts_as_unknown(client, doctor, settings):
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 30
    staff = User.objects.create_superuser('staff@example.net', PASSWORD)
    erase_user(doctor, by=staff)

    _wrong_password(client)
    _wrong_password(client)

    [record] = _failures()
    assert record.reason == 'unknown_email' and record.attempt.account_id is None


# --- A failing log write ----------------------------------------------------------------

def test_a_failing_log_write_never_stops_the_login(client, doctor, monkeypatch,
                                                   sentry_capture):
    def fail(*args, **kwargs):
        raise DatabaseError(f'insert failed: {NORMALIZED} from {IP}')

    monkeypatch.setattr(Event.objects, 'create', fail)
    _wrong_password(client)
    _wrong_password(client)
    response = _login_with_code(client, extra=META)
    sentry_sdk.flush()

    assert response['Location'] == '/en/welcome/' and _logged_in(client)
    assert _records() == []
    # No login attempt left without its event (none would ever be cleaned up).
    assert not Attempt.objects.filter(kind=Attempt.LOGIN).exists()
    assert len(sentry_capture.events) == 2  # the 2nd wrong password, the login
    text = json.dumps(sentry_capture.events)
    assert 'DatabaseError' in text and 'login_failed' in text
    for secret in (NORMALIZED, IP, AGENT, PASSWORD, WRONG_PASSWORD):
        assert secret not in text


def test_a_failing_counter_never_stops_the_login(client, doctor, monkeypatch,
                                                 sentry_capture):
    def fail(*args, **kwargs):
        raise DatabaseError(f'cache write failed for {NORMALIZED}')

    monkeypatch.setattr(login_failures, 'record_failure', fail)
    monkeypatch.setattr(login_failures, 'reset', fail)
    _wrong_password(client)
    response = _login_with_code(client, extra=META)
    sentry_sdk.flush()

    assert response['Location'] == '/en/welcome/' and _logged_in(client)
    assert len(_logins()) == 1
    assert _messages(sentry_capture) == [
        'Login failure counter error: record_failure (DatabaseError)',
        'Login failure counter error: reset (DatabaseError)']
    assert NORMALIZED not in json.dumps(sentry_capture.events)


def test_a_failing_account_lookup_still_shows_the_generic_error(client, doctor,
                                                                monkeypatch,
                                                                sentry_capture):
    """The view's own lookup of the account (only for the record) fails; the form's
    check of the credentials does not."""
    from types import SimpleNamespace

    from accounts import views
    from accounts.forms import INVALID_LOGIN_MESSAGE

    def fail(email):
        raise DatabaseError(f'lookup failed for {email}')

    _wrong_password(client)  # the next one would be recorded
    broken = SimpleNamespace(_default_manager=SimpleNamespace(by_email=fail))
    monkeypatch.setattr(views, 'get_user_model', lambda: broken)
    response = _login(client, password=WRONG_PASSWORD, extra=META)
    sentry_sdk.flush()

    assert response.status_code == 200 and not _logged_in(client)
    assert response.context['form'].non_field_errors() == [INVALID_LOGIN_MESSAGE]
    assert _failures() == []
    # The lockout's lookup (entry 5) fails first; the login goes on as without a lock.
    assert _messages(sentry_capture) == [
        'Login failure counter error: lock_lookup (DatabaseError)',
        'Login failure counter error: record_failure (DatabaseError)']
    text = json.dumps(sentry_capture.events)
    assert NORMALIZED not in text and IP not in text


# --- Retention --------------------------------------------------------------------------

def test_retention_leaves_login_records_alone(client, doctor):
    _wrong_password(client)
    _wrong_password(client)
    _login_with_code(client, extra=META)
    later = timezone.now() + timedelta(days=800)

    retention.anonymize(now=later)
    retention.delete_old(now=later)

    records = _records()
    assert [r.event for r in records] == ['login_failed', 'login']
    assert all(record.data and record.data['ip'] == IP for record in records)
    # Signup's attempts (the doctor's own signup) follow their rules as before.
    assert not Attempt.objects.filter(kind=Attempt.SIGNUP).exists()
