"""One session per account, with an idle limit (epic-login entry 6, CAP-9).

Every login (the portal's, signup's auto-login, the admin's) records the session's key on
the account (`User.current_session_key`) and deletes the account's previous session, from
Django's `user_logged_in` signal (`on_user_logged_in`), so every way of logging in is
covered. The middleware (`SessionLimitMiddleware`) then ends an authenticated request:

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
import hashlib
import time
from importlib import import_module

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.views import redirect_to_login
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import resolve_url
from django.urls import NoReverseMatch, Resolver404, resolve, reverse
from django.utils.crypto import constant_time_compare
from django.utils.translation import gettext_lazy as _

# The time (epoch seconds) of the session's last authenticated request.
SEEN_SESSION_KEY = 'auth_seen_at'
IDLE_LIMIT = 15 * 60  # seconds without a request that end a logged-in session
SESSION_GRACE = 60 * 60  # seconds a logged-in session (cookie and row) lives unrenewed

REPLACED_MESSAGE = _('You were logged out because your account was logged in on another '
                     'device.')
IDLE_MESSAGE = _('Your session ended after 15 minutes without activity. Please log in '
                 'again.')


# The password reset pages' URL names (accounts.urls): an ended session carries on there.
RESET_PAGES = frozenset({'password_reset', 'password_reset_done', 'password_reset_confirm',
                         'password_reset_new'})


def _now():
    """The current time (epoch seconds); a function so tests can move it."""
    return time.time()


def _session_store():
    return import_module(settings.SESSION_ENGINE).SessionStore


def _set_current_key(user, key, expected=None, check_expected=False):
    """Record `key` as the account's current session; with `check_expected`, only if the
    current one is still `expected`. Returns whether it was recorded."""
    accounts = get_user_model()._default_manager.filter(pk=user.pk)
    if check_expected:
        accounts = accounts.filter(current_session_key=expected)
    recorded = accounts.update(current_session_key=key) > 0
    if recorded:
        user.current_session_key = key
    return recorded


def _replaced_cache_key(session_key):
    # Hashed: the cache never holds a session key, even a deleted one.
    return 'session-replaced:' + hashlib.sha256(session_key.encode()).hexdigest()


def _delete_replaced(session_key):
    """Delete the account's previous session, remembering that it was replaced."""
    _session_store()(session_key).delete()
    cache.set(_replaced_cache_key(session_key), 1, timeout=SESSION_GRACE)


def _pop_replaced(session_key):
    """Whether `session_key` was replaced by a later login; forgotten once asked."""
    cache_key = _replaced_cache_key(session_key)
    if cache.get(cache_key) is None:
        return False
    cache.delete(cache_key)
    return True


def _start_activity(session):
    session[SEEN_SESSION_KEY] = _now()
    session.set_expiry(SESSION_GRACE)


def on_user_logged_in(sender, request, user, **kwargs):
    """A login: the account's previous session is deleted and this one becomes current,
    with its activity clock started."""
    session = getattr(request, 'session', None)
    if session is None:
        return
    if session.session_key is None:
        session.save()
    key = session.session_key
    # Written only over the key read, so two simultaneous logins cannot both replace the
    # same session: the one that loses re-reads the winner's key and replaces it in turn
    # (its browser gets the message).
    while True:
        previous = get_user_model()._default_manager.filter(pk=user.pk).values_list(
            'current_session_key', flat=True).first()
        if previous and previous != key:
            _delete_replaced(previous)
        if _set_current_key(user, key, expected=previous, check_expected=True):
            break
    _start_activity(session)


def end_all_sessions(user):
    """End every session of `user` (the password reset, entry 10): the current one is
    deleted and none is current any more, so any other session it may still have is
    ended by the middleware on its next request."""
    previous = get_user_model()._default_manager.filter(pk=user.pk).values_list(
        'current_session_key', flat=True).first()
    if previous:
        _session_store()(previous).delete()
    _set_current_key(user, None)


def _is_current(user, key):
    return bool(user.current_session_key) and constant_time_compare(
        user.current_session_key, key or '')


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


def _is_reset_page(request):
    try:
        return resolve(request.path_info).url_name in RESET_PAGES
    except Resolver404:
        return False


def _to_login(request):
    """The response that sends an ended session to the login page; None to let the
    request go on (logged out): no login page, already on it, or a password reset page."""
    login_url = _login_url(request)
    if login_url is None or request.path == login_url or _is_reset_page(request):
        return None
    if request.headers.get('HX-Request'):
        # htmx: a full-page redirect, not the login page swapped into a fragment.
        response = HttpResponse(status=204)
        response['HX-Redirect'] = login_url
        return response
    if request.method == 'GET':
        # Back to this page after the next login (the login checks `next` is safe).
        return redirect_to_login(request.get_full_path(), login_url)
    return HttpResponseRedirect(login_url)


class SessionLimitMiddleware:
    """One session per account, ended after IDLE_LIMIT without a request (see the module
    docstring). After AuthenticationMiddleware and MessageMiddleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        start_key = None
        if user.is_authenticated:
            session = request.session
            key = session.session_key
            if not _is_current(user, key):
                # A session from before this rule, or one left after `end_all_sessions`
                # (a later login deletes the older session: it arrives anonymous).
                return self._end(request, None)
            seen = session.get(SEEN_SESSION_KEY)
            if not isinstance(seen, (int, float)) or _now() - seen > IDLE_LIMIT:
                return self._end(request, IDLE_MESSAGE)
            _start_activity(session)
            start_key = key
        elif self._was_replaced(request):
            return self._end(request, REPLACED_MESSAGE)
        response = self.get_response(request)
        still_in = (start_key is not None and request.user.is_authenticated
                    and request.user.pk == user.pk)
        if still_in and self._ended_meanwhile(request, user, start_key):
            # Replaced (or ended) while this request ran: its session must not be saved
            # (its row is gone), and the browser goes to login as it would next time.
            message = REPLACED_MESSAGE if _pop_replaced(start_key) else None
            auth_logout(request)
            if message is not None:
                messages.info(request, message)
            return _to_login(request) or response
        return response

    @staticmethod
    def _was_replaced(request):
        """An anonymous request still carrying the cookie of a session that a later login
        deleted (only a cookie whose session is gone is looked up)."""
        cookie = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
        if not cookie or request.session.session_key is not None:
            return False
        return _pop_replaced(cookie)

    def _end(self, request, message):
        auth_logout(request)
        if message is not None:
            messages.info(request, message)
        response = _to_login(request)
        if response is None:
            return self.get_response(request)
        return response

    @staticmethod
    def _ended_meanwhile(request, user, start_key):
        """Whether this request's session stopped being the account's during the view: a
        login elsewhere or `end_all_sessions` deleted its row. A key cycled without a login
        (Django's `update_session_auth_hash`, after a password change) becomes current,
        unless another login has meanwhile."""
        new_key = request.session.session_key
        if new_key and new_key != start_key:
            return not _set_current_key(user, new_key, expected=start_key,
                                        check_expected=True)
        return not _session_store()().exists(start_key)
