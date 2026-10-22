"""The password reset (epic-login entry 10, CAP-2): every row of the plan's I/O matrix.
The request page (CAPTCHA, per-IP and per-email limits, the same answer for any email),
the emailed link (1 hour, once, SITE_URL), the new password (signup's rules), and the end
of every session with no login; EN/ES. Two `Client()` instances are two browsers. The mail
goes out in a background thread, run inline here (`inline_send`)."""
import json
import logging
import re
import smtplib
from datetime import timedelta

import pytest
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sessions.models import Session
from django.core.mail import EmailMessage
from django.test import Client
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts import login_failures, password_reset, sessions
from accounts.password_reset import LINK_SESSION_KEY, PASSWORD_CHANGED_MESSAGE
from conftest import TURNSTILE_FAIL_SECRET, TURNSTILE_TEST_TOKEN
from eventlog.models import Attempt, Event
from signup import limits
from signup.captcha import TOKEN_FIELD
from signup.forms import BLOCKED_MESSAGE
from signup.tests.helpers import EMAIL_ADDRESS, NORMALIZED, PASSWORD

from .helpers import (LOGIN, _deactivate, _erase, _html, _lock_by_passwords, _logged_in, _login,
                      _login_remembering, _login_with_code, _records)

pytestmark = [pytest.mark.django_db,
              pytest.mark.usefixtures('capture_codes', 'clear_cache', 'portal_urls')]

User = get_user_model()

NEW_PASSWORD = 'amber-glacier-meadow-77'
REQUEST, SENT, NEW = ('/en/password-reset/', '/en/password-reset/sent/',
                      '/en/password-reset/new/')
SITE = 'https://portal.example.test'
LINK_RE = re.compile(r'https://portal\.example\.test/(en|es)/reset/#(([^.\s]+)\.(\S+))')
SENT_TEXT = 'If an account exists for that email address, we have sent it a link'
INVALID_TEXT = 'This link is no longer valid'
CHANGED = str(PASSWORD_CHANGED_MESSAGE)
# The real one, before `inline_send` replaces it.
REAL_IN_BACKGROUND = password_reset.in_background


@pytest.fixture(autouse=True)
def inline_send(monkeypatch, settings):
    """The mail is sent in the request (not a thread), and links use SITE."""
    settings.SITE_URL = SITE
    monkeypatch.setattr(password_reset, 'in_background', lambda function: function())


def _request(client, email=EMAIL_ADDRESS, lang='en', extra=None, **fields):
    """Post the request form with the Turnstile test token, as the widget would."""
    return client.post(f'/{lang}/password-reset/',
                       {'email': email, TOKEN_FIELD: TURNSTILE_TEST_TOKEN, **fields},
                       **(extra or {}))


class Link:
    """A mailed link: its language, and its fragment `<uidb64>.<token>` (`value`)."""

    def __init__(self, lang, value):
        self.lang, self.value = lang, value
        self.uid, _dot, self.token = value.partition('.')


def _link(message):
    """The link in a mail: `<SITE>/<lang>/reset/#<uidb64>.<token>`."""
    match = LINK_RE.search(message.body)
    assert match, message.body
    return Link(match.group(1), match.group(2))


def _requested_link(client, mailoutbox, lang='en'):
    response = _request(client, lang=lang)
    assert response['Location'] == f'/{lang}/password-reset/sent/'
    return _link(mailoutbox[-1])


def _open(browser, link):
    """Open the emailed link as the browser does: GET the reset page (the fragment is never
    sent), then its script posts the fragment. A valid one redirects to the new-password
    page: (its URL, that page); otherwise (None, the "no longer valid" page)."""
    page = browser.get(f'/{link.lang}/reset/')
    assert page.status_code == 200 and 'data-reset-link-form' in _html(page)
    response = browser.post(f'/{link.lang}/reset/', {'link': link.value})
    if response.status_code == 302:
        return response['Location'], browser.get(response['Location'])
    return None, response


def _set_password(browser, password=NEW_PASSWORD, password2=None, lang='en'):
    return browser.post(f'/{lang}/password-reset/new/', {
        'password1': password, 'password2': password if password2 is None else password2})


def _all_event_text():
    return json.dumps([[e.event, e.step, e.field, e.reason, e.data]
                       for e in Event.objects.all()], default=str)


def _assert_invalid(response):
    assert response.status_code == 200
    page = _html(response)
    assert INVALID_TEXT in page
    assert 'href="/en/password-reset/"' in page and 'data-reset-again' in page
    assert 'name="password1"' not in page


# --- The request page ---------------------------------------------------------------------

def test_request_page_shows_the_form_and_the_widget(client):
    response = client.get(REQUEST)

    assert response.status_code == 200
    page = _html(response)
    assert 'Reset your password' in page
    assert 'name="email"' in page and 'autocomplete="email"' in page
    assert 'data-turnstile data-sitekey="' in page and 'js/turnstile.js' in page
    assert 'noindex' in page
    assert 'href="/en/login/"' in page


@pytest.mark.parametrize('email', [NORMALIZED, NORMALIZED.upper(), EMAIL_ADDRESS])
def test_request_for_an_account_mails_the_link(client, doctor, mailoutbox, email):
    response = _request(client, email=email)

    assert response.status_code == 302 and response['Location'] == SENT
    assert SENT_TEXT in _html(client.get(SENT))
    [message] = mailoutbox
    assert message.to == [NORMALIZED]
    assert message.from_email == 'MyTreatPlan <noreply@mytreatplan.com>'
    assert message.subject == 'Reset your MyTreatPlan password'
    assert 'valid for 1 hour' in message.body
    # Plain text only (decision 3).
    assert message.content_subtype == 'plain'
    assert not getattr(message, 'alternatives', [])
    link = _link(message)
    assert f'{SITE}/en/reset/#{link.uid}.{link.token}' in message.body  # in the fragment
    [record] = _records(event='password_reset_requested')
    assert record.attempt.account_id == doctor.pk
    assert (record.step, record.reason) == ('password-reset', 'mail_queued')
    assert 'email' not in record.data
    assert link.uid not in _all_event_text() and link.token not in _all_event_text()


def test_link_comes_from_site_url_not_the_host(client, doctor, mailoutbox, settings):
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, 'evil.example']
    _request(client, extra={'HTTP_HOST': 'evil.example'})

    [message] = mailoutbox
    assert 'evil.example' not in message.body
    assert f'{SITE}/en/reset/#' in message.body


def test_request_for_an_unknown_email_sends_nothing(client, doctor, mailoutbox):
    known = _html(client.get(_request(client)['Location']))
    unknown_response = _request(Client(), email='Nobody.Here@Example.com')

    assert unknown_response['Location'] == SENT
    assert _html(Client().get(SENT)).count(SENT_TEXT) == known.count(SENT_TEXT) == 1
    assert len(mailoutbox) == 1  # only the account's
    record = _records(event='password_reset_requested')[-1]
    assert record.attempt.account_id is None
    assert record.reason == 'unknown_email'
    assert record.data['email'] == 'nobody.here@example.com'


@pytest.mark.parametrize('case', ['inactive', 'erased', 'unusable_password'])
def test_inactive_erased_and_unusable_accounts_get_no_mail(client, doctor, mailoutbox,
                                                           settings, case):
    if case == 'inactive':
        _deactivate(doctor)
    elif case == 'erased':
        _erase(doctor, settings)
    else:
        doctor.set_unusable_password()
        doctor.save()

    response = _request(client)

    assert response['Location'] == SENT
    assert mailoutbox == []
    record = _records(event='password_reset_requested')[-1]
    if case == 'erased':
        assert (record.attempt.account_id, record.reason) == (None, 'unknown_email')
    else:
        assert (record.attempt.account_id, record.reason) == (doctor.pk, 'inactive_account')


def test_malformed_email_is_a_field_error(client, mailoutbox):
    response = _request(client, email='not-an-email')

    assert response.status_code == 200
    assert list(response.context['form'].errors) == ['email']
    assert mailoutbox == []


def test_failed_captcha_is_blocked(client, doctor, mailoutbox, settings):
    settings.TURNSTILE_SECRET_KEY = TURNSTILE_FAIL_SECRET

    response = _request(client)

    assert response.status_code == 200
    form = response.context['form']
    assert form.non_field_errors() == [BLOCKED_MESSAGE]
    assert list(form.errors) == ['__all__']
    assert mailoutbox == []
    assert _records(event='password_reset_requested') == []


def test_unavailable_captcha_lets_the_request_through(client, doctor, mailoutbox, turnstile):
    turnstile.error = OSError('down')

    assert _request(client)['Location'] == SENT

    assert len(mailoutbox) == 1
    [record] = _records(event='check_unavailable')
    assert (record.step, record.field, record.reason) == ('password-reset', 'captcha',
                                                          'turnstile')


def test_ip_limit_answers_the_same_and_records_the_first_block(client, doctor, mailoutbox,
                                                               settings):
    settings.SIGNUP_IP_LIMITS = {**settings.SIGNUP_IP_LIMITS, limits.RESET_REQUESTS: 2}
    others = ['a@example.org', 'b@example.org']
    for email in others:
        assert _request(client, email=email)['Location'] == SENT

    for _ in range(2):  # over the limit: no mail, the same answer
        assert _request(client)['Location'] == SENT

    assert mailoutbox == []
    [block] = _records(event='rejected')
    assert (block.step, block.field, block.reason) == ('password-reset', 'ip',
                                                       'blocked: rate limit')
    assert block.data['counter'] == 'reset_requests'
    assert len(_records(event='password_reset_requested')) == 2


def test_ip_limit_default_is_20(settings):
    assert settings.SIGNUP_IP_LIMITS[limits.RESET_REQUESTS] == 20


def test_fourth_mail_to_an_email_within_the_hour_is_not_sent(client, doctor, mailoutbox,
                                                             monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(password_reset, '_now', lambda: now[0])
    for _ in range(3):
        assert _request(client)['Location'] == SENT
    assert len(mailoutbox) == 3

    assert _request(client, email=NORMALIZED.upper())['Location'] == SENT
    assert len(mailoutbox) == 3
    # The request is recorded as any other; the background job records the limit.
    assert _records(event='password_reset_requested')[-1].reason == 'mail_queued'
    [limited] = [e for e in _records(event='rejected') if e.field == 'email']
    assert (limited.attempt.account_id, limited.reason) == (doctor.pk, 'rate_limited_email')

    now[0] += password_reset.MAIL_WINDOW_SECONDS  # the first mail leaves the window
    _request(client)
    assert len(mailoutbox) == 4


def test_failed_send_answers_the_same_and_reports_without_values(
        client, doctor, mailoutbox, monkeypatch, sentry_capture, caplog):
    def refuse(self, *args, **kwargs):
        raise smtplib.SMTPRecipientsRefused({NORMALIZED: (550, b'no such user')})
    monkeypatch.setattr(EmailMessage, 'send', refuse)

    with caplog.at_level(logging.WARNING):
        response = _request(client)

    assert response['Location'] == SENT
    messages = [event.get('logentry', {}).get('message') or event.get('message')
                for event in sentry_capture.events]
    assert 'Password reset error: send (SMTPRecipientsRefused)' in messages
    assert NORMALIZED not in json.dumps(sentry_capture.events, default=str)
    assert NORMALIZED not in caplog.text


def test_request_path_does_the_same_work_for_any_email(client, doctor, monkeypatch):
    """The per-email count, the token and the rendering run only in the background job:
    the request itself never calls them, whether or not the email has an account."""
    jobs, inline = [], []
    monkeypatch.setattr(password_reset, 'in_background', jobs.append)
    for name in ('reserve_mail', 'build_message'):
        monkeypatch.setattr(password_reset, name,
                            lambda *args, _name=name, **kwargs: inline.append(_name))
    monkeypatch.setattr(default_token_generator, 'make_token',
                        lambda user: inline.append('make_token'))

    assert _request(client)['Location'] == SENT
    assert _request(Client(), email='nobody.here@example.com')['Location'] == SENT

    assert inline == [] and len(jobs) == 1


def test_send_runs_in_a_background_thread():
    sent = []
    thread = REAL_IN_BACKGROUND(lambda: sent.append(True))
    thread.join(5)
    assert sent == [True] and thread.daemon


# --- The link -----------------------------------------------------------------------------

def test_link_redirects_to_the_new_password_form_without_its_secrets(client, doctor,
                                                                     mailoutbox):
    link = _requested_link(client, mailoutbox)
    uid, token = link.uid, link.token

    location, response = _open(Client(), link)

    assert location == NEW
    assert response.status_code == 200
    page = _html(response)
    assert 'Choose a new password' in page
    assert 'name="password1"' in page and 'name="password2"' in page
    assert 'autocomplete="new-password"' in page
    assert 'id="password-strength"' in page and 'zxcvbn-ts-core.js' in page
    assert 'signup-account.js' in page
    assert uid not in page and token not in page


def test_new_password_page_without_a_link_is_invalid(client):
    _assert_invalid(client.get(NEW))


@pytest.mark.parametrize('password, password2, field, code', [
    ('short-pass', None, 'password1', 'password_too_short'),
    ('123456789012345', None, 'password1', 'password_entirely_numeric'),
    ('ana.garcia@example.com', None, 'password1', 'password_too_similar'),
    (NEW_PASSWORD, 'another-thing-99', 'password2', 'password_mismatch'),
])
def test_weak_and_mismatched_passwords_are_field_errors(client, doctor, mailoutbox,
                                                        password, password2, field, code):
    browser = Client()
    _open(browser, _requested_link(client, mailoutbox))

    response = _set_password(browser, password, password2)

    assert response.status_code == 200
    assert response.context['form'].has_error(field, code)
    doctor.refresh_from_db()
    assert doctor.check_password(PASSWORD)


def test_breached_password_is_refused(client, doctor, mailoutbox, external_checks):
    external_checks.breached.add(NEW_PASSWORD)
    browser = Client()
    _open(browser, _requested_link(client, mailoutbox))

    response = _set_password(browser)

    assert response.context['form'].has_error('password1', 'password_breached')
    doctor.refresh_from_db()
    assert doctor.check_password(PASSWORD)


def test_reset_done(client, doctor, mailoutbox):
    browser = Client()
    _open(browser, _requested_link(client, mailoutbox))

    response = _set_password(browser)

    assert response.status_code == 302 and response['Location'] == LOGIN
    doctor.refresh_from_db()
    assert doctor.check_password(NEW_PASSWORD) and not doctor.check_password(PASSWORD)
    assert not _logged_in(browser)
    assert LINK_SESSION_KEY not in browser.session
    assert CHANGED in _html(browser.get(LOGIN))
    [record] = _records(event='password_reset_done')
    assert record.attempt.account_id == doctor.pk
    assert record.step == 'password-reset-new'
    text = _all_event_text()
    assert NEW_PASSWORD not in text and PASSWORD not in text


def test_reuse_of_the_link_is_invalid(client, doctor, mailoutbox):
    link = _requested_link(client, mailoutbox)
    browser = Client()
    _open(browser, link)
    assert _set_password(browser)['Location'] == LOGIN

    _location, response = _open(browser, link)
    _assert_invalid(response)
    _location, response = _open(Client(), link)
    _assert_invalid(response)
    # A second post with the same link is refused too (the password is not changed again).
    other = Client()
    _open(other, link)
    _assert_invalid(_set_password(other, 'yet-another-secret-66'))
    doctor.refresh_from_db()
    assert doctor.check_password(NEW_PASSWORD)


def test_a_newer_link_also_works_once_the_older_one_is_used(client, doctor, mailoutbox):
    first = _requested_link(client, mailoutbox)
    second = _requested_link(client, mailoutbox)
    browser = Client()
    _open(browser, first)
    _set_password(browser)

    _location, response = _open(Client(), second)
    _assert_invalid(response)  # the password changed: every other link is dead


@pytest.mark.parametrize('seconds, valid', [(3599, True), (3601, False)])
def test_link_is_valid_for_one_hour(client, doctor, mailoutbox, monkeypatch, seconds, valid):
    link = _requested_link(client, mailoutbox)
    later = default_token_generator._now() + timedelta(seconds=seconds)
    monkeypatch.setattr(default_token_generator, '_now', lambda: later)

    _location, response = _open(Client(), link)

    if valid:
        assert 'name="password1"' in _html(response)
    else:
        _assert_invalid(response)


def test_link_expiring_while_the_form_is_open_is_refused(client, doctor, mailoutbox,
                                                         monkeypatch):
    browser = Client()
    _open(browser, _requested_link(client, mailoutbox))
    later = default_token_generator._now() + timedelta(seconds=3601)
    monkeypatch.setattr(default_token_generator, '_now', lambda: later)

    _assert_invalid(_set_password(browser))
    doctor.refresh_from_db()
    assert doctor.check_password(PASSWORD)


@pytest.mark.parametrize('tamper', ['token', 'uid', 'garbage_uid', 'other_account'])
def test_tampered_link_is_invalid(client, doctor, mailoutbox, tamper):
    link = _requested_link(client, mailoutbox)
    uid, token = link.uid, link.token
    if tamper == 'token':
        token = token[:-1] + ('a' if token[-1] != 'a' else 'b')
    elif tamper == 'uid':
        uid = uid[:-1] + ('A' if uid[-1] != 'A' else 'B')
    elif tamper == 'garbage_uid':
        uid = '!!!'
    else:
        other = User.objects.create_user('bea@example.com', PASSWORD, phone='+34622918375')
        uid = urlsafe_base64_encode(force_bytes(other.pk))

    _location, response = _open(Client(), Link('en', f'{uid}.{token}'))

    _assert_invalid(response)


def test_link_of_an_account_deactivated_meanwhile_is_invalid(client, doctor, mailoutbox):
    link = _requested_link(client, mailoutbox)
    _deactivate(doctor)

    _location, response = _open(Client(), link)

    _assert_invalid(response)


# --- Sessions, devices and counters -----------------------------------------------------

def test_reset_on_b_ends_a_and_the_new_password_logs_in(client, doctor, mailoutbox,
                                                        login_clock):
    """Acceptance: a session on browser A; the reset on browser B through the emailed
    link; A is logged out, B is not logged in, the new password then logs in (with the
    code)."""
    a = Client()
    assert _login_with_code(a)['Location'] == '/en/welcome/'
    assert _logged_in(a)
    a_key = a.session.session_key

    b = Client()
    _open(b, _requested_link(b, mailoutbox))
    assert _set_password(b)['Location'] == LOGIN

    assert not _logged_in(b)
    # Every session ended (end_all_sessions): A's row is gone and none is current.
    assert not Session.objects.filter(session_key=a_key).exists()
    assert User.objects.values_list('current_session_key', flat=True).get(pk=doctor.pk) is None
    response = a.get('/en/welcome/')
    assert response.status_code == 302
    assert response['Location'].startswith(LOGIN)
    assert not _logged_in(a)

    login_clock.advance(61)  # past the login code's cooldown
    assert _login(b, password=PASSWORD).context['form'].has_error('__all__', 'invalid_login')
    response = _login_with_code(b, password=NEW_PASSWORD)
    assert response['Location'] == '/en/welcome/'
    assert _logged_in(b)


def test_reset_from_a_logged_in_browser_logs_it_out(client, doctor, mailoutbox):
    assert _login_with_code(client)['Location'] == '/en/welcome/'
    _open(client, _requested_link(client, mailoutbox))

    assert _set_password(client)['Location'] == LOGIN

    assert not _logged_in(client)
    assert CHANGED in _html(client.get(LOGIN))
    assert client.get('/en/welcome/')['Location'].startswith(LOGIN)
    # Logged-in pages along the way never recorded the link's secrets.
    link = _link(mailoutbox[-1])
    pages = json.dumps([e.data for e in Event.objects.filter(attempt__kind=Attempt.PAGE)])
    assert link.token not in pages and link.uid not in pages


def test_remembered_device_needs_the_code_after_a_reset(client, doctor, mailoutbox,
                                                        login_clock):
    assert _login_remembering(client)['Location'] == '/en/welcome/'
    client.post('/en/logout/')
    login_clock.advance(61)
    assert _login(client)['Location'] == '/en/welcome/'  # remembered: no code
    client.post('/en/logout/')

    browser = Client()
    _open(browser, _requested_link(browser, mailoutbox))
    _set_password(browser)

    assert _login(client, password=NEW_PASSWORD)['Location'] == '/en/login/verify/'


def test_reset_clears_the_lock_and_both_counts(client, doctor, mailoutbox, lock_clock):
    """Decision 2: locked by 5 wrong passwords, then a reset: the new password logs in at
    once."""
    _lock_by_passwords(client)
    login_failures.record_failure(login_failures.CODE, account_id=doctor.pk)
    assert login_failures.is_locked(account_id=doctor.pk)

    browser = Client()
    _open(browser, _requested_link(browser, mailoutbox))
    _set_password(browser)

    assert not login_failures.is_locked(account_id=doctor.pk)
    assert not login_failures.is_locked(email=NORMALIZED)
    for counter in login_failures.COUNTERS:
        assert login_failures.failure_count(counter, account_id=doctor.pk) == 0
    assert _login(client, password=NEW_PASSWORD)['Location'] == '/en/login/verify/'


# --- Spanish, logs and Sentry -----------------------------------------------------------

def test_spanish_pages_and_mail(client, doctor, mailoutbox):
    page = _html(client.get('/es/password-reset/'))
    assert 'Restablecer su contraseña' in page and 'data-language="es"' in page

    response = _request(client, lang='es')
    assert response['Location'] == '/es/password-reset/sent/'
    assert 'Si existe una cuenta con esa dirección' in _html(client.get(response['Location']))
    [message] = mailoutbox
    assert message.subject == 'Restablezca su contraseña de MyTreatPlan'
    assert 'abra este enlace' in message.body and 'válido durante 1 hora' in message.body
    link = _link(message)
    assert link.lang == 'es'

    browser = Client()
    location, response = _open(browser, link)
    assert location == '/es/password-reset/new/'
    assert 'Elija una nueva contraseña' in _html(response)
    mismatch = _set_password(browser, NEW_PASSWORD, 'otra-cosa-distinta-12', lang='es')
    assert 'Las dos contraseñas no coinciden.' in _html(mismatch)
    assert _set_password(browser, lang='es')['Location'] == '/es/login/'
    assert 'Su contraseña se ha cambiado. Inicie sesión.' in _html(browser.get('/es/login/'))
    _location, response = _open(Client(), link)
    assert 'Este enlace ya no es válido' in _html(response)


def test_link_secrets_never_reach_the_logs(client, doctor, mailoutbox, caplog):
    with caplog.at_level(logging.DEBUG):
        link = _requested_link(client, mailoutbox)
        browser = Client()
        _open(browser, link)
        _set_password(browser)
        _open(Client(), link)
    assert link.token not in caplog.text and link.uid not in caplog.text


def test_no_route_takes_the_uid_or_the_token(client, doctor, mailoutbox):
    """Decision 4: the secrets travel only in the fragment and the POST body. No URL
    pattern has a parameter, and every URL the flow visits is free of them."""
    from django.urls import get_resolver
    from django.urls.resolvers import URLPattern

    def patterns(resolver):
        for entry in resolver.url_patterns:
            if isinstance(entry, URLPattern):
                yield entry
            else:
                yield from patterns(entry)
    for pattern in patterns(get_resolver()):
        if pattern.name in sessions.RESET_PAGES:
            assert not pattern.pattern.converters, pattern.name

    link = _requested_link(client, mailoutbox)
    browser = Client()
    page = browser.get('/en/reset/')
    response = browser.post('/en/reset/', {'link': link.value})
    urls = [page.request['PATH_INFO'], response.request['PATH_INFO'], response['Location']]
    _set_password(browser)
    for url in urls:
        assert link.uid not in url and link.token not in url


def test_reset_link_page_without_javascript_says_it_is_needed(client):
    page = _html(client.get('/en/reset/'))

    assert '<noscript>' in page and 'needs JavaScript' in page
    assert 'js/password-reset-link.js' in page
    assert 'Esta página necesita JavaScript' in _html(client.get('/es/reset/'))


@pytest.mark.parametrize('value', ['', 'no-dot', '.token-only', 'uid-only.', 'x' * 300])
def test_malformed_fragment_value_is_invalid(client, value):
    _assert_invalid(client.post('/en/reset/', {'link': value}))


def test_idle_ended_session_opening_the_link_gets_the_reset_page(client, doctor, mailoutbox,
                                                                 monkeypatch, login_clock):
    """Decision 4: an idle session (3.6) ended on a reset page carries on anonymously there
    instead of bouncing to login."""
    monkeypatch.setattr('accounts.sessions._now', login_clock)
    assert _login_with_code(client)['Location'] == '/en/welcome/'
    link = _requested_link(Client(), mailoutbox)
    login_clock.advance(sessions.IDLE_LIMIT + 1)

    page = client.get('/en/reset/')

    assert page.status_code == 200 and 'data-reset-link-form' in _html(page)
    assert not _logged_in(client)
    location, response = _open(client, link)
    assert location == NEW and 'name="password1"' in _html(response)


# --- Decision 5: the login page's link --------------------------------------------------

@pytest.mark.parametrize('lang, text', [('en', 'Forgot your password?'),
                                        ('es', '¿Ha olvidado su contraseña?')])
def test_login_page_links_to_the_request_page(client, lang, text):
    page = _html(client.get(f'/{lang}/login/'))

    assert f'href="/{lang}/password-reset/"' in page and 'data-forgot-password' in page
    assert text in page
