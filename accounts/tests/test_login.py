"""Login tracer bullet (epic-login entry 1): every row of the plan's I/O matrix: login and
logout, the CAPTCHA, the safe `next`, the generic error, the portal switch, the nav, and
Spanish. Since entry 2 a right password is followed by the code (`_login_with_code`); the
second factor itself is tested in test_login_code.py."""
import logging
import re

import pytest
from django.contrib.auth import SESSION_KEY, get_user_model
from django.test import Client

from accounts.erasure import erase_user
from accounts.forms import INVALID_LOGIN_MESSAGE
from conftest import TURNSTILE_FAIL_SECRET, TURNSTILE_TEST_TOKEN
from eventlog.models import Attempt, Event
from signup.captcha import TOKEN_FIELD
from signup.forms import BLOCKED_MESSAGE
from signup.tests.conftest import clear_cache  # noqa: F401 - autouse, this module only
from signup.tests.conftest import clock  # noqa: F401 - fixture
from signup.tests.helpers import (EMAIL_ADDRESS, MOBILE_E164, NORMALIZED, PASSWORD,
                                  _complete, _current_code)
from signup.verification import EMAIL, PURPOSE_LOGIN, SMS

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()

SPANISH_ERROR = 'El correo electrónico o la contraseña no son correctos.'


@pytest.fixture
def doctor(client):
    """A Doctor created by signup (the whole wizard), then logged out."""
    response = _complete(client)
    assert response['Location'] == '/en/welcome/'
    client.logout()
    return User.objects.by_email(NORMALIZED).get()


def _login(client, email=EMAIL_ADDRESS, password=PASSWORD, lang='en', query='', extra=None,
           **fields):
    """Post the login form with the Turnstile test token, as the widget would; `extra`
    goes to the test client (e.g. secure, REMOTE_ADDR)."""
    return client.post(f'/{lang}/login/{query}', {
        'email': email, 'password': password, TOKEN_FIELD: TURNSTILE_TEST_TOKEN, **fields},
        **(extra or {}))


def _login_with_code(client, contact='email', lang='en', extra=None, **kwargs):
    """`_login` (the password), then the second factor: choose `contact` ('email' or
    'mobile') and post the right code. Returns the response to the code."""
    response = _login(client, lang=lang, extra=extra, **kwargs)
    assert response.status_code == 302, response.context['form'].errors
    assert response['Location'] == f'/{lang}/login/verify/'
    response = client.post(f'/{lang}/login/verify/', {'contact': contact}, **(extra or {}))
    assert response['Location'] == f'/{lang}/login/code/'
    if contact == 'email':
        code = _current_code(EMAIL, NORMALIZED, PURPOSE_LOGIN)
    else:
        code = _current_code(SMS, MOBILE_E164, PURPOSE_LOGIN)
    return client.post(f'/{lang}/login/code/', {'code': code}, **(extra or {}))


def _logged_in(client):
    return SESSION_KEY in client.session


def _html(response):
    return response.content.decode()


# --- Success and next ------------------------------------------------------------------

@pytest.mark.parametrize('email', [NORMALIZED, NORMALIZED.upper(), EMAIL_ADDRESS])
def test_signup_user_logs_in_with_email_in_any_case(client, doctor, email):
    response = _login_with_code(client, email=email)

    assert response.status_code == 302
    assert response['Location'] == '/en/welcome/'
    assert int(client.session[SESSION_KEY]) == doctor.pk
    assert 'Welcome to MyTreatPlan' in _html(client.get('/en/welcome/'))


def test_login_page_shows_the_form_and_the_widget(client):
    response = client.get('/en/login/')

    assert response.status_code == 200
    html = _html(response)
    assert '<h1' in html and 'Log in' in html
    assert 'name="email"' in html and 'name="password"' in html
    assert 'autocomplete="current-password"' in html
    assert 'data-turnstile data-sitekey="' in html
    assert 'data-turnstile-error' in html and 'data-turnstile-pending' in html
    assert 'js/turnstile.js' in html
    assert 'href="/en/signup/"' in html
    assert 'name="csrfmiddlewaretoken"' in html
    assert 'noindex' in html


def test_safe_next_is_honoured(client, doctor, clock):
    response = _login_with_code(client, query='?next=/en/welcome/')
    assert response['Location'] == '/en/welcome/'

    client.logout()
    clock.advance(61)  # past the login code's cooldown
    response = _login_with_code(client, next='/es/welcome/')
    assert response['Location'] == '/es/welcome/'


@pytest.mark.parametrize('unsafe', ['https://evil.example/', '//evil.example/',
                                    'http://testserver/en/welcome/', 'javascript:alert(1)'])
def test_unsafe_next_goes_to_welcome(client, doctor, unsafe):
    """Another host, a scheme-relative URL, and plain HTTP while the request is HTTPS are
    refused, as Django's LoginView refuses them."""
    response = _login_with_code(client, next=unsafe, extra={'secure': True})

    assert response.status_code == 302
    assert response['Location'] == '/en/welcome/'
    assert _logged_in(client)


@pytest.mark.parametrize('relative', ['foo', 'logout'])
def test_relative_next_is_a_literal_url(client, doctor, relative):
    """A safe `next` without a slash is a relative URL, never reversed as a view name."""
    response = _login_with_code(client, next=relative)

    assert response.status_code == 302
    assert response['Location'] == relative
    assert _logged_in(client)

    response = client.get(f'/en/login/?next={relative}')  # logged in already
    assert response.status_code == 302
    assert response['Location'] == relative
    assert _logged_in(client)


def test_login_page_keeps_only_a_safe_next(client):
    assert 'name="next" value="/en/welcome/"' in _html(client.get('/en/login/?next=/en/welcome/'))
    html = _html(client.get('/en/login/?next=https://evil.example/'))
    assert 'name="next"' not in html  # (the language switcher keeps the query string)


def test_already_logged_in_is_sent_to_welcome(client, doctor):
    client.force_login(doctor)

    response = client.get('/en/login/')

    assert response.status_code == 302
    assert response['Location'] == '/en/welcome/'


def test_anonymous_on_welcome_goes_to_login(client):
    response = client.get('/en/welcome/')

    assert response.status_code == 302
    assert response['Location'] == '/en/login/?next=/en/welcome/'


# --- Failures: one generic error ------------------------------------------------------

def _deactivate(doctor):
    doctor.is_active = False
    doctor.save(update_fields=['is_active'])


def _erase(doctor, settings):
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 30
    staff = User.objects.create_superuser('staff@example.net', PASSWORD)
    assert erase_user(doctor, by=staff)


@pytest.mark.parametrize('case', ['wrong_password', 'unknown_email', 'inactive', 'erased',
                                  'malformed_email'])
def test_every_failure_gets_the_same_generic_error(client, doctor, settings, case):
    email, password = NORMALIZED, PASSWORD
    if case == 'wrong_password':
        password = 'wrong-password-123'
    elif case == 'unknown_email':
        email = 'nobody@example.com'
    elif case == 'inactive':
        _deactivate(doctor)
    elif case == 'erased':
        _erase(doctor, settings)
    elif case == 'malformed_email':
        email = 'not-an-email'

    response = _login(client, email=email, password=password)

    assert response.status_code == 200
    assert not _logged_in(client)
    form = response.context['form']
    assert form.non_field_errors() == [INVALID_LOGIN_MESSAGE]
    assert not form.has_error('email') and not form.has_error('password')
    html = _html(response)
    assert str(INVALID_LOGIN_MESSAGE) in html
    # The email is kept, the password is not.
    assert f'value="{email}"' in html
    assert password not in html


def test_failure_pages_are_identical_but_for_the_email(client, doctor):
    """A wrong password and an unknown email give the same page (the CSRF token and the
    typed email aside): nothing tells whether the account exists."""
    def page(email, password):
        html = _html(_login(client, email=email, password=password))
        html = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', '', html)
        return html.replace(email, 'EMAIL')

    assert page(NORMALIZED, 'wrong-password-123') == page('nobody@example.com', PASSWORD)


def test_empty_fields_are_required(client):
    response = _login(client, email='', password='')

    assert response.status_code == 200
    form = response.context['form']
    assert form.has_error('email', 'required') and form.has_error('password', 'required')
    assert not _logged_in(client)


# --- CAPTCHA -----------------------------------------------------------------------------

def test_failed_captcha_is_blocked_without_checking_credentials(client, doctor, settings,
                                                                 turnstile, monkeypatch):
    settings.TURNSTILE_SECRET_KEY = TURNSTILE_FAIL_SECRET
    checked = []
    before = len(turnstile.calls)  # signup's own check
    monkeypatch.setattr('accounts.forms.authenticate',
                        lambda *args, **kwargs: checked.append(1))

    response = _login(client)

    assert response.status_code == 200
    assert not _logged_in(client)
    assert response.context['form'].non_field_errors() == [BLOCKED_MESSAGE]
    assert checked == []
    assert len(turnstile.calls) == before + 1
    assert f'value="{EMAIL_ADDRESS}"' in _html(response)  # as typed


def test_missing_token_is_blocked(client, doctor, turnstile):
    before = len(turnstile.calls)
    response = client.post('/en/login/', {'email': NORMALIZED, 'password': PASSWORD})

    assert response.context['form'].non_field_errors() == [BLOCKED_MESSAGE]
    assert not _logged_in(client)
    assert len(turnstile.calls) == before  # a missing token fails without asking Cloudflare


def test_captcha_is_checked_with_the_token_and_ip(client, doctor, turnstile):
    _login(client, extra={'REMOTE_ADDR': '203.0.113.9'})

    assert turnstile.calls[-1]['response'] == TURNSTILE_TEST_TOKEN
    assert turnstile.calls[-1]['remoteip'] == '203.0.113.9'


def test_unavailable_captcha_lets_the_login_through_and_is_logged(client, doctor, turnstile,
                                                                   caplog):
    """Since entry 4 in the event log (`check_unavailable`), no longer a server-log line."""
    turnstile.error = TimeoutError()

    with caplog.at_level(logging.WARNING):
        response = _login(client)

    assert response['Location'] == '/en/login/verify/'  # on to the second factor
    assert not _logged_in(client)
    [event] = Event.objects.filter(attempt__kind=Attempt.LOGIN, event='check_unavailable')
    assert (event.step, event.field, event.reason) == ('login', 'captcha', 'turnstile')
    assert event.attempt.account_id is None and event.attempt.ended_at is not None
    assert [record for record in caplog.records if record.name == 'accounts.views'] == []
    for record in caplog.records:
        assert NORMALIZED not in record.getMessage() and PASSWORD not in record.getMessage()


def test_unavailable_captcha_still_checks_the_password(client, doctor, turnstile):
    turnstile.error = TimeoutError()

    response = _login(client, password='wrong-password-123')

    assert response.context['form'].non_field_errors() == [INVALID_LOGIN_MESSAGE]
    assert not _logged_in(client)


def test_login_needs_csrf(doctor):
    client = Client(enforce_csrf_checks=True)

    response = _login(client)

    assert response.status_code == 403
    assert not _logged_in(client)


def test_password_is_hidden_from_error_reports(client, doctor, rf):
    """sensitive_post_parameters: Django's error reports mask the password."""
    from accounts import views

    request = rf.post('/en/login/', {'email': NORMALIZED, 'password': PASSWORD})
    request.session = client.session
    request.user = doctor
    views.login(request)
    assert request.sensitive_post_parameters == ('password',)


# --- Logout ------------------------------------------------------------------------------

def test_logout_ends_the_session(client, doctor):
    client.force_login(doctor)

    response = client.post('/en/logout/')

    assert response.status_code == 302
    assert response['Location'] == '/en/login/'
    assert not _logged_in(client)
    page = _html(client.get('/en/login/'))
    assert 'You have logged out.' in page
    assert client.get('/en/welcome/')['Location'] == '/en/login/?next=/en/welcome/'


def test_logout_by_get_does_not_log_out(client, doctor):
    client.force_login(doctor)

    response = client.get('/en/logout/')

    assert response.status_code == 405
    assert _logged_in(client)


def test_logout_needs_csrf(doctor):
    client = Client(enforce_csrf_checks=True)
    client.force_login(doctor)

    assert client.post('/en/logout/').status_code == 403
    assert _logged_in(client)


# --- Portal off --------------------------------------------------------------------------

@pytest.mark.parametrize('url', ['/en/login/', '/es/login/', '/en/logout/', '/es/logout/'])
def test_portal_off_is_404(client, settings, url):
    settings.ROOT_URLCONF = 'mytreatplan.urls'  # the real URLconf, built with the switch off
    assert not settings.PORTAL_ENABLED

    assert client.get(url).status_code == 404
    assert client.post(url).status_code == 404
    assert 'data-portal-link' not in _html(client.get('/en/'))


# --- Nav ---------------------------------------------------------------------------------

def _nav(html):
    return re.search(r'<nav[^>]*aria-label="(?:Main|Principal)".*?</nav>', html, re.S).group(0)


def test_nav_for_anonymous_visitors(client):
    nav = _nav(_html(client.get('/en/')))

    assert 'href="/en/login/"' in nav and 'data-portal-link="login"' in nav
    assert 'href="/en/signup/"' in nav and 'data-portal-link="signup"' in nav
    assert 'data-portal-link="logout"' not in nav and 'data-portal-link="portal"' not in nav
    # The portal pages' header shows no portal link to anonymous visitors.
    assert 'data-portal-link' not in _nav(_html(client.get('/en/login/')))


@pytest.mark.parametrize('url', ['/en/', '/en/welcome/'])
def test_nav_for_a_logged_in_doctor(client, doctor, url):
    client.force_login(doctor)

    nav = _nav(_html(client.get(url)))

    assert 'href="/en/welcome/"' in nav and 'data-portal-link="portal"' in nav
    assert 'Client portal' in nav
    assert re.search(r'<form method="post" action="/en/logout/"', nav)
    assert 'name="csrfmiddlewaretoken"' in nav
    assert re.search(r'data-portal-link="logout">Log out</button>', nav)
    assert 'data-portal-link="login"' not in nav and 'data-portal-link="signup"' not in nav


def test_nav_logout_button_logs_out(client, doctor):
    """The nav's form posts to logout with its CSRF token."""
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(doctor)
    nav = _nav(_html(csrf_client.get('/en/')))
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', nav).group(1)

    response = csrf_client.post('/en/logout/', {'csrfmiddlewaretoken': token})

    assert response['Location'] == '/en/login/'
    assert not _logged_in(csrf_client)


# --- Spanish -----------------------------------------------------------------------------

def test_spanish_page_error_and_messages(client, doctor):
    html = _html(client.get('/es/login/'))
    assert '<html lang="es"' in html
    assert 'Iniciar sesión' in html and 'Contraseña' in html
    assert 'Inicie sesión en su portal de clientes de MyTreatPlan.' in html
    assert 'href="/es/signup/"' in html and 'Cree una cuenta' in html

    response = _login(client, lang='es', password='wrong-password-123')
    assert SPANISH_ERROR in _html(response)

    response = _login_with_code(client, lang='es')
    assert response['Location'] == '/es/welcome/'
    nav = _nav(_html(client.get('/es/welcome/')))
    assert 'Portal de clientes' in nav and 'Cerrar sesión' in nav

    response = client.post('/es/logout/')
    assert response['Location'] == '/es/login/'
    assert 'Ha cerrado la sesión.' in _html(client.get('/es/login/'))


def test_spanish_blocked_message(client, doctor, settings):
    settings.TURNSTILE_SECRET_KEY = TURNSTILE_FAIL_SECRET

    html = _html(_login(client, lang='es'))

    assert 'No hemos podido procesar su solicitud. Inténtelo de nuevo más tarde.' in html
