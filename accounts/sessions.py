"""One session per account, with an idle limit (epic-login entry 6, CAP-9).

The code lives in `mytp_core.sessions` (epic-admin-access entry 3), with the portal's
policy (`mytp_core.policy.PORTAL`: 15 minutes idle, 1 hour grace). This module keeps the
portal's names, its translated messages (the package has no locale), the password reset
pages, the login page choice (`_login_url`: the admin's for an admin request) and the
middleware path in settings; the core class's seams (`_now`, `_delete_replaced`,
`login_url`) go through this module's functions, so what a test patches here applies.

Every login (the portal's, signup's auto-login, the admin's) records the session's key on
the account (`User.current_session_key`) and deletes the account's previous session, from
Django's `user_logged_in` signal (`on_user_logged_in`, connected in accounts.apps), so
every way of logging in is covered. The middleware (`SessionLimitMiddleware`) then ends an
authenticated request:

- whose session is not the account's current one: logged out, to the login page;
- that comes more than IDLE_LIMIT after the session's last request (decision 2): logged
  out, to the login page with IDLE_MESSAGE.

The previous session's row is deleted by the new login, so its browser's next request
is anonymous: the deleted key is remembered (hashed, in the cache, for SESSION_GRACE), and
a request still carrying it goes to the login page with REPLACED_MESSAGE (decision 1),
once.

Otherwise it stores the request's time as the last activity (htmx requests included) and
renews the session's own expiry (cookie and database row) to SESSION_GRACE, longer than
the idle limit, so an idle session can still be read, and its message shown, when the
Doctor comes back within that hour. After it the row is expired and the daily
`clearsessions` job (deploy/jobs.conf) deletes it. The rules apply to every authenticated
session, admin and staff included (decision 3); anonymous sessions (signup's wizard, the
pending login) are untouched.

Ending a session (`auth_logout`) leaves the remembered-device cookie (accounts.devices).
`end_all_sessions(user)` ends every session of an account (for the password reset).

The password reset pages (RESET_PAGES, epic-login entry 10, decision 4) never bounce to
login: a session ended on them (replaced, idle, or ended meanwhile) carries on anonymously
there, so the reset link opened in an idle browser still works.
"""
import time

from django.conf import settings
from django.shortcuts import resolve_url
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _
from mytp_core import sessions as core
from mytp_core.policy import PORTAL

POLICY = PORTAL

# The time (epoch seconds) of the session's last authenticated request.
SEEN_SESSION_KEY = core.SEEN_SESSION_KEY
IDLE_LIMIT = POLICY.idle_limit_seconds  # seconds without a request that end a session
SESSION_GRACE = POLICY.session_grace_seconds  # seconds a session lives unrenewed

REPLACED_MESSAGE = _('You were logged out because your account was logged in on another '
                     'device.')
IDLE_MESSAGE = _('Your session ended after 15 minutes without activity. Please log in '
                 'again.')


# The password reset pages' URL names (accounts.urls): an ended session carries on there.
RESET_PAGES = frozenset({'password_reset', 'password_reset_done', 'password_reset_confirm',
                         'password_reset_new'})


class PortalSingleSession(core.SingleSession):
    """The core class with the portal's cache key; its seams go through this module's
    functions."""
    seen_session_key = SEEN_SESSION_KEY
    replaced_prefix = 'session-replaced:'

    def _now(self):
        return _now()

    def _delete_replaced(self, session_key):
        _delete_replaced(session_key)

    def login_url(self, request):
        return _login_url(request)


_sessions = PortalSingleSession(POLICY, replaced_message=REPLACED_MESSAGE,
                                idle_message=IDLE_MESSAGE, pass_through_url_names=RESET_PAGES,
                                session_key_field='current_session_key')


def _now():
    """The current time (epoch seconds); a function so tests can move it."""
    return time.time()


def _replaced_cache_key(session_key):
    # Hashed: the cache never holds a session key, even a deleted one.
    return _sessions._replaced_cache_key(session_key)


def _delete_replaced(session_key):
    """Delete the account's previous session, remembering that it was replaced."""
    core.SingleSession._delete_replaced(_sessions, session_key)


def _pop_replaced(session_key):
    """Whether `session_key` was replaced by a later login; forgotten once asked."""
    return _sessions._pop_replaced(session_key)


def on_user_logged_in(sender, request, user, **kwargs):
    """A login: the account's previous session is deleted and this one becomes current,
    with its activity clock started."""
    _sessions.on_user_logged_in(sender, request, user, **kwargs)


def end_all_sessions(user):
    """End every session of `user` (the password reset, entry 10): the current one is
    deleted and none is current any more, so any other session it may still have is
    ended by the middleware on its next request."""
    _sessions.end_all_sessions(user)


def _login_url(request):
    """The admin's login page for an admin request, else the portal's (None when the
    portal is off: the request then goes on, logged out)."""
    try:
        admin_root = reverse('admin:index')
    except NoReverseMatch:
        admin_root = None
    if admin_root and request.path.startswith(admin_root):
        return reverse('admin:login')
    try:
        return resolve_url(settings.LOGIN_URL)
    except NoReverseMatch:
        return None


class SessionLimitMiddleware(core.SessionLimitMiddleware):
    """One session per account, ended after IDLE_LIMIT without a request (see the module
    docstring). After AuthenticationMiddleware and MessageMiddleware."""
    single_session = _sessions
