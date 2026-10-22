"""Signup entry 9, anti-abuse: Cloudflare Turnstile on step 1 (signup.captcha) and the
per-IP limits on signup starts, live email checks and code sends (signup.limits). Every
row of the plan's I/O matrix."""
import datetime
import html
import logging
import socket
import urllib.error
from types import SimpleNamespace

import pytest
from django.conf import settings as django_settings
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts import http as accounts_http
from conftest import TURNSTILE_FAIL_SECRET, TURNSTILE_PASS_SECRET, TURNSTILE_TEST_TOKEN
from eventlog.models import Attempt, Event
from signup import captcha, limits
from signup.models import PendingRegistration
from signup.verification import (EMAIL, PURPOSE_RESUME, FakeVerificationProvider,
                                  VerificationError)

from .helpers import NORMALIZED, PASSWORD, STEP1, _legal, _step1

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

IP = '203.0.113.7'
OTHER_IP = '198.51.100.23'
GENERIC = "We couldn't process your request. Please try again later."
GENERIC_ES = 'No hemos podido procesar su solicitud. Inténtelo de nuevo más tarde.'
EMAIL_URL = '/en/signup/verify-email/'
RESUME_URL = '/en/signup/resume/'

# The real siteverify call (the autouse `turnstile` fixture replaces it in every test).
REAL_POST_SITEVERIFY = captcha.post_siteverify


def _client(ip=IP):
    return Client(REMOTE_ADDR=ip)


def _email(n):
    return f'person{n}@example.com'


def _request(ip=IP):
    """Just enough of a request for signup.limits."""
    return SimpleNamespace(META={'REMOTE_ADDR': ip})


def _rejected(field):
    return list(Event.objects.filter(event='rejected', field=field))


class Pauses:
    """Stands in for the IP lock's wait (signup.limits._sleep): records each pause, takes
    no time, and runs `on_pause` (e.g. the other request releasing the lock)."""

    def __init__(self):
        self.calls = []
        self.on_pause = None

    def __call__(self, seconds):
        self.calls.append(seconds)
        if self.on_pause:
            self.on_pause(len(self.calls))


@pytest.fixture(autouse=True)
def pauses(monkeypatch):
    fake = Pauses()
    monkeypatch.setattr(limits, '_sleep', fake)
    return fake


@pytest.fixture
def small_limits(settings):
    settings.SIGNUP_IP_LIMITS = {limits.SIGNUP_STARTS: 2, limits.EMAIL_CHECKS: 2,
                                 limits.CODE_SENDS: 2}
    return settings.SIGNUP_IP_LIMITS


def _assert_blocked_page(response, generic=GENERIC):
    assert response.status_code == 200
    form = response.context['form']
    assert form.non_field_errors() == [generic]
    # Only the generic error: no field is validated, nothing tells which check.
    assert list(form.errors) == ['__all__']
    assert html.escape(generic) in response.content.decode()


# --- Settings and page ------------------------------------------------------------------

def test_defaults_are_the_test_keys_and_the_agreed_limits():
    # conftest.py sets the test keys; the limits come from mytreatplan/settings.py.
    assert django_settings.TURNSTILE_SITE_KEY == '1x00000000000000000000AA'
    assert django_settings.TURNSTILE_SECRET_KEY == TURNSTILE_PASS_SECRET
    assert set(django_settings.SIGNUP_IP_LIMITS) == set(limits.IP_COUNTERS)


def test_widget_is_on_step1_only(client):
    page = client.get('/en/signup/').content.decode()
    assert 'class="cf-turnstile"' in page
    assert 'data-sitekey="1x00000000000000000000AA"' in page
    assert 'data-language="en"' in page
    assert 'turnstile/v0/api.js?render=explicit' in page
    assert _step1(client).status_code == 302
    legal = client.get('/en/signup/legal/').content.decode()
    assert 'cf-turnstile' not in legal and 'turnstile/v0/api.js' not in legal


# --- CAPTCHA ----------------------------------------------------------------------------

def test_captcha_passes(turnstile):
    client = _client()
    assert _step1(client).status_code == 302
    assert PendingRegistration.objects.count() == 1
    assert turnstile.calls == [{'secret': TURNSTILE_PASS_SECRET,
                                'response': TURNSTILE_TEST_TOKEN, 'remoteip': IP}]


def test_captcha_fails(settings, turnstile, external_checks, caplog):
    settings.TURNSTILE_SECRET_KEY = TURNSTILE_FAIL_SECRET
    client = _client()
    # Even with a field error, only the generic error shows, and no check runs.
    response = _step1(client, password2='something-else-entirely')
    _assert_blocked_page(response)
    assert not PendingRegistration.objects.exists()
    assert external_checks.pwned_prefixes == [] and external_checks.debounce_domains == []
    [event] = _rejected('captcha')
    assert (event.step, event.reason) == ('account', 'blocked: captcha')
    assert event.data['ip'] == IP and event.data['email'] == 'ana.garcia@Example.COM'
    # Neither the token nor the secret is logged anywhere.
    assert TURNSTILE_TEST_TOKEN not in str(event.data)
    for record in caplog.records:
        assert TURNSTILE_TEST_TOKEN not in record.getMessage()
        assert TURNSTILE_FAIL_SECRET not in record.getMessage()
    # The page renders a new widget for the next try.
    assert 'class="cf-turnstile"' in response.content.decode()


def test_no_token_is_refused_without_asking_cloudflare(turnstile):
    client = _client()
    response = _step1(client, **{captcha.TOKEN_FIELD: ''})
    _assert_blocked_page(response)
    assert turnstile.calls == []
    assert not PendingRegistration.objects.exists()
    assert [e.reason for e in _rejected('captcha')] == ['blocked: captcha']


def test_oversized_token_is_refused(turnstile):
    response = _step1(_client(), **{captcha.TOKEN_FIELD: 'x' * 2049})
    _assert_blocked_page(response)
    assert turnstile.calls == []


@pytest.mark.parametrize('error', [TimeoutError('timed out'), socket.timeout(),
                                   urllib.error.URLError('down'), ValueError('bad json')])
def test_turnstile_down_lets_the_step_through(turnstile, error):
    turnstile.error = error
    assert _step1(_client()).status_code == 302
    assert PendingRegistration.objects.count() == 1
    [event] = Event.objects.filter(event='check_unavailable', field='captcha')
    assert (event.step, event.reason) == ('account', 'turnstile')
    assert not _rejected('captcha')


@pytest.mark.parametrize('answer', [
    {'success': False, 'error-codes': ['internal-error']},
    {'success': False, 'error-codes': ['invalid-input-secret']},
    ['not', 'a', 'dict'],
])
def test_siteverify_unable_to_decide_counts_as_down(turnstile, answer):
    turnstile.answer = answer
    assert _step1(_client()).status_code == 302
    assert Event.objects.filter(event='check_unavailable', field='captcha').count() == 1


def test_turnstile_down_still_applies_the_ip_limits(turnstile, small_limits):
    turnstile.error = TimeoutError()
    for n in range(2):
        assert _step1(_client(), email=_email(n)).status_code == 302
    _assert_blocked_page(_step1(_client(), email=_email(9)))
    assert PendingRegistration.objects.count() == 2


@pytest.mark.parametrize('answer', [
    {'success': False, 'error-codes': ['timeout-or-duplicate']},
    {'success': False, 'error-codes': ['invalid-input-response']},
    {'success': 'true'},
    {},
])
def test_siteverify_refusals_fail(turnstile, answer):
    turnstile.answer = answer
    assert captcha.verify_turnstile(TURNSTILE_TEST_TOKEN, IP) == captcha.FAILED


def test_verify_turnstile_posts_to_siteverify(monkeypatch, settings):
    """The real HTTP call (accounts.http.post_form), with urllib stubbed."""
    sent = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"success": true}'

    def urlopen(request, timeout):
        sent.update(url=request.full_url, method=request.get_method(), body=request.data,
                    timeout=timeout, agent=request.get_header('User-agent'),
                    content_type=request.get_header('Content-type'))
        return Response()

    monkeypatch.setattr(captcha, 'post_siteverify', REAL_POST_SITEVERIFY)
    monkeypatch.setattr(accounts_http.urllib.request, 'urlopen', urlopen)
    assert captcha.verify_turnstile('tok', IP) == captcha.PASSED
    assert sent['url'] == settings.TURNSTILE_VERIFY_URL and sent['method'] == 'POST'
    assert sent['body'] == (f'secret={TURNSTILE_PASS_SECRET}&response=tok&remoteip={IP}'
                            .encode())
    assert sent['timeout'] == settings.EXTERNAL_CHECK_TIMEOUT
    assert sent['agent'] == settings.EXTERNAL_CHECK_USER_AGENT
    assert sent['content_type'] == 'application/x-www-form-urlencoded'


def test_unavailable_logs_no_token(turnstile, caplog):
    turnstile.error = urllib.error.URLError('down')
    with caplog.at_level(logging.WARNING, logger='signup.captcha'):
        assert captcha.verify_turnstile(TURNSTILE_TEST_TOKEN, IP) == captcha.UNAVAILABLE
    assert caplog.records
    assert all(TURNSTILE_TEST_TOKEN not in r.getMessage() for r in caplog.records)


# --- Signup starts ----------------------------------------------------------------------

def test_starts_limit(small_limits):
    for n in range(2):
        assert _step1(_client(), email=_email(n)).status_code == 302
    response = _step1(_client(), email=_email(2))
    _assert_blocked_page(response)
    assert PendingRegistration.objects.count() == 2
    [event] = _rejected('ip')
    assert (event.step, event.reason) == ('account', 'blocked: rate limit')
    assert event.data['counter'] == limits.SIGNUP_STARTS and event.data['ip'] == IP
    # The page says nothing of the limit or when it resets: the generic error only.
    assert 'rate limit' not in response.content.decode().lower()


def test_starts_limit_is_per_ip(small_limits):
    for n in range(2):
        _step1(_client(), email=_email(n))
    assert _step1(_client(OTHER_IP), email=_email(2)).status_code == 302
    assert PendingRegistration.objects.count() == 3


def test_forwarded_ip_is_the_one_counted(small_limits):
    """Behind Nginx: the last X-Forwarded-For entry (eventlog.service.client_ip)."""
    for n in range(2):
        proxy = Client(REMOTE_ADDR='127.0.0.1', HTTP_X_FORWARDED_FOR=f'10.0.0.{n}, {IP}')
        assert _step1(proxy, email=_email(n)).status_code == 302
    _assert_blocked_page(_step1(_client(IP), email=_email(2)))


def test_resaving_the_own_signup_is_not_a_start(small_limits):
    client = _client()
    assert _step1(client, email=_email(0)).status_code == 302
    for _resave in range(3):  # Back to step 1 and Continue again
        assert _step1(client, email=_email(0)).status_code == 302
    assert PendingRegistration.objects.count() == 1
    # One start counted: one more is allowed, then the limit.
    assert _step1(_client(), email=_email(1)).status_code == 302
    _assert_blocked_page(_step1(_client(), email=_email(2)))
    # The own signup can still be resaved once the limit is reached.
    assert _step1(client, email=_email(0)).status_code == 302


def test_invalid_and_pending_found_posts_are_not_starts(small_limits):
    for _try in range(3):
        assert _step1(_client(), email=_email(0), password2='mismatch-xyz').status_code == 200
    assert _step1(_client(), email=_email(0)).status_code == 302
    for _try in range(3):  # another signup has this email: the resume offer, nothing saved
        assert _step1(_client(), email=_email(0)).status_code == 200
    assert _step1(_client(), email=_email(1)).status_code == 302


def test_window_resets(small_limits, clock):
    for n in range(2):
        _step1(_client(), email=_email(n))
    _assert_blocked_page(_step1(_client(), email=_email(2)))
    clock.advance(limits.IP_WINDOW_SECONDS)
    assert _step1(_client(), email=_email(2)).status_code == 302


def test_counter_keeps_the_window_timeout(small_limits):
    """The count lives through its window (not the cache's 5-minute default)."""
    assert limits.ip_reserve(_request(), limits.SIGNUP_STARTS)
    assert limits.ip_reserve(_request(), limits.SIGNUP_STARTS)
    assert limits.ip_reserve(_request(), limits.SIGNUP_STARTS) is None
    with connection.cursor() as cursor:
        cursor.execute("SELECT expires FROM django_cache WHERE cache_key LIKE '%signup:ip:%'")
        [(expires,)] = cursor.fetchall()
    if isinstance(expires, str):
        expires = parse_datetime(expires)
    if timezone.is_naive(expires):
        expires = timezone.make_aware(expires, datetime.timezone.utc)
    assert (expires - timezone.now()).total_seconds() > limits.IP_WINDOW_SECONDS - 60


# --- Live email checks ------------------------------------------------------------------

def test_email_checks_limit(small_limits, django_user_model):
    django_user_model.objects.create_user(email=NORMALIZED, password=PASSWORD)
    client = _client()
    for _check in range(2):
        response = client.post('/en/signup/check-email/', {'email': NORMALIZED})
        assert response.content.strip()  # the recovery note
    response = client.post('/en/signup/check-email/', {'email': NORMALIZED})
    assert response.status_code == 200 and response.content.strip() == b''
    [event] = _rejected('ip')
    assert event.reason == 'blocked: rate limit'
    assert event.data['counter'] == limits.EMAIL_CHECKS and event.data['ip'] == IP
    # Another IP still gets its answer.
    other = _client(OTHER_IP).post('/en/signup/check-email/', {'email': NORMALIZED})
    assert other.content.strip()


# --- Code sends -------------------------------------------------------------------------

@pytest.fixture
def send_limits(small_limits):
    """Room for the starts the code send tests need: only the send limit is small."""
    small_limits[limits.SIGNUP_STARTS] = 10
    return small_limits


def _to_code_page(client, n):
    assert _step1(client, email=_email(n)).status_code == 302
    return _legal(client)


def test_sends_limit_across_contacts(send_limits):
    for n in range(2):
        _to_code_page(_client(), n)
    assert FakeVerificationProvider.current_code(EMAIL, _email(1), 'signup')
    client = _client()
    assert _to_code_page(client, 2)['Location'] == EMAIL_URL
    # Not sent, and the page shows the generic error only.
    assert not FakeVerificationProvider.current_code(EMAIL, _email(2), 'signup')
    page = client.get(EMAIL_URL).content.decode()
    assert 'data-blocked' in page and html.escape(GENERIC) in page
    assert 'data-hourly-limit' not in page and 'data-cooldown' not in page
    [event] = _rejected('ip')
    assert (event.step, event.reason) == ('verify-email', 'blocked: rate limit')
    assert event.data['counter'] == limits.CODE_SENDS and event.data['ip'] == IP
    # The contact limits are untouched: from another IP the resend goes out.
    other = Client(REMOTE_ADDR=OTHER_IP)
    other.cookies = client.cookies
    other.post(EMAIL_URL, {'action': 'resend'})
    assert FakeVerificationProvider.current_code(EMAIL, _email(2), 'signup')


def test_failed_sends_are_not_counted(send_limits, monkeypatch):
    def fail(*args, **kwargs):
        raise VerificationError('down')

    monkeypatch.setattr(FakeVerificationProvider, 'send', fail)
    for n in range(3):
        _to_code_page(_client(), n)
    assert Event.objects.filter(event='code_send_failed').count() == 3
    assert not _rejected('ip')
    # Each failed send's reservation was refunded.
    assert cache.get(limits._ip_key(_request(), limits.CODE_SENDS)) == 0
    assert limits.ip_reserve(_request(), limits.CODE_SENDS)  # still under the limit


def test_refund_is_in_the_window_of_the_reservation(small_limits, clock):
    reservation = limits.ip_reserve(_request(), limits.SIGNUP_STARTS)
    clock.advance(limits.IP_WINDOW_SECONDS)
    assert limits.ip_reserve(_request(), limits.SIGNUP_STARTS)
    limits.ip_refund(_request(), limits.SIGNUP_STARTS, reservation)
    assert cache.get(reservation) == 0
    assert cache.get(limits._ip_key(_request(), limits.SIGNUP_STARTS)) == 1


def test_resume_send_blocked_shows_the_generic_error(send_limits):
    owner = _client(OTHER_IP)
    _to_code_page(owner, 0)  # its email code: another IP
    for n in range(1, 3):
        _to_code_page(_client(), n)  # 2 sends from IP
    second = _client()
    second.post('/en/signup/check-email/', {'email': _email(0)})
    response = second.post(RESUME_URL, {'action': 'send'}, follow=True)
    page = response.content.decode()
    assert 'data-blocked' in page and html.escape(GENERIC) in page
    assert not FakeVerificationProvider.current_code(EMAIL, _email(0), PURPOSE_RESUME)
    [event] = _rejected('ip')
    assert event.step == 'resume' and event.data['counter'] == limits.CODE_SENDS


# --- Keys, Spanish ----------------------------------------------------------------------

def test_cache_keys_hold_no_plain_ip(send_limits):
    client = _client()
    _to_code_page(client, 0)
    client.post('/en/signup/check-email/', {'email': _email(5)})
    with connection.cursor() as cursor:
        cursor.execute('SELECT cache_key, value FROM django_cache')
        rows = cursor.fetchall()
    ip_keys = [key for key, _value in rows if ':signup:ip:' in key]
    # Every signup counter (the login's own, epic-login entry 5, and the password reset's,
    # entry 10, are not used by signup).
    assert {key.split(':')[4] for key in ip_keys} == set(limits.IP_COUNTERS) - {
        limits.LOGIN_ATTEMPTS, limits.RESET_REQUESTS}
    for key, value in rows:
        assert IP not in key and IP not in str(value)


def test_generic_error_in_spanish(settings):
    settings.TURNSTILE_SECRET_KEY = TURNSTILE_FAIL_SECRET
    response = _step1(_client(), lang='es')
    _assert_blocked_page(response, generic=GENERIC_ES)


def test_step1_post_keeps_what_was_typed_when_blocked(settings):
    settings.TURNSTILE_SECRET_KEY = TURNSTILE_FAIL_SECRET
    page = _step1(_client()).content.decode()
    assert f'value="{STEP1["first_name"]}"' in page
    assert PASSWORD not in page


# --- IPv6 networks, locks, one log per window --------------------------------------------

def test_ipv6_addresses_in_one_64_share_a_counter(small_limits):
    for n in range(2):
        assert _step1(_client(f'2001:db8:1:2::{n + 1}'), email=_email(n)).status_code == 302
    # Another address of the same /64: the same counter.
    _assert_blocked_page(_step1(_client('2001:db8:1:2:ffff::9'), email=_email(2)))
    # Another /64: its own counter.
    assert _step1(_client('2001:db8:1:3::1'), email=_email(3)).status_code == 302


def test_ip_subjects():
    assert limits._ip_subject('2001:db8:1:2:aaaa::1') == '2001:db8:1:2::/64'
    assert limits._ip_subject('::ffff:203.0.113.7') == IP
    assert limits._ip_subject(IP) == IP
    assert limits._ip_subject('not-an-ip') == 'not-an-ip'
    assert limits._ip_hash('203.0.113.8') != limits._ip_hash(IP)


def test_lock_released_within_the_wait_proceeds(pauses):
    assert limits.acquire_ip_lock(_request(), limits.SIGNUP_STARTS)
    # The other request finishes its read-check-increment during the third pause.
    pauses.on_pause = lambda n: n == 3 and limits.release_ip_lock(
        _request(), limits.SIGNUP_STARTS)
    assert _step1(_client(), email=_email(0)).status_code == 302
    assert pauses.calls == [limits.IP_LOCK_PAUSE] * 3
    assert not _rejected('ip')


def test_held_ip_lock_blocks_a_start(pauses):
    assert limits.acquire_ip_lock(_request(), limits.SIGNUP_STARTS)
    _assert_blocked_page(_step1(_client(), email=_email(0)))
    # It waited the whole second before answering blocked.
    assert pauses.calls == [limits.IP_LOCK_PAUSE] * limits.IP_LOCK_TRIES
    assert limits.IP_LOCK_PAUSE * limits.IP_LOCK_TRIES == pytest.approx(1)
    assert not PendingRegistration.objects.exists()
    [event] = _rejected('ip')
    assert event.data['counter'] == limits.SIGNUP_STARTS
    # Released: the next start goes through, and releases the lock after itself (the
    # lock covers only the count, not the save).
    limits.release_ip_lock(_request(), limits.SIGNUP_STARTS)
    assert _step1(_client(), email=_email(0)).status_code == 302
    assert limits.acquire_ip_lock(_request(), limits.SIGNUP_STARTS)


def test_held_ip_lock_blocks_an_email_check(django_user_model):
    django_user_model.objects.create_user(email=NORMALIZED, password=PASSWORD)
    assert limits.acquire_ip_lock(_request(), limits.EMAIL_CHECKS)
    response = _client().post('/en/signup/check-email/', {'email': NORMALIZED})
    assert response.content.strip() == b''
    limits.release_ip_lock(_request(), limits.EMAIL_CHECKS)
    response = _client().post('/en/signup/check-email/', {'email': NORMALIZED})
    assert response.content.strip()


def test_held_ip_lock_blocks_a_send():
    assert limits.acquire_ip_lock(_request(), limits.CODE_SENDS)
    client = _client()
    _to_code_page(client, 0)
    assert not FakeVerificationProvider.current_code(EMAIL, _email(0), 'signup')
    assert 'data-blocked' in client.get(EMAIL_URL).content.decode()
    [event] = _rejected('ip')
    assert event.data['counter'] == limits.CODE_SENDS


def test_a_block_is_logged_once_per_ip_counter_and_window(small_limits, clock):
    for _check in range(2):
        _client().post('/en/signup/check-email/', {'email': _email(0)})
    assert not Attempt.objects.exists()
    for _check in range(3):  # cookieless: each a new session
        response = _client().post('/en/signup/check-email/', {'email': _email(0)})
        assert response.content.strip() == b''
    assert len(_rejected('ip')) == 1 and Attempt.objects.count() == 1
    # Other counters and IPs are logged on their own.
    for n in range(3):
        _step1(_client(), email=_email(n))
    _client(OTHER_IP).post('/en/signup/check-email/', {'email': _email(0)})
    assert [e.data['counter'] for e in _rejected('ip')] == [limits.EMAIL_CHECKS,
                                                            limits.SIGNUP_STARTS]
    # A new window: the first block is logged again.
    clock.advance(limits.IP_WINDOW_SECONDS)
    for _check in range(3):
        _client().post('/en/signup/check-email/', {'email': _email(0)})
    assert len(_rejected('ip')) == 3


def test_ip_lock_is_not_held_during_the_send(monkeypatch):
    """Another attendee on the same IP can reserve while a provider call is running."""
    free = []
    real_send = FakeVerificationProvider.send

    def send(self, *args, **kwargs):
        free.append(limits.acquire_ip_lock(_request(), limits.CODE_SENDS))
        limits.release_ip_lock(_request(), limits.CODE_SENDS)
        return real_send(self, *args, **kwargs)

    monkeypatch.setattr(FakeVerificationProvider, 'send', send)
    _to_code_page(_client(), 0)
    assert free == [True]
    assert FakeVerificationProvider.current_code(EMAIL, _email(0), 'signup')
