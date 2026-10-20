"""The remembered device (epic-login entry 3, CAP-8): every row of the plan's I/O matrix.
Ticking "Remember me on this device/browser for 90 days" with the right code gives the
browser a signed mark for that account (accounts.devices, decision 1); a later right
password and CAPTCHA with a valid mark logs in at once, through `finish_login`."""
import pytest
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import Client

from accounts import devices
from accounts.forms import INVALID_LOGIN_MESSAGE
from accounts.views import PENDING_LOGIN_SESSION_KEY
from conftest import TURNSTILE_FAIL_SECRET
from signup.forms import BLOCKED_MESSAGE
from signup.tests.conftest import capture_codes, clear_cache  # noqa: F401 - autouse
from signup.tests.conftest import clock  # noqa: F401 - fixture
from signup.tests.helpers import MOBILE_E164, NORMALIZED, PASSWORD, _current_code, _wrong
from signup.verification import EMAIL, PURPOSE_LOGIN

from .test_login import _html, _logged_in, _login, doctor  # noqa: F401
from .test_login_code import login_clock  # noqa: F401 - fixture

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()

VERIFY, CODE, WELCOME = '/en/login/verify/', '/en/login/code/', '/en/welcome/'
DAY = 24 * 60 * 60
LABEL = 'Remember me on this device/browser for 90 days'


def _to_code(client, email=NORMALIZED, lang='en', **kwargs):
    response = _login(client, email=email, lang=lang, **kwargs)
    assert response['Location'] == f'/{lang}/login/verify/'
    assert client.post(f'/{lang}/login/verify/', {'contact': 'email'})['Location'] == \
        f'/{lang}/login/code/'


def _post_code(client, code, remember=True, lang='en'):
    data = {'code': code}
    if remember:
        data['remember'] = 'on'
    return client.post(f'/{lang}/login/code/', data)


def _login_with_code(client, remember=True, email=NORMALIZED, **kwargs):
    _to_code(client, email=email, **kwargs)
    return _post_code(client, _current_code(EMAIL, email, PURPOSE_LOGIN), remember)


def _remembered(client, doctor):
    """A browser where `doctor` ticked the box with the right code, then logged out."""
    response = _login_with_code(client)
    assert response['Location'] == WELCOME
    client.post('/en/logout/')
    assert not _logged_in(client)
    return response


def _mark(client, user):
    cookie = client.cookies.get(devices.cookie_name(user))
    return cookie.value if cookie else None


def _second_doctor():
    return User.objects.create_user('bea@example.com', PASSWORD, phone='+34622918375')


# --- The checkbox -------------------------------------------------------------------------

def test_code_page_has_the_unticked_checkbox(client, doctor):
    _to_code(client)

    html = _html(client.get(CODE))

    assert LABEL in html
    assert 'type="checkbox" name="remember"' in html
    assert 'checked' not in html


def test_spanish_label(client, doctor):
    _to_code(client, lang='es')

    html = _html(client.get('/es/login/code/'))

    assert 'Recordarme en este dispositivo/navegador durante 90 días' in html
    assert LABEL not in html


# --- Giving the mark ----------------------------------------------------------------------

def test_ticked_right_code_logs_in_and_sets_the_mark(client, doctor, login_clock):
    response = _login_with_code(client)

    assert response['Location'] == WELCOME
    assert _logged_in(client)
    cookie = response.cookies[devices.cookie_name(doctor)]
    assert cookie['max-age'] == 90 * DAY
    assert cookie['httponly'] is True
    assert cookie['samesite'] == 'Lax'
    assert bool(cookie['secure']) is False  # SESSION_COOKIE_SECURE is off here
    assert devices.is_valid(cookie.value, doctor, login_clock())
    # No personal value, in the cookie's name or its content.
    data = signing.loads(cookie.value, salt=devices.SIGNING_SALT)
    assert set(data) == {'u', 't', 'h'}
    for text in (cookie.key, cookie.value, str(data)):
        assert NORMALIZED not in text and MOBILE_E164 not in text
    assert str(doctor.pk) not in cookie.key


def test_mark_is_secure_when_debug_is_off(client, doctor, settings):
    # The production settings make the session cookie Secure exactly when DEBUG is off.
    settings.SESSION_COOKIE_SECURE = True

    response = _login_with_code(client)

    assert response.cookies[devices.cookie_name(doctor)]['secure'] is True


def test_unticked_right_code_logs_in_without_a_mark(client, doctor):
    response = _login_with_code(client, remember=False)

    assert response['Location'] == WELCOME
    assert _logged_in(client)
    assert devices.cookie_name(doctor) not in response.cookies
    assert _mark(client, doctor) is None


def test_ticked_wrong_code_sets_no_mark(client, doctor):
    _to_code(client)

    response = _post_code(client, _wrong(_current_code(EMAIL, NORMALIZED, PURPOSE_LOGIN)))

    assert response.status_code == 200
    assert not _logged_in(client)
    assert client.session.get(PENDING_LOGIN_SESSION_KEY)
    assert devices.cookie_name(doctor) not in response.cookies
    assert _mark(client, doctor) is None


# --- Using the mark -----------------------------------------------------------------------

def test_remembered_device_skips_the_code(client, doctor, capture_codes):
    _remembered(client, doctor)
    sent = len(capture_codes.records)

    response = _login(client)

    assert response.status_code == 302
    assert response['Location'] == WELCOME
    assert _logged_in(client)
    assert PENDING_LOGIN_SESSION_KEY not in client.session
    assert len(capture_codes.records) == sent  # no code sent
    # The skip gives no new mark: the first one's 90 days stand.
    assert devices.cookie_name(doctor) not in response.cookies


def test_remembered_device_honours_the_safe_next(client, doctor):
    _remembered(client, doctor)

    assert _login(client, query='?next=/es/welcome/')['Location'] == '/es/welcome/'
    client.post('/en/logout/')
    assert _login(client, query='?next=https://evil.example/')['Location'] == WELCOME


def test_still_remembered_after_logout(client, doctor):
    _remembered(client, doctor)
    assert _login(client)['Location'] == WELCOME
    client.post('/en/logout/')

    assert _mark(client, doctor)
    assert _login(client)['Location'] == WELCOME


def test_mark_lasts_a_fixed_90_days(client, doctor, login_clock):
    _remembered(client, doctor)

    login_clock.advance(60 * DAY)
    assert _login(client)['Location'] == WELCOME  # a later login does not extend it
    client.post('/en/logout/')
    login_clock.advance(30 * DAY - 1)
    assert _login(client)['Location'] == WELCOME
    client.post('/en/logout/')
    login_clock.advance(2)  # 90 days and a second

    response = _login(client)

    assert response['Location'] == VERIFY
    assert not _logged_in(client)


def test_another_browser_needs_the_code(client, doctor):
    _remembered(client, doctor)

    other = Client()
    assert _login(other)['Location'] == VERIFY
    assert not _logged_in(other)


def test_another_account_needs_its_own_code(client, doctor):
    _remembered(client, doctor)
    mark = _mark(client, doctor)
    bea = _second_doctor()

    response = _login(client, email='bea@example.com')

    assert response['Location'] == VERIFY
    assert not _logged_in(client)
    assert _mark(client, doctor) == mark  # A's mark untouched

    # Both accounts remembered in one browser: neither overwrites the other.
    assert client.post(VERIFY, {'contact': 'email'})['Location'] == CODE
    response = _post_code(client, _current_code(EMAIL, 'bea@example.com', PURPOSE_LOGIN))
    assert response['Location'] == WELCOME
    client.post('/en/logout/')
    assert _mark(client, doctor) == mark and _mark(client, bea)
    assert devices.cookie_name(doctor) != devices.cookie_name(bea)
    assert _login(client, email='bea@example.com')['Location'] == WELCOME
    client.post('/en/logout/')
    assert _login(client)['Location'] == WELCOME


def test_wrong_password_skips_nothing(client, doctor):
    _remembered(client, doctor)

    response = _login(client, password='wrong-password-123')

    assert response.status_code == 200
    assert not _logged_in(client)
    assert response.context['form'].non_field_errors() == [INVALID_LOGIN_MESSAGE]
    assert PENDING_LOGIN_SESSION_KEY not in client.session


def test_failed_captcha_skips_nothing(client, doctor, settings):
    _remembered(client, doctor)
    settings.TURNSTILE_SECRET_KEY = TURNSTILE_FAIL_SECRET

    response = _login(client)

    assert response.status_code == 200
    assert not _logged_in(client)
    assert response.context['form'].non_field_errors() == [BLOCKED_MESSAGE]


def test_unavailable_captcha_skips_nothing(client, doctor, turnstile):
    _remembered(client, doctor)
    turnstile.error = TimeoutError()

    response = _login(client)

    # The login goes on as in 3.2 (decision 1 there), but to the code: no skip.
    assert response['Location'] == VERIFY
    assert not _logged_in(client)
    assert client.session.get(PENDING_LOGIN_SESSION_KEY)


@pytest.mark.parametrize('tamper', ['modified', 'garbage', 'foreign', 'other_salt'])
def test_tampered_mark_is_ignored(client, doctor, login_clock, tamper):
    _remembered(client, doctor)
    name, mark = devices.cookie_name(doctor), _mark(client, doctor)
    if tamper == 'modified':
        value = mark[:-2] + ('AA' if not mark.endswith('AA') else 'BB')
    elif tamper == 'garbage':
        value = 'not-a-mark'
    elif tamper == 'foreign':
        # Another account's genuine mark, moved under this account's cookie.
        value = devices.issue(_second_doctor(), login_clock())
    else:
        value = signing.dumps({'u': str(doctor.pk), 't': int(login_clock()),
                               'h': doctor.get_session_auth_hash()}, salt='another')
    client.cookies[name] = value

    response = _login(client)

    assert response['Location'] == VERIFY
    assert not _logged_in(client)


def test_password_change_forgets_the_device(client, doctor):
    _remembered(client, doctor)
    doctor.set_password('another-Password-456')
    doctor.save()

    response = _login(client, password='another-Password-456')

    assert response['Location'] == VERIFY
    assert not _logged_in(client)


@pytest.mark.parametrize('change', ['inactive', 'no_mobile'])
def test_inactive_or_no_mobile_gets_the_generic_error(client, doctor, change):
    _remembered(client, doctor)
    if change == 'inactive':
        doctor.is_active = False
    else:
        doctor.phone = ''
    doctor.save()

    response = _login(client)

    assert response.status_code == 200
    assert not _logged_in(client)
    assert response.context['form'].non_field_errors() == [INVALID_LOGIN_MESSAGE]
    assert PENDING_LOGIN_SESSION_KEY not in client.session


def test_skip_goes_through_finish_login(client, doctor, monkeypatch):
    _remembered(client, doctor)
    calls = []
    from accounts import views
    original = views.finish_login
    monkeypatch.setattr(views, 'finish_login',
                        lambda request, user, pending: calls.append(dict(pending))
                        or original(request, user, pending))

    assert _login(client)['Location'] == WELCOME
    assert len(calls) == 1
    assert calls[0]['user'] == str(doctor.pk) and calls[0]['contact'] is None
