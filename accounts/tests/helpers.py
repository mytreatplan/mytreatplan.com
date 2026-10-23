"""The accounts tests' shared flow helpers and test values: the login (password, code,
remembered device), the failed logins and the lockout, the login records, and the
request headers. Their fixtures are in conftest.py."""
from django.contrib.auth import SESSION_KEY, get_user_model

from accounts import devices, login_failures
from accounts.erasure import erase_user
from conftest import TURNSTILE_TEST_TOKEN
from eventlog.models import Attempt, Event
from signup.captcha import TOKEN_FIELD
from signup.tests.helpers import EMAIL_ADDRESS, MOBILE_E164, NORMALIZED, PASSWORD, _current_code
from signup.verification import EMAIL, PURPOSE_LOGIN, SMS

User = get_user_model()

# The request headers of a known browser (the client's IP and user agent).
IP = '203.0.113.42'
AGENT = 'Mozilla/5.0 (AccountsTestBrowser)'
META = {'REMOTE_ADDR': IP, 'HTTP_USER_AGENT': AGENT}
WRONG_PASSWORD = 'wrong-password-123'
VERIFY, CODE, LOGIN, WELCOME = ('/en/login/verify/', '/en/login/code/', '/en/login/',
                                '/en/welcome/')


# --- The login ----------------------------------------------------------------------------

def _login(client, email=EMAIL_ADDRESS, password=PASSWORD, lang='en', query='', extra=None,
           **fields):
    """Post the login form with the Turnstile test token, as the widget would; `extra`
    goes to the test client (e.g. secure, REMOTE_ADDR)."""
    return client.post(f'/{lang}/login/{query}', {
        'email': email, 'password': password, TOKEN_FIELD: TURNSTILE_TEST_TOKEN, **fields},
        **(extra or {}))


def _right_code(contact='email', email=NORMALIZED, phone=MOBILE_E164):
    """The login code sent to the email or the mobile (the fake provider's)."""
    if contact == 'email':
        return _current_code(EMAIL, email, PURPOSE_LOGIN)
    return _current_code(SMS, phone, PURPOSE_LOGIN)


def _login_with_code(client, contact='email', lang='en', extra=None, **kwargs):
    """`_login` (the password), then the second factor: choose `contact` ('email' or
    'mobile') and post the right code. Returns the response to the code."""
    response = _login(client, lang=lang, extra=extra, **kwargs)
    assert response.status_code == 302, response.context['form'].errors
    assert response['Location'] == f'/{lang}/login/verify/'
    response = client.post(f'/{lang}/login/verify/', {'contact': contact}, **(extra or {}))
    assert response['Location'] == f'/{lang}/login/code/'
    return client.post(f'/{lang}/login/code/', {'code': _right_code(contact)},
                       **(extra or {}))


def _to_code(client, contact='email', lang='en', extra=None, **kwargs):
    """The password (`_login`, with `extra` and `kwargs`), then the choice of `contact`:
    the code page is next."""
    assert _login(client, lang=lang, extra=extra, **kwargs)['Location'] == \
        f'/{lang}/login/verify/'
    response = client.post(f'/{lang}/login/verify/', {'contact': contact})
    assert response['Location'] == f'/{lang}/login/code/'
    return response


def _post_code(client, code, lang='en', extra=None, **data):
    """Post `code` (and `data`, e.g. remember='on') on the login code page."""
    return client.post(f'/{lang}/login/code/', {'code': code, **data}, **(extra or {}))


def _login_remembering(client, remember=True, email=NORMALIZED, **kwargs):
    """`_to_code`, then the right code with "Remember me" ticked (unless not `remember`)."""
    _to_code(client, email=email, **kwargs)
    data = {'remember': 'on'} if remember else {}
    return _post_code(client, _right_code(email=email), **data)


def _logged_in(client):
    return SESSION_KEY in client.session


def _html(response):
    return response.content.decode()


def _mark(client, user):
    """The browser's remembered-device mark for `user` (accounts.devices), or None."""
    cookie = client.cookies.get(devices.cookie_name(user))
    return cookie.value if cookie else None


def _during_the_view(monkeypatch, action):
    """Run `action` inside the welcome view, before it renders."""
    import accounts.views
    real_render = accounts.views.render

    def render(*args, **kwargs):
        action()
        return real_render(*args, **kwargs)
    monkeypatch.setattr(accounts.views, 'render', render)


# --- The account --------------------------------------------------------------------------

def _deactivate(doctor):
    doctor.is_active = False
    doctor.save(update_fields=['is_active'])


def _erase(doctor, settings):
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 30
    staff = User.objects.create_superuser('staff@example.net', PASSWORD)
    assert erase_user(doctor, by=staff)


# --- Failed logins and the lockout --------------------------------------------------------

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


# --- The records ----------------------------------------------------------------------------

def _records(**filters):
    """The login records (kind `login`), oldest first."""
    return list(Event.objects.filter(attempt__kind=Attempt.LOGIN, **filters)
                .select_related('attempt').order_by('created_at', 'id'))


def _failures():
    return _records(event='login_failed')


def _logins():
    return _records(event='login')


def _locks(**filters):
    return _records(event='account_locked', **filters)


def _ip_blocks():
    return _records(event='rejected', field='ip')


def _messages(sentry_capture):
    """The messages of the captured Sentry events (not their breadcrumbs)."""
    return [event.get('logentry', {}).get('message') or event.get('message')
            for event in sentry_capture.events]
