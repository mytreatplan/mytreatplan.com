"""Lockout and login rate limits (epic-login entry 5): every row of the plan's I/O matrix.
5 wrong passwords, or 5 wrong codes, in a row lock the account (an unknown email: its
blind index) for 10 minutes (accounts.login_failures); password submissions count against
the IP's hourly `login_attempts` limit (signup.limits). Both refuse with signup's
BLOCKED_MESSAGE, which tells nothing."""
import html
import json

import pytest
import sentry_sdk
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import DatabaseError

from accounts import login_failures
from accounts.views import PENDING_LOGIN_SESSION_KEY
from conftest import TURNSTILE_FAIL_SECRET
from eventlog.models import Attempt, Event
from signup import limits
from signup.forms import BLOCKED_MESSAGE
from signup.tests.conftest import capture_codes, clear_cache  # noqa: F401 - autouse
from signup.tests.conftest import clock  # noqa: F401 - fixture
from signup.tests.helpers import NORMALIZED, PASSWORD, _current_code, _wrong
from signup.verification import EMAIL, PURPOSE_LOGIN

from .test_login import _html, _logged_in, _login, _login_with_code, doctor  # noqa: F401
from .test_login_code import login_clock  # noqa: F401 - fixture

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()

IP = '203.0.113.77'
META = {'REMOTE_ADDR': IP}
WRONG_PASSWORD = 'wrong-password-123'
UNKNOWN = 'nobody.here@example.com'
VERIFY, CODE, LOGIN, WELCOME = ('/en/login/verify/', '/en/login/code/', '/en/login/',
                                '/en/welcome/')
SPANISH_BLOCKED = 'No hemos podido procesar su solicitud. Inténtelo de nuevo más tarde.'


@pytest.fixture
def lock_clock(login_clock, monkeypatch):
    """The login clock, also seen by the failure counters and the locks."""
    monkeypatch.setattr('accounts.login_failures._now', login_clock)
    return login_clock


@pytest.fixture
def login_limit(settings):
    """The IP's login attempts lowered to 3 an hour (signup's limits unchanged)."""
    settings.SIGNUP_IP_LIMITS = {**settings.SIGNUP_IP_LIMITS, limits.LOGIN_ATTEMPTS: 3}
    return 3


def _assert_blocked(response, message=BLOCKED_MESSAGE):
    """Only the generic BLOCKED_MESSAGE, as a non-field error; nothing else."""
    assert response.status_code == 200
    form = response.context['form']
    assert form.non_field_errors() == [message]
    assert list(form.errors) == ['__all__']
    page = _html(response)
    assert 'The email address or password is not correct.' not in page
    assert 'attempt' not in page.lower()


def _wrong_password(client, email=NORMALIZED, lang='en'):
    response = _login(client, email=email, password=WRONG_PASSWORD, lang=lang, extra=META)
    assert response.status_code == 200 and not _logged_in(client)
    return response


def _lock_by_passwords(client, email=NORMALIZED):
    """4 wrong passwords (the generic login error), then the 5th, which locks."""
    for _ in range(login_failures.LOCK_THRESHOLD - 1):
        response = _wrong_password(client, email)
        assert response.context['form'].has_error('__all__', 'invalid_login')
    return _wrong_password(client, email)


def _to_code(client):
    assert _login(client, extra=META)['Location'] == VERIFY
    assert client.post(VERIFY, {'contact': 'email'})['Location'] == CODE


def _post_code(client, code):
    return client.post(CODE, {'code': code}, **META)


def _right_code():
    return _current_code(EMAIL, NORMALIZED, PURPOSE_LOGIN)


def _locks(**filters):
    return list(Event.objects.filter(attempt__kind=Attempt.LOGIN, event='account_locked',
                                     **filters).select_related('attempt'))


def _ip_blocks():
    return list(Event.objects.filter(attempt__kind=Attempt.LOGIN, event='rejected',
                                     field='ip'))


def _messages(sentry_capture):
    return [event.get('logentry', {}).get('message') or event.get('message')
            for event in sentry_capture.events]


# --- Wrong passwords --------------------------------------------------------------------

def test_fifth_wrong_password_locks_the_account(client, doctor, lock_clock):
    response = _lock_by_passwords(client)

    _assert_blocked(response)
    assert login_failures.is_locked(account_id=doctor.pk)
    # Locking resets the counter: after the lock a new run of 5 is needed.
    assert login_failures.failure_count(login_failures.PASSWORD, account_id=doctor.pk) == 0
    [lock] = _locks()
    assert lock.attempt.account_id == doctor.pk
    assert (lock.step, lock.field, lock.reason) == ('login', 'password', 'wrong_password')
    assert lock.data == {'count': 5, 'ip': IP, 'user_agent': ''}
    assert WRONG_PASSWORD not in json.dumps(lock.data)


def test_locked_account_refuses_the_right_password(client, doctor, lock_clock, monkeypatch):
    _lock_by_passwords(client)
    checked = []
    monkeypatch.setattr('accounts.forms.authenticate',
                        lambda *args, **kwargs: checked.append(1))

    response = _login(client, extra=META)

    _assert_blocked(response)
    assert checked == []  # refused before the password is checked
    assert PENDING_LOGIN_SESSION_KEY not in client.session
    assert not _logged_in(client)
    assert client.get(VERIFY)['Location'] == LOGIN
    # Refusals during the lock are not counted and do not lock again.
    assert len(_locks()) == 1


def test_locked_account_refuses_a_remembered_device(client, doctor, lock_clock):
    _to_code(client)
    assert client.post(CODE, {'code': _right_code(), 'remember': 'on'})['Location'] == WELCOME
    client.post('/en/logout/')
    _lock_by_passwords(client)

    response = _login(client, extra=META)

    _assert_blocked(response)
    assert not _logged_in(client)


def test_lock_ends_after_ten_minutes(client, doctor, lock_clock):
    """The acceptance criterion: refused within the 10 minutes, logged in after."""
    _lock_by_passwords(client)
    lock_clock.advance(login_failures.LOCK_SECONDS - 1)
    _assert_blocked(_login(client, extra=META))

    lock_clock.advance(2)  # 10 min + 1 s

    assert not login_failures.is_locked(account_id=doctor.pk)
    # The next wrong one is a 1st again.
    response = _wrong_password(client)
    assert response.context['form'].has_error('__all__', 'invalid_login')
    assert login_failures.failure_count(login_failures.PASSWORD, account_id=doctor.pk) == 1
    response = _login_with_code(client, extra=META)
    assert response['Location'] == WELCOME and _logged_in(client)


# --- Wrong codes ------------------------------------------------------------------------

def test_fifth_wrong_code_locks_and_ends_the_pending_login(client, doctor, lock_clock):
    _to_code(client)
    wrong = _wrong(_right_code())
    for _ in range(login_failures.LOCK_THRESHOLD - 1):
        assert _post_code(client, wrong).context['form'].has_error('code', 'invalid_code')

    response = _post_code(client, wrong)

    assert response['Location'] == LOGIN
    assert PENDING_LOGIN_SESSION_KEY not in client.session
    assert html.escape(str(BLOCKED_MESSAGE)) in _html(client.get(LOGIN))
    assert login_failures.is_locked(account_id=doctor.pk)
    assert login_failures.failure_count(login_failures.CODE, account_id=doctor.pk) == 0
    [lock] = _locks()
    assert lock.attempt.account_id == doctor.pk
    assert (lock.step, lock.field) == ('login-code', 'code')
    assert wrong not in json.dumps(lock.data)
    # The right password is refused too while the lock lasts.
    _assert_blocked(_login(client, extra=META))


def test_lock_set_while_on_the_code_page(client, doctor, lock_clock):
    _to_code(client)
    login_failures.lock(account_id=doctor.pk)

    response = client.get(CODE)

    assert response['Location'] == LOGIN
    assert PENDING_LOGIN_SESSION_KEY not in client.session
    assert html.escape(str(BLOCKED_MESSAGE)) in _html(client.get(LOGIN))
    # The pending login is gone: the code page cannot be used any more.
    assert client.post(CODE, {'code': '123456'})['Location'] == LOGIN
    assert not _logged_in(client)


def test_lock_set_while_choosing_the_contact(client, doctor, lock_clock):
    assert _login(client, extra=META)['Location'] == VERIFY
    login_failures.lock(account_id=doctor.pk)

    assert client.post(VERIFY, {'contact': 'email'})['Location'] == LOGIN
    assert PENDING_LOGIN_SESSION_KEY not in client.session


def test_separate_counters_do_not_add_up(client, doctor, lock_clock):
    for _ in range(4):
        _wrong_password(client)
    _to_code(client)
    wrong = _wrong(_right_code())
    for _ in range(4):
        response = _post_code(client, wrong)
        assert response.status_code == 200
        assert response.context['form'].has_error('code', 'invalid_code')

    assert not login_failures.is_locked(account_id=doctor.pk)
    assert _locks() == []
    assert client.get(CODE).status_code == 200


# --- Unknown email ----------------------------------------------------------------------

def test_unknown_email_locks_the_same_way(client, doctor, lock_clock):
    response = _lock_by_passwords(client, email=UNKNOWN)

    _assert_blocked(response)
    assert login_failures.is_locked(email=UNKNOWN)
    assert login_failures.is_locked(email=UNKNOWN.upper())  # by its blind index
    assert not login_failures.is_locked(account_id=doctor.pk)
    _assert_blocked(_wrong_password(client, email=UNKNOWN))
    [lock] = _locks()
    assert lock.attempt.account_id is None
    assert lock.reason == 'unknown_email' and lock.data['email'] == UNKNOWN
    # The real account is not affected.
    assert _login(client, extra=META)['Location'] == VERIFY


# --- Concurrency ------------------------------------------------------------------------

def test_simultaneous_failures_are_both_counted(doctor, monkeypatch):
    """A failure that finds the counter's lock held waits for it, then reads the count
    the other one wrote: both are counted (none is lost to a read-then-write race)."""
    key = login_failures._key(login_failures.PASSWORD, doctor.pk)
    assert cache.add(key + ':mutex', 1)  # the other request holds the counter's lock
    waits = []

    def other_finishes(seconds):
        if not waits:  # the other request writes its count and lets go
            cache.set(key, {'count': 1, 'last': login_failures._now()})
            cache.delete(key + ':mutex')
        waits.append(seconds)
    monkeypatch.setattr(login_failures, '_sleep', other_finishes)

    failure = login_failures.record_failure(login_failures.PASSWORD, account_id=doctor.pk)
    assert (failure.count, failure.locked) == (2, False)
    assert len(waits) == 1
    assert login_failures.failure_count(login_failures.PASSWORD, account_id=doctor.pk) == 2
    assert cache.get(key + ':mutex') is None


def test_counter_lock_never_got_is_reported_and_the_login_goes_on(client, doctor,
                                                                   monkeypatch,
                                                                   sentry_capture):
    key = login_failures._key(login_failures.PASSWORD, doctor.pk)
    assert cache.add(key + ':mutex', 1, timeout=60)
    monkeypatch.setattr(login_failures, '_sleep', lambda seconds: None)

    response = _wrong_password(client)
    sentry_sdk.flush()

    assert response.context['form'].has_error('__all__', 'invalid_login')
    assert _messages(sentry_capture) == [
        'Login failure counter error: record_failure (CounterBusy)']
    assert NORMALIZED not in json.dumps(sentry_capture.events)


# --- The per-IP limit -------------------------------------------------------------------

def test_ip_over_the_limit_is_refused_without_checking_credentials(client, doctor,
                                                                    login_limit,
                                                                    monkeypatch):
    for _ in range(login_limit):
        _wrong_password(client)
    checked = []
    monkeypatch.setattr('accounts.forms.authenticate',
                        lambda *args, **kwargs: checked.append(1))

    response = _login(client, extra=META)

    _assert_blocked(response)
    assert checked == []
    assert PENDING_LOGIN_SESSION_KEY not in client.session
    [block] = _ip_blocks()
    assert (block.step, block.reason) == ('login', 'blocked: rate limit')
    assert block.data == {'counter': 'login_attempts', 'ip': IP, 'user_agent': ''}
    assert block.attempt.account_id is None
    # Only the first block of the window is recorded.
    _assert_blocked(_login(client, extra=META))
    assert len(_ip_blocks()) == 1
    # Not counted as a wrong password, so it does not lock either.
    assert login_failures.failure_count(login_failures.PASSWORD,
                                        account_id=doctor.pk) == login_limit


def test_failing_block_record_still_refuses(client, doctor, login_limit, monkeypatch,
                                            sentry_capture):
    for _ in range(login_limit):
        _wrong_password(client)

    def fail(request, counter):
        raise DatabaseError(f'cache add failed for {IP}')
    monkeypatch.setattr(limits, 'first_block', fail)
    checked = []
    monkeypatch.setattr('accounts.forms.authenticate',
                        lambda *args, **kwargs: checked.append(1))

    response = _login(client, extra=META)
    sentry_sdk.flush()

    _assert_blocked(response)
    assert checked == []
    assert PENDING_LOGIN_SESSION_KEY not in client.session
    assert _ip_blocks() == []
    assert _messages(sentry_capture) == [
        'Login failure counter error: first_block (DatabaseError)']
    assert IP not in json.dumps(sentry_capture.events)


def test_ip_limit_is_per_ip_and_hourly(client, doctor, login_limit, lock_clock):
    for _ in range(login_limit):
        _wrong_password(client)
    _assert_blocked(_login(client, extra=META))

    other = _login(client, extra={'REMOTE_ADDR': '198.51.100.9'})
    assert other['Location'] == VERIFY

    lock_clock.advance(limits.IP_WINDOW_SECONDS)
    assert _login(client, extra=META)['Location'] == VERIFY


def test_failed_captcha_is_not_counted_against_the_ip(client, doctor, login_limit,
                                                      settings):
    settings.TURNSTILE_SECRET_KEY = TURNSTILE_FAIL_SECRET
    for _ in range(login_limit + 2):
        _wrong_password(client)
    settings.TURNSTILE_SECRET_KEY = '1x0000000000000000000000000000000AA'

    assert _login(client, extra=META)['Location'] == VERIFY
    assert _ip_blocks() == []


def test_locked_and_empty_submissions_are_given_back_to_the_ip(client, doctor, settings,
                                                               lock_clock):
    """Only submissions that reach the credentials check count against the IP."""
    settings.SIGNUP_IP_LIMITS = {**settings.SIGNUP_IP_LIMITS, limits.LOGIN_ATTEMPTS: 6}
    _lock_by_passwords(client, email=UNKNOWN)  # 5 counted
    for _ in range(3):
        _assert_blocked(_wrong_password(client, email=UNKNOWN))  # locked: given back
        _login(client, password='', extra=META)  # empty: given back

    assert _login(client, extra=META)['Location'] == VERIFY  # the 6th
    assert _ip_blocks() == []


# --- Errors -----------------------------------------------------------------------------

def test_failing_lock_and_limit_never_stop_the_login(client, doctor, monkeypatch,
                                                     sentry_capture):
    def fail(*args, **kwargs):
        raise DatabaseError(f'cache read failed for {NORMALIZED} {IP}')

    real_reserve = limits.ip_reserve

    def fail_login_attempts(request, counter):
        if counter == limits.LOGIN_ATTEMPTS:
            fail()
        return real_reserve(request, counter)  # the code sends' own limit still works

    monkeypatch.setattr(login_failures, 'is_locked', fail)
    monkeypatch.setattr(limits, 'ip_reserve', fail_login_attempts)

    response = _login_with_code(client, extra=META)
    sentry_sdk.flush()

    assert response['Location'] == WELCOME and _logged_in(client)
    messages = _messages(sentry_capture)
    assert 'Login failure counter error: ip_reserve (DatabaseError)' in messages
    assert 'Login failure counter error: is_locked (DatabaseError)' in messages
    text = json.dumps(sentry_capture.events)
    for secret in (NORMALIZED, IP, PASSWORD):
        assert secret not in text


def _failures():
    return list(Event.objects.filter(attempt__kind=Attempt.LOGIN, event='login_failed')
                .order_by('created_at', 'id'))


def test_failing_lock_write_keeps_the_count_and_the_record(client, doctor, monkeypatch,
                                                           sentry_capture):
    def fail(**kwargs):
        raise DatabaseError('cache write failed')

    monkeypatch.setattr(login_failures, 'lock', fail)
    for _ in range(login_failures.LOCK_THRESHOLD):
        response = _wrong_password(client)
    sentry_sdk.flush()

    # Not locked: the generic login error, as without a lock; the count and the 5th
    # failure's record (3.4) are kept.
    assert response.context['form'].has_error('__all__', 'invalid_login')
    assert login_failures.failure_count(login_failures.PASSWORD, account_id=doctor.pk) == 5
    assert _failures()[-1].data['count'] == 5
    assert _locks() == []
    assert _messages(sentry_capture) == ['Login failure counter error: lock (DatabaseError)']
    assert _login(client, extra=META)['Location'] == VERIFY


def test_failing_count_clear_still_locks_and_refuses(client, doctor, monkeypatch,
                                                     sentry_capture):
    def fail(key):
        raise DatabaseError('cache delete failed')

    monkeypatch.setattr(login_failures, '_clear', fail)
    response = _lock_by_passwords(client)
    sentry_sdk.flush()

    _assert_blocked(response)
    assert login_failures.is_locked(account_id=doctor.pk)
    assert _failures()[-1].data['count'] == 5
    [lock] = _locks()
    assert lock.data['count'] == 5
    assert _messages(sentry_capture) == [
        'Login failure counter error: clear_count (DatabaseError)']
    _assert_blocked(_login(client, extra=META))


def test_lock_set_during_the_password_check_still_refuses(client, doctor, monkeypatch):
    """A parallel 5th failure locks the account after the first lock check passed: the
    right password authenticated meanwhile is refused all the same."""
    from accounts import views

    real_check = views._login_locked

    def locked_meanwhile(email):
        result = real_check(email)
        login_failures.lock(account_id=doctor.pk)
        return result
    monkeypatch.setattr(views, '_login_locked', locked_meanwhile)

    response = _login(client, extra=META)

    _assert_blocked(response)
    assert PENDING_LOGIN_SESSION_KEY not in client.session
    assert not _logged_in(client)


def test_remembered_device_locked_during_the_password_check(client, doctor, monkeypatch):
    from accounts import views

    _to_code(client)
    assert client.post(CODE, {'code': _right_code(), 'remember': 'on'})['Location'] == WELCOME
    client.post('/en/logout/')
    real_check = views._login_locked

    def locked_meanwhile(email):
        result = real_check(email)
        login_failures.lock(account_id=doctor.pk)
        return result
    monkeypatch.setattr(views, '_login_locked', locked_meanwhile)

    _assert_blocked(_login(client, extra=META))
    assert not _logged_in(client)


# --- Spanish ----------------------------------------------------------------------------

def test_spanish_refusal(client, doctor, lock_clock):
    for _ in range(login_failures.LOCK_THRESHOLD - 1):
        _wrong_password(client, lang='es')
    _assert_blocked(_wrong_password(client, lang='es'), SPANISH_BLOCKED)
    _assert_blocked(_login(client, lang='es', extra=META), SPANISH_BLOCKED)


def test_spanish_code_page_refusal(client, doctor, lock_clock):
    _to_code(client)
    login_failures.lock(account_id=doctor.pk)

    response = client.get('/es/login/code/')

    assert response['Location'] == '/es/login/'
    assert SPANISH_BLOCKED in _html(client.get('/es/login/'))
