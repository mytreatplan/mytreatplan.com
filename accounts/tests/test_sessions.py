"""One session per Doctor with an idle limit (epic-login entry 6, CAP-9): every row of the
plan's I/O matrix. Two `Client()` instances are two browsers; the idle clock is
`accounts.sessions._now`, moved by the signup tests' Clock."""
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from accounts import sessions
from accounts.sessions import (IDLE_LIMIT, IDLE_MESSAGE, REPLACED_MESSAGE, SEEN_SESSION_KEY,
                               SESSION_GRACE, end_all_sessions)
from accounts.views import PENDING_LOGIN_SESSION_KEY
from signup.tests.conftest import capture_codes, clear_cache  # noqa: F401 - autouse
from signup.tests.conftest import clock  # noqa: F401 - fixture
from signup.tests.helpers import PASSWORD, _complete, _step1
from signup.views import SEEN_SESSION_KEY as SIGNUP_SEEN

from .test_login import _html, _logged_in, _login, _login_with_code, doctor  # noqa: F401
from .test_remembered_device import _login_with_code as _login_remembering
from .test_remembered_device import _mark

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()

WELCOME, LOGIN = '/en/welcome/', '/en/login/'
REPLACED = str(REPLACED_MESSAGE)
IDLE = str(IDLE_MESSAGE)
REPLACED_ES = ('Se ha cerrado su sesión porque se ha iniciado sesión con su cuenta en otro '
               'dispositivo.')
IDLE_ES = ('Su sesión ha finalizado tras 15 minutos sin actividad. Vuelva a iniciar '
           'sesión.')


@pytest.fixture
def idle_clock(clock, monkeypatch):  # noqa: F811
    """The signup clock (limits, fake provider), also the sessions' idle clock."""
    monkeypatch.setattr('accounts.sessions._now', clock)
    return clock


def _browser(lang='en', contact='email'):
    """A new browser where the Doctor logs in (password and code). A second browser in
    the same test uses the other `contact` (the first one's code is used up)."""
    browser = Client()
    response = _login_with_code(browser, contact=contact, lang=lang)
    assert response['Location'] == f'/{lang}/welcome/'
    return browser


def _row_exists(browser):
    return Session.objects.filter(session_key=browser.session.session_key).exists()


def _current_key(user):
    return User.objects.values_list('current_session_key', flat=True).get(pk=user.pk)


# --- One session per account ----------------------------------------------------------------

def test_second_login_deletes_the_first_session(doctor):
    a = _browser()
    a_key = a.session.session_key

    b = _browser(contact='mobile')

    assert _logged_in(b)
    assert not Session.objects.filter(session_key=a_key).exists()
    assert _current_key(doctor) == b.session.session_key


def test_older_browser_is_sent_to_login_with_the_message(doctor):
    a = _browser()
    _browser(contact='mobile')

    response = a.get(WELCOME)

    assert response.status_code == 302
    assert response['Location'] == f'{LOGIN}?next={WELCOME}'
    assert not _logged_in(a)
    assert REPLACED in _html(a.get(LOGIN))
    # The message once only.
    assert REPLACED not in _html(a.get(LOGIN))


def test_newer_browser_stays_logged_in(doctor):
    a = _browser()
    b = _browser(contact='mobile')
    a.get(WELCOME)

    assert b.get(WELCOME).status_code == 200


def test_older_browser_post_goes_to_login(doctor):
    a = _browser()
    _browser(contact='mobile')

    response = a.post('/en/logout/')

    assert response['Location'] == LOGIN
    assert REPLACED in _html(a.get(LOGIN))


def test_older_browser_htmx_request_redirects_the_page(doctor):
    a = _browser()
    _browser(contact='mobile')

    response = a.get(WELCOME, headers={'HX-Request': 'true'})

    assert response.status_code == 204
    assert response['HX-Redirect'] == LOGIN


def test_login_records_the_key_and_starts_the_idle_clock(doctor, idle_clock):
    a = _browser()

    assert _current_key(doctor) == a.session.session_key
    assert a.session[SEEN_SESSION_KEY] == idle_clock()
    assert a.session.get_expiry_age() == SESSION_GRACE
    assert a.cookies['sessionid']['max-age'] == SESSION_GRACE


def test_signup_auto_login_is_the_current_session():
    """Signup's auto-login records its session the same way: a login elsewhere ends it."""
    a = Client()
    assert _complete(a)['Location'] == WELCOME
    user = User.objects.get()
    assert _current_key(user) == a.session.session_key
    assert a.session.get_expiry_age() == SESSION_GRACE

    _browser()

    assert a.get(WELCOME)['Location'] == f'{LOGIN}?next={WELCOME}'
    assert REPLACED in _html(a.get(LOGIN))


def test_any_new_login_ends_an_earlier_session(doctor):
    """The receiver covers every way of logging in, here Django's own `force_login`: the
    earlier session is deleted and its browser sent to login."""
    a = Client()
    a.force_login(doctor)
    a_key = a.session.session_key
    b = Client()

    b.force_login(doctor)

    assert not Session.objects.filter(session_key=a_key).exists()
    assert a.get(WELCOME)['Location'] == f'{LOGIN}?next={WELCOME}'


def test_session_from_before_the_rule_ends_without_the_message(doctor):
    a = _browser()
    User.objects.filter(pk=doctor.pk).update(current_session_key=None)

    assert a.get(WELCOME)['Location'] == f'{LOGIN}?next={WELCOME}'
    page = _html(a.get(LOGIN))
    assert REPLACED not in page and IDLE not in page



def test_simultaneous_logins_the_loser_replaces_the_winner(doctor, monkeypatch):
    """Two logins read the same current key; the other one (C) writes first. This login
    (B) then loses its conditional write, re-reads C's key and replaces it: C gets the
    message, B is current."""
    c = Client()
    c.force_login(doctor)
    c_key = c.session.session_key
    User.objects.filter(pk=doctor.pk).update(current_session_key='a-stale-key')
    real_delete = sessions._delete_replaced
    calls = []

    def delete_then_c_writes(key):
        real_delete(key)
        if not calls:  # C's write lands between B's read and B's write
            User.objects.filter(pk=doctor.pk).update(current_session_key=c_key)
        calls.append(key)
    monkeypatch.setattr(sessions, '_delete_replaced', delete_then_c_writes)
    b = Client()

    b.force_login(doctor)

    assert calls == ['a-stale-key', c_key]
    assert _current_key(doctor) == b.session.session_key
    assert not Session.objects.filter(session_key=c_key).exists()
    assert c.get(WELCOME)['Location'] == f'{LOGIN}?next={WELCOME}'
    assert REPLACED in _html(c.get(LOGIN))
    assert b.get(WELCOME).status_code == 200


def _during_the_view(monkeypatch, action):
    """Run `action` inside the welcome view, before it renders."""
    import accounts.views
    real_render = accounts.views.render

    def render(*args, **kwargs):
        action()
        return real_render(*args, **kwargs)
    monkeypatch.setattr(accounts.views, 'render', render)


def test_replaced_during_a_request_goes_to_login(doctor, monkeypatch):
    """A login elsewhere while this request runs deletes its row: no failed save (400),
    the replaced redirect with the message."""
    a = _browser()
    b = Client()
    _during_the_view(monkeypatch, lambda: b.force_login(doctor))

    response = a.get(WELCOME)

    assert response.status_code == 302
    assert response['Location'] == f'{LOGIN}?next={WELCOME}'
    monkeypatch.undo()
    assert not _logged_in(a)
    assert REPLACED in _html(a.get(LOGIN))
    assert _current_key(doctor) == b.session.session_key


def test_end_all_sessions_during_a_request_goes_to_login(doctor, monkeypatch):
    a = _browser()
    _during_the_view(monkeypatch, lambda: end_all_sessions(doctor))

    response = a.get(WELCOME)

    assert response.status_code == 302
    assert response['Location'] == f'{LOGIN}?next={WELCOME}'
    monkeypatch.undo()
    assert not _logged_in(a)
    page = _html(a.get(LOGIN))
    assert REPLACED not in page and IDLE not in page
    assert _current_key(doctor) is None

# --- The idle limit -------------------------------------------------------------------------

def test_idle_15_minutes_and_a_second_ends_the_session(doctor, idle_clock):
    a = _browser()
    idle_clock.advance(IDLE_LIMIT + 1)

    response = a.get(WELCOME)

    assert response['Location'] == f'{LOGIN}?next={WELCOME}'
    assert not _logged_in(a)
    assert IDLE in _html(a.get(LOGIN))


def test_idle_exactly_15_minutes_is_not_idle(doctor, idle_clock):
    a = _browser()
    idle_clock.advance(IDLE_LIMIT)

    assert a.get(WELCOME).status_code == 200


def test_activity_every_14_minutes_keeps_the_session(doctor, idle_clock):
    a = _browser()
    for _ in range(4):
        idle_clock.advance(14 * 60)
        assert a.get(WELCOME).status_code == 200
        assert a.session[SEEN_SESSION_KEY] == idle_clock()
    assert _logged_in(a)


def test_htmx_requests_count_as_activity(doctor, idle_clock):
    a = _browser()
    idle_clock.advance(10 * 60)
    a.get(WELCOME, headers={'HX-Request': 'true'})
    idle_clock.advance(10 * 60)

    assert a.get(WELCOME).status_code == 200


def test_each_request_renews_the_expiry(doctor):
    a = _browser()
    Session.objects.filter(session_key=a.session.session_key).update(
        expire_date=timezone.now() + timedelta(minutes=5))

    response = a.get(WELCOME)

    expire = Session.objects.get(session_key=a.session.session_key).expire_date
    assert expire > timezone.now() + timedelta(minutes=59)
    assert response.cookies['sessionid']['max-age'] == SESSION_GRACE


def test_idle_htmx_request_redirects_the_page(doctor, idle_clock):
    a = _browser()
    idle_clock.advance(IDLE_LIMIT + 1)

    response = a.get(WELCOME, headers={'HX-Request': 'true'})

    assert response['HX-Redirect'] == LOGIN
    assert IDLE in _html(a.get(LOGIN))


def test_back_after_an_hour_plain_login_page(doctor):
    """After the grace the row is expired: the session is gone, no message."""
    a = _browser()
    Session.objects.filter(session_key=a.session.session_key).update(
        expire_date=timezone.now() - timedelta(seconds=1))

    response = a.get(WELCOME)

    assert response['Location'] == f'{LOGIN}?next={WELCOME}'
    page = _html(a.get(LOGIN))
    assert IDLE not in page and REPLACED not in page


def test_clearsessions_deletes_an_abandoned_session(doctor):
    a = _browser()
    key = a.session.session_key
    Session.objects.filter(session_key=key).update(
        expire_date=timezone.now() - timedelta(seconds=1))

    call_command('clearsessions')

    assert not Session.objects.filter(session_key=key).exists()


# --- The remembered device ------------------------------------------------------------------

def test_remembered_mark_survives_a_replaced_session(doctor):
    a = Client()
    _login_remembering(a)
    mark = _mark(a, doctor)
    assert mark
    _browser(contact='mobile')
    a.get(WELCOME)
    assert not _logged_in(a)

    assert _mark(a, doctor) == mark
    # The next login skips the code.
    assert _login(a)['Location'] == WELCOME


def test_remembered_mark_survives_the_idle_end(doctor, idle_clock):
    a = Client()
    _login_remembering(a)
    mark = _mark(a, doctor)
    idle_clock.advance(IDLE_LIMIT + 1)
    a.get(WELCOME)
    assert not _logged_in(a)

    assert _mark(a, doctor) == mark
    assert _login(a)['Location'] == WELCOME


# --- Anonymous sessions ---------------------------------------------------------------------

def test_anonymous_sessions_are_untouched(client, idle_clock):
    """The signup wizard keeps its own idle key and expiry; no idle-limit key is added."""
    assert _step1(client)['Location'] == '/en/signup/legal/'
    idle_clock.advance(20 * 60)
    assert client.get('/en/signup/legal/').status_code == 200

    assert SIGNUP_SEEN in client.session
    assert SEEN_SESSION_KEY not in client.session
    assert client.session.get_expiry_age() != SESSION_GRACE


def test_pending_login_is_untouched(doctor, idle_clock):
    a = Client()
    assert _login(a)['Location'] == '/en/login/verify/'

    assert PENDING_LOGIN_SESSION_KEY in a.session
    assert SEEN_SESSION_KEY not in a.session
    assert a.get('/en/login/verify/').status_code == 200


# --- End all --------------------------------------------------------------------------------

def test_end_all_sessions(doctor):
    a = _browser()
    key = a.session.session_key

    end_all_sessions(doctor)

    assert not Session.objects.filter(session_key=key).exists()
    assert _current_key(doctor) is None
    assert a.get(WELCOME)['Location'] == f'{LOGIN}?next={WELCOME}'


def test_end_all_sessions_without_a_session(doctor):
    end_all_sessions(doctor)

    assert _current_key(doctor) is None


# --- Admin (decision 3) ---------------------------------------------------------------------

@pytest.fixture
def staff():
    return User.objects.create_superuser('staff@example.net', PASSWORD)


def _admin_browser(staff):
    browser = Client()
    response = browser.post('/admin/login/', {'username': 'staff@example.net',
                                              'password': PASSWORD, 'next': '/admin/'})
    assert response['Location'] == '/admin/'
    return browser


def test_admin_one_session(staff):
    a = _admin_browser(staff)
    b = _admin_browser(staff)

    response = a.get('/admin/')

    assert response['Location'] == '/admin/login/?next=/admin/'
    assert REPLACED in _html(a.get('/admin/login/'))
    assert b.get('/admin/').status_code == 200


def test_admin_idle(staff, idle_clock):
    a = _admin_browser(staff)
    idle_clock.advance(IDLE_LIMIT + 1)

    assert a.get('/admin/')['Location'] == '/admin/login/?next=/admin/'
    assert IDLE in _html(a.get('/admin/login/'))


def test_password_change_keeps_the_session_current(staff):
    """Django's own password change cycles the session key without a login: the new key
    stays the current one."""
    a = _admin_browser(staff)
    new = 'An0ther-long-passphrase!'
    response = a.post(f'/admin/accounts/user/{staff.pk}/password/',
                      {'password1': new, 'password2': new, 'usable_password': 'true'})
    assert response.status_code == 302, response.content

    assert _current_key(staff) == a.session.session_key
    assert a.get('/admin/').status_code == 200


# --- Spanish --------------------------------------------------------------------------------

def test_spanish_replaced_message(doctor):
    a = _browser(lang='es')
    _browser(lang='es', contact='mobile')

    assert a.get('/es/welcome/')['Location'] == '/es/login/?next=/es/welcome/'
    assert REPLACED_ES in _html(a.get('/es/login/'))


def test_spanish_idle_message(doctor, idle_clock):
    a = _browser(lang='es')
    idle_clock.advance(IDLE_LIMIT + 1)

    a.get('/es/welcome/')

    assert IDLE_ES in _html(a.get('/es/login/'))

