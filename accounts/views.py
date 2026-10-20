"""The registered area's entry and exit: login, logout and the welcome page.

Login checks the CAPTCHA (signup.captcha) first, then the credentials (accounts.forms).
A right password logs no one in: it starts a pending login (`start_pending_login`), and
the second factor (epic-login entry 2, CAP-7) follows on two pages: `login_verify`, the
choice of the account's email or mobile, and `login_code`, the code sent there through
signup's verification provider and sending helper (signup.steps.send_code: WhatsApp,
else SMS; the per-contact cooldown and hourly limits, purpose `login`). Only an approved
code completes the login, through `finish_login` and `complete_login`, the one place a
login is finished; with "Remember me" ticked it also gives the browser the account's
remembered-device mark (accounts.devices, entry 3), and a later right password with that
valid mark completes the login through `finish_login` at once, without the code. Logout
is a POST only (and leaves the mark).

The pending login grants nothing: the session holds the user's pk, their session auth
hash, its start time and the safe `next`, plus the contact chosen. It lives
CODE_TTL_SECONDS; it ends, back on the login page, when it expires or when the account
changes meanwhile (deactivated, its password or its mobile changed).
"""
import functools
import logging
import time

from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME, get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render, resolve_url
from django.utils.crypto import constant_time_compare
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from eventlog.service import client_ip
from signup import captcha, limits
from signup.steps import (CODE_STATE_SESSION_KEY, SENT, code_help_context, code_state,
                          send_code)
from signup.verification import (CODE_TTL_SECONDS, EMAIL, MOBILE, PURPOSE_LOGIN, SMS,
                                 WHATSAPP)

from . import devices
from .forms import LoginCodeForm, LoginContactForm, LoginForm, mask_email, mask_phone

logger = logging.getLogger(__name__)

# Where a login lands without a safe `next`.
LOGIN_REDIRECT = 'welcome'


# The session's pending login (see the module docstring). Never a contact or a code.
PENDING_LOGIN_SESSION_KEY = 'login_pending'
# The login code page's name in signup.steps' code state (and its event-log step).
CODE_PAGE = 'login'
# When a pending login has expired or its account changed: back to the login page.
LOGIN_EXPIRED_MESSAGE = gettext_lazy('Your login has expired. Please log in again.')


def _now():
    """The current time (epoch seconds); a function so tests can move it."""
    return time.time()


def safe_next(request, url):
    """`url` when it is a safe `next` (this host, and HTTPS when the request is, as
    Django's LoginView checks it), else ''."""
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return url
    return ''


def requested_next(request):
    """The `next` of the POST or the query string when it is safe, else ''."""
    url = request.POST.get(REDIRECT_FIELD_NAME, request.GET.get(REDIRECT_FIELD_NAME, ''))
    return safe_next(request, url)


def redirect_after_login(request):
    """To the safe `next` as given (a literal URL, never reversed as a view name, as
    Django's LoginView does), else to the welcome page."""
    return HttpResponseRedirect(requested_next(request) or resolve_url(LOGIN_REDIRECT))


def complete_login(request, user, next_url=None):
    """Finish the login of `user`: log them in and send them to `next_url` (already
    checked; by default the request's safe `next`), or the welcome page. Called only once
    the second factor has passed (`finish_login`)."""
    auth_login(request, user)
    if next_url is None:
        return redirect_after_login(request)
    return HttpResponseRedirect(safe_next(request, next_url) or resolve_url(LOGIN_REDIRECT))


# --- The pending login ------------------------------------------------------------------

def start_pending_login(request, user, next_url):
    """After a right password: remember who is logging in, nothing more. Replaces any
    pending login of this session."""
    request.session[PENDING_LOGIN_SESSION_KEY] = {
        'user': str(user.pk),
        'hash': user.get_session_auth_hash(),
        'started': _now(),
        'next': next_url,
        # The contacts as they were at the password (blind indexes, never the values).
        'email_index': user.email_index,
        'phone_index': user.phone_index,
        'contact': None,
        # Whether a code was sent to the chosen contact during this pending login.
        'sent': False,
    }
    _clear_code_state(request)


def end_pending_login(request):
    """Forget the session's pending login and its code page's state."""
    request.session.pop(PENDING_LOGIN_SESSION_KEY, None)
    _clear_code_state(request)


def _clear_code_state(request):
    states = request.session.get(CODE_STATE_SESSION_KEY)
    if states and CODE_PAGE in states:
        request.session[CODE_STATE_SESSION_KEY] = {
            page: state for page, state in states.items() if page != CODE_PAGE}


def _account_unchanged(user, pending):
    """The account can still log in as it could at the password: active, the same session
    auth hash (so the same password), a mobile (decision 2), and the same email and
    mobile (so a code goes to, and is checked against, the contacts of that moment)."""
    return (user is not None and user.is_active and bool(user.phone)
            and constant_time_compare(user.get_session_auth_hash(), pending.get('hash', ''))
            and constant_time_compare(user.email_index, pending.get('email_index', ''))
            and constant_time_compare(user.phone_index, pending.get('phone_index', '')))


def _pending_user(pending):
    return get_user_model()._default_manager.filter(pk=pending.get('user')).first()


def _login_again(request):
    """End the pending login: back to the login page with LOGIN_EXPIRED_MESSAGE. Says
    nothing of why (expired, or the account changed)."""
    end_pending_login(request)
    messages.info(request, LOGIN_EXPIRED_MESSAGE)
    return redirect('login')


def _pending_login_view(view):
    """The second-factor pages: only with a valid pending login, else back to the login
    page (with a message when it expired or its account changed). Calls
    `view(request, user, pending)`."""
    @functools.wraps(view)
    def wrapper(request):
        if request.user.is_authenticated:
            end_pending_login(request)
            return redirect_after_login(request)
        pending = request.session.get(PENDING_LOGIN_SESSION_KEY)
        if not isinstance(pending, dict):
            return redirect('login')
        if _now() - pending.get('started', 0) >= CODE_TTL_SECONDS:
            return _login_again(request)
        user = _pending_user(pending)
        if not _account_unchanged(user, pending):
            return _login_again(request)
        return view(request, user, dict(pending))
    return wrapper


def finish_login(request, user, pending):
    """The one place a login completes after the second factor (3.3's remembered device
    skips the code by calling it right after the password). The account must still be as
    it was at the password; the pending login is removed before `auth_login`."""
    user.refresh_from_db()
    if not _account_unchanged(user, pending):
        return _login_again(request)
    end_pending_login(request)
    return complete_login(request, user, pending.get('next') or '')


def login_code_rejected(request, user, form):
    """The one place a rejected login code is handled: called only for a wrong code
    (`invalid_code`) or one no longer valid (`code_expired`), never for an empty submit or
    a check the provider could not make. The hook for the login counters and lockout
    (entries 4 and 5). No personal value or code is logged."""
    codes = sorted({error.code for errors in form.errors.as_data().values()
                    for error in errors})
    logger.info('login code rejected: reasons=%s', ','.join(codes))


@sensitive_post_parameters('password')
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
def login(request):
    if request.user.is_authenticated:
        return redirect_after_login(request)
    # Opening or posting the login page ends any earlier pending login, whatever the
    # outcome: only a right password now starts a new one.
    end_pending_login(request)
    if request.method != 'POST':
        form = LoginForm(request)
    else:
        form = LoginForm(request, data=request.POST)
        result = captcha.verify_turnstile(request.POST.get(captcha.TOKEN_FIELD, ''),
                                          client_ip(request))
        if result == captcha.FAILED:
            form.refuse('captcha_failed')
        else:
            if result == captcha.UNAVAILABLE:
                # The login goes on (decision 1: the login lockout and IP limits, entry 5,
                # are the guard during an outage). No personal value is logged.
                logger.warning('check_unavailable: step=login field=captcha reason=%s',
                               captcha.TURNSTILE_CHECK)
            if form.is_valid():
                user = form.get_user()
                if not user.phone:
                    # No mobile, no portal login (decision 2): the generic error, no code.
                    form.refuse_login()
                else:
                    start_pending_login(request, user, requested_next(request))
                    if (result == captcha.PASSED
                            and devices.is_remembered(request, user, _now())):
                        # A remembered device (entry 3), only with a passed CAPTCHA: the
                        # same pending login, ended by the one completion, without the code.
                        return finish_login(request, user,
                                            dict(request.session[PENDING_LOGIN_SESSION_KEY]))
                    return redirect('login_verify')
    return render(request, 'accounts/login.html', {
        'form': form,
        'next': requested_next(request),
        **captcha.widget_context(),
    })


def _contact(user, kind):
    return user.email if kind == EMAIL else user.phone


@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
@_pending_login_view
def login_verify(request, user, pending):
    """The choice of where the login code goes: the email or the mobile, both masked
    (decision 3). Choosing sends the code (the mobile's by WhatsApp, else SMS) unless
    one went to that same contact within the cooldown (it is still valid)."""
    form = LoginContactForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        kind = form.cleaned_data['contact']
        contact = _contact(user, kind)
        since = limits.seconds_since_last_send(contact, PURPOSE_LOGIN)
        if not (pending.get('contact') == kind and since is not None
                and since < limits.COOLDOWN_SECONDS):
            _clear_code_state(request)
            outcome = send_code(request, None, CODE_PAGE, contact, PURPOSE_LOGIN,
                                EMAIL if kind == EMAIL else WHATSAPP)
            request.session[PENDING_LOGIN_SESSION_KEY] = {
                **pending, 'contact': kind, 'sent': outcome.status == SENT}
        return redirect('login_code')
    return render(request, 'accounts/login_choose.html', {
        'email': mask_email(user.email),
        'phone': mask_phone(user.phone),
    })


@sensitive_post_parameters('code')
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
@_pending_login_view
def login_code(request, user, pending):
    """The login code: check it (an approved one completes the login), or send a new one
    (`action`: `resend` over the channel used last, `sms` for the mobile), within the
    limits, as signup's code pages."""
    kind = pending.get('contact')
    if kind not in (EMAIL, MOBILE):
        return redirect('login_verify')
    contact = _contact(user, kind)
    action = request.POST.get('action') if request.method == 'POST' else None
    if action is not None and action != 'resend' and not (action == 'sms' and kind == MOBILE):
        return redirect('login_code')  # an unknown action, or SMS on the email page
    if action is not None:
        if action == 'sms':
            channel, message = SMS, _('We have sent you a new code by SMS.')
        else:
            last = code_state(request, CODE_PAGE).get('channel')
            channel = EMAIL if kind == EMAIL else (SMS if last == SMS else WHATSAPP)
            message = _('We have sent you a new code.')
        outcome = send_code(request, None, CODE_PAGE, contact, PURPOSE_LOGIN, channel,
                            resend=True)
        if outcome.status == SENT:
            request.session[PENDING_LOGIN_SESSION_KEY] = {**pending, 'sent': True}
            messages.success(request, message)
        return redirect('login_code')

    if request.method == 'POST':
        form = LoginCodeForm(request.POST, kind=kind, contact=contact)
        if form.is_valid():
            response = finish_login(request, user, pending)
            if form.cleaned_data['remember'] and request.user.is_authenticated:
                devices.remember(response, user, _now())
            return response
        if form.has_error('code', 'invalid_code') or form.has_error('code', 'code_expired'):
            login_code_rejected(request, user, form)
    else:
        form = LoginCodeForm(kind=kind, contact=contact)
    code = code_help_context(request, CODE_PAGE, form, contact, PURPOSE_LOGIN,
                             sms=kind == MOBILE)
    # "Sent" only for a send that succeeded during this pending login (not, e.g., a code
    # of an earlier login, already used, while this one's send met the cooldown).
    code['sent_recently'] = code['sent_recently'] and bool(pending.get('sent'))
    return render(request, 'accounts/login_code.html', {
        'form': form,
        'kind': kind,
        'contact': mask_email(user.email) if kind == EMAIL else mask_phone(user.phone),
        'code': code,
    })


@require_POST
@csrf_protect
@never_cache
def logout(request):
    auth_logout(request)
    messages.success(request, _('You have logged out.'))
    return redirect('login')


@login_required
def welcome(request):
    """Landing page right after signup or login. Its content comes with the onboarding spec."""
    return render(request, 'accounts/welcome.html')
