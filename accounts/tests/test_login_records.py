"""Login records (epic-login entry 4, CAP-3): every row of the plan's I/O matrix. Every
completed login, and every failed one from the second in a row onwards (wrong passwords
and wrong codes counted apart, accounts.login_failures), is an event-log record of kind
`login` tied to the account's pk."""
import json
from datetime import timedelta

import pytest
import sentry_sdk
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.utils import timezone

from accounts import login_failures
from accounts.erasure import erase_user, purge_erased
from conftest import _assert_no_secrets
from eventlog import retention
from eventlog.models import Attempt, Event
from signup.forms import BLOCKED_MESSAGE
from signup.tests.helpers import NORMALIZED, PASSWORD, _complete, _wrong
from signup.verification import MAX_CHECK_ATTEMPTS

from .helpers import (AGENT, IP, META, WRONG_PASSWORD, _deactivate, _failures, _logged_in,
                      _login, _login_with_code, _logins, _messages, _post_code, _records,
                      _right_code, _to_code, _wrong_password)

pytestmark = [pytest.mark.django_db,
              pytest.mark.usefixtures('capture_codes', 'clear_cache', 'portal_urls')]

User = get_user_model()

UNKNOWN_TYPED = '  Nobody.Here@Example.COM '
UNKNOWN_NORMALIZED = 'nobody.here@example.com'


def _count(counter, doctor):
    return login_failures.failure_count(counter, account_id=doctor.pk)


def _no_secrets(events, *secrets):
    """None of `secrets` (the passwords and codes submitted) in any column of `events`."""
    assert secrets and events
    _assert_no_secrets(secrets, *(' '.join([json.dumps(event.data), event.step, event.event,
                                            event.field, event.reason, event.attempt.outcome,
                                            event.attempt.kind]) for event in events),
                       show_leak=True)


# --- Login ------------------------------------------------------------------------------

def test_login_with_code_is_recorded_and_resets_the_counters(client, doctor, lock_clock):
    _wrong_password(client)
    assert _count(login_failures.PASSWORD, doctor) == 1

    before = timezone.now()
    _to_code(client, extra=META)
    response = _post_code(client, _right_code(), extra=META)

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


def test_remembered_login_is_recorded_without_a_code(client, doctor, lock_clock):
    _to_code(client, extra=META)
    response = _post_code(client, _right_code(), extra=META, remember='on')
    assert response['Location'] == '/en/welcome/'
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


# --- Signup's auto-login (QA A2) ----------------------------------------------------------

def test_signup_auto_login_is_recorded(client):
    before = timezone.now()
    client.defaults.update(META)

    response = _complete(client)

    assert response['Location'] == '/en/welcome/' and _logged_in(client)
    user = User.objects.get()
    [record] = _logins()
    assert record.attempt.account_id == user.pk
    assert record.attempt.outcome == 'login' and record.created_at >= before
    assert (record.step, record.field, record.reason) == ('signup', '', 'signup')
    assert record.data == {'code_needed': True, 'ip': IP, 'user_agent': AGENT}
    _no_secrets([record], PASSWORD)


def test_portal_login_is_recorded_once(client, doctor, lock_clock):
    _login_with_code(client, extra=META)

    [record] = _logins()
    assert record.reason == 'code'


def test_admin_login_is_not_a_signup_login(client, staff):
    response = client.post('/admin/login/', {'username': 'staff@example.net',
                                              'password': PASSWORD}, **META)

    assert response.status_code == 302 and _logged_in(client)
    assert _logins() == []


def test_forced_login_is_not_recorded(client, doctor):
    client.force_login(doctor)

    assert _logins() == []


def test_login_code_resend_logs_no_signup_event(client, doctor, login_clock):
    """QA A7 is the signup and resume pages' only: the login code page has no attempt."""
    _to_code(client, extra=META)
    login_clock.advance(60)
    before = set(Event.objects.values_list('pk', flat=True))

    assert client.post('/en/login/code/', {'action': 'resend'})['Location'] == '/en/login/code/'

    assert not Event.objects.exclude(pk__in=before).exists()
    assert not Event.objects.filter(reason='OTP not delivered').exists()


# --- Wrong passwords --------------------------------------------------------------------

def test_first_wrong_password_leaves_no_record(client, doctor):
    _wrong_password(client)

    assert _records() == []
    assert _count(login_failures.PASSWORD, doctor) == 1
    assert _count(login_failures.CODE, doctor) == 0


def test_a_failure_without_a_user_agent_is_recorded_with_an_empty_one(client, doctor):
    for _ in range(2):
        response = _login(client, password=WRONG_PASSWORD, extra={'REMOTE_ADDR': IP})
        assert response.status_code == 200 and not _logged_in(client)

    [record] = _failures()
    assert record.data == {'count': 2, 'ip': IP, 'user_agent': ''}


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
    _to_code(client, extra=META)
    wrong = _wrong(_right_code())

    _post_code(client, wrong, extra=META)
    assert _failures() == []
    _post_code(client, wrong, extra=META)

    [record] = _failures()
    assert record.attempt.account_id == doctor.pk
    assert (record.step, record.field, record.reason) == ('login-code', 'code', 'wrong_code')
    assert record.data == {'count': 2, 'ip': IP, 'user_agent': AGENT}
    assert _count(login_failures.CODE, doctor) == 2
    assert _count(login_failures.PASSWORD, doctor) == 1
    _no_secrets(_records(), wrong, WRONG_PASSWORD, PASSWORD)


def test_the_fifth_code_counts_as_code_expired(client, doctor):
    _to_code(client, extra=META)
    wrong = _wrong(_right_code())
    for _ in range(MAX_CHECK_ATTEMPTS):
        response = _post_code(client, wrong, extra=META)

    # The 5th wrong code also locks the account (entry 5): back to the login page.
    assert response['Location'] == '/en/login/'
    records = _failures()
    _no_secrets(records, wrong, PASSWORD)
    assert [r.reason for r in records] == ['wrong_code'] * (MAX_CHECK_ATTEMPTS - 2) + [
        'code_expired']
    assert records[-1].field == 'code'
    assert records[-1].data['count'] == MAX_CHECK_ATTEMPTS


# --- Deactivated account (QA A14) --------------------------------------------------------

def test_deactivated_account_right_password_is_counted_as_inactive(client, doctor):
    """QA A14, decision 2: counted like any failure (from the 2nd on recorded), with the
    reason `inactive_account`, and the generic error."""
    from accounts.forms import INVALID_LOGIN_MESSAGE

    _deactivate(doctor)

    for _ in range(3):
        response = _login(client, extra=META)
        assert response.status_code == 200 and not _logged_in(client)
        assert response.context['form'].non_field_errors() == [INVALID_LOGIN_MESSAGE]

    second, third = _failures()
    for record, count in ((second, 2), (third, 3)):
        assert record.attempt.account_id == doctor.pk
        assert (record.event, record.step, record.field, record.reason) == (
            'login_failed', 'login', 'password', 'inactive_account')
        assert record.data == {'count': count, 'ip': IP, 'user_agent': AGENT}
    _no_secrets([second, third], PASSWORD)
    assert _count(login_failures.PASSWORD, doctor) == 3
    # Its hash is untouched (checked without the setter).
    assert User.objects.get(pk=doctor.pk).password == doctor.password


@pytest.mark.parametrize('last', [PASSWORD, WRONG_PASSWORD])
def test_deactivated_account_locks_whichever_the_fifth_guess(client, doctor, last):
    """4 wrong passwords then the right one lock exactly as 5 wrong ones: the lockout
    cannot reveal the right guess."""
    _deactivate(doctor)
    for _ in range(login_failures.LOCK_THRESHOLD - 1):
        _wrong_password(client)

    response = _login(client, password=last, extra=META)

    assert response.status_code == 200 and not _logged_in(client)
    assert response.context['form'].non_field_errors() == [BLOCKED_MESSAGE]
    assert login_failures.is_locked(account_id=doctor.pk)
    [lock] = _records(event='account_locked')
    assert lock.attempt.account_id == doctor.pk
    assert lock.reason == ('inactive_account' if last == PASSWORD else 'wrong_password')
    assert lock.data['count'] == login_failures.LOCK_THRESHOLD


def test_deactivated_account_wrong_password_is_counted(client, doctor):
    _deactivate(doctor)

    _wrong_password(client)
    _wrong_password(client)

    [record] = _failures()
    assert (record.attempt.account_id, record.reason) == (doctor.pk, 'wrong_password')
    assert _count(login_failures.PASSWORD, doctor) == 2


def test_erased_account_right_password_counts_as_unknown(client, doctor, settings):
    """An erased account is not a deactivated one: its email is unknown (its password is
    unusable anyway)."""
    from .helpers import _erase

    _erase(doctor, settings)
    _login(client, extra=META)
    _login(client, extra=META)

    [record] = _failures()
    assert (record.attempt.account_id, record.reason) == (None, 'unknown_email')


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

def test_a_login_resets_the_run(client, doctor, lock_clock):
    _wrong_password(client)
    _login_with_code(client, extra=META)
    client.post('/en/logout/')

    _wrong_password(client)

    assert _failures() == []  # a "first" again
    assert _count(login_failures.PASSWORD, doctor) == 1


def test_a_run_expires_24_hours_after_its_last_failure(client, doctor, lock_clock):
    _wrong_password(client)
    lock_clock.advance(24 * 60 * 60 + 1)

    _wrong_password(client)

    assert _failures() == []
    assert _count(login_failures.PASSWORD, doctor) == 1


def test_a_run_within_24_hours_goes_on(client, doctor, lock_clock):
    _wrong_password(client)
    lock_clock.advance(24 * 60 * 60 - 1)

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

def test_records_outlive_the_purged_account(client, doctor, lock_clock, settings):
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
    # The purge leaves the account's own record (QA A6), tested in test_erasure.py.
    assert [r.event for r in records] == ['login_failed', 'login', 'account_purged']


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

    from accounts import login_guard
    from accounts.forms import INVALID_LOGIN_MESSAGE

    def fail(email):
        raise DatabaseError(f'lookup failed for {email}')

    _wrong_password(client)  # the next one would be recorded
    broken = SimpleNamespace(_default_manager=SimpleNamespace(by_email=fail))
    monkeypatch.setattr(login_guard, 'get_user_model', lambda: broken)
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

def test_retention_keeps_login_records_intact_for_5_years(client, doctor):
    """Epic-login entry 8: never anonymized, deleted 5 calendar years after they end."""
    _wrong_password(client)
    _wrong_password(client)
    _login_with_code(client, extra=META)
    later = timezone.now() + timedelta(days=800)

    retention.anonymize(now=later)
    retention.delete_old(now=later)
    almost = timezone.now() + relativedelta(years=5) - timedelta(days=1)
    assert retention.delete_old_activity(now=almost) == (0, 0)

    records = _records()
    assert [r.event for r in records] == ['login_failed', 'login']
    assert all(record.data and record.data['ip'] == IP
               and record.data['user_agent'] == AGENT for record in records)
    # Signup's attempts (the doctor's own signup) follow their rules as before.
    assert not Attempt.objects.filter(kind=Attempt.SIGNUP).exists()

    past = timezone.now() + relativedelta(years=5) + timedelta(days=1)
    assert retention.delete_old_activity(now=past) == (2, 2)
    assert _records() == []
