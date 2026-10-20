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

Every completed login, and every failed one from the second in a row onwards (wrong
passwords and wrong codes counted apart, accounts.login_failures), is recorded in the
event log (`eventlog.service.record_activity`, kind `login`), tied to the account's pk
(entry 4, CAP-3): `_record_login` from `finish_login`, `_record_failure` from the password
check and `login_code_rejected`. Recording never stops a login.

The lockout and the per-IP limit (entry 5): LOCK_THRESHOLD wrong passwords, or as many
wrong codes, in a row lock the account (an unknown email: its blind index) for
LOCK_SECONDS (accounts.login_failures). After the CAPTCHA, a password submission first
reserves one of the IP's hourly login attempts (signup.limits LOGIN_ATTEMPTS), then the
lock is checked, and only then the credentials. A locked account, or an IP over its
limit, gets signup's BLOCKED_MESSAGE, which tells nothing (not which check, how many
attempts are left, or whether the account exists); a right password, and a remembered
device, are refused while the lock lasts. A locked account's pending login ends on the
code pages, back on the login page with that message. Locks (`account_locked`) and the
first IP block in a window (`rejected`, field `ip`) are recorded; a counter, lock or
limit error never stops a login (it is reported, without values).

The pending login grants nothing: the session holds the user's pk, their session auth
hash, its start time and the safe `next`, plus the contact chosen. It lives
CODE_TTL_SECONDS; it ends, back on the login page, when it expires or when the account
changes meanwhile (deactivated, its password or its mobile changed).
"""
import functools
import logging
import time

import sentry_sdk

from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME, get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import NON_FIELD_ERRORS
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

from eventlog import service as eventlog
from eventlog.models import Attempt
from eventlog.service import client_ip
from signup import captcha, limits
from signup.forms import BLOCKED_MESSAGE, BLOCKED_RATE_LIMIT
from signup.steps import (CODE_STATE_SESSION_KEY, SENT, code_help_context, code_state,
                          send_code)
from signup.verification import (CODE_TTL_SECONDS, EMAIL, MOBILE, PURPOSE_LOGIN, SMS,
                                 WHATSAPP)

from . import devices, login_failures
from .crypto import normalize_email_for_index
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

# The login records (entry 4): their event-log steps, events and reasons.
LOGIN_STEP = 'login'  # the login page (the password, the CAPTCHA)
CODE_STEP = 'login-code'  # the login code page
LOGIN_EVENT = 'login'
LOGIN_FAILED_EVENT = 'login_failed'
CHECK_UNAVAILABLE_EVENT = 'check_unavailable'
# The lockout and the per-IP limit (entry 5).
ACCOUNT_LOCKED_EVENT = 'account_locked'
IP_BLOCKED_EVENT = 'rejected'
# How a login passed the second factor.
BY_CODE, BY_REMEMBERED_DEVICE = 'code', 'remembered_device'
# Why a login failed.
WRONG_PASSWORD, UNKNOWN_EMAIL = 'wrong_password', 'unknown_email'
WRONG_CODE, CODE_EXPIRED = 'wrong_code', 'code_expired'


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
        if _locked(account_id=user.pk):
            return _locked_out(request)
        return view(request, user, dict(pending))
    return wrapper


def _locked_out(request):
    """A locked account on the code pages: the pending login ends, back on the login page
    with BLOCKED_MESSAGE (nothing tells why)."""
    end_pending_login(request)
    messages.error(request, BLOCKED_MESSAGE)
    return redirect('login')


# --- The login records (entry 4) --------------------------------------------------------

def _report_counter_failure(action, error):
    """Report a failed login counter call (or the account lookup before it) without any
    personal value: the action and the error class only."""
    message = f'Login failure counter error: {action} ({type(error).__name__})'
    logger.warning(message)
    with sentry_sdk.new_scope() as scope:
        scope.set_tag('login_failures.action', action)
        sentry_sdk.capture_message(message, level='error')


def _record_login(request, user, remembered):
    """A completed login: both failure counts of the account start again, and the login
    is recorded (its time, IP and user agent; `reason` says whether a code was needed)."""
    try:
        login_failures.reset(user.pk)
    except Exception as error:  # noqa: BLE001 - recording must never break the login
        _report_counter_failure('reset', error)
    eventlog.record_activity(
        Attempt.LOGIN, LOGIN_EVENT, request=request, account_id=user.pk,
        step=LOGIN_STEP if remembered else CODE_STEP,
        reason=BY_REMEMBERED_DEVICE if remembered else BY_CODE,
        data={'code_needed': not remembered})


def _locked(account_id=None, email=None):
    """Whether the account (or the typed email) is locked; False when the check fails
    (reported: a lock error never stops a login)."""
    try:
        return login_failures.is_locked(account_id=account_id, email=email)
    except Exception as error:  # noqa: BLE001 - the lockout must never break the login
        _report_counter_failure('is_locked', error)
        return False


def _login_locked(email):
    """Whether the typed `email` may not log in now: its account is locked, or the email
    itself (failures with no account lock it the same way). An empty email is never
    locked (the form refuses it anyway)."""
    if not (email or '').strip():
        return False
    try:
        account = get_user_model()._default_manager.by_email(email).only('pk').first()
    except Exception as error:  # noqa: BLE001 - the lockout must never break the login
        _report_counter_failure('lock_lookup', error)
        account = None
    return _locked(account_id=account.pk if account else None, email=email)


def _record_failure(request, counter, reason=None, *, step, account_id=None, email=None):
    """Count a failed login (`counter`, for the account, or for the typed `email` with no
    account) and record it from the second failure in a row onwards: `reason`, the
    counter (`field`) and the count; an unknown email's record holds it, normalized
    (decision 3). Never a password or a code. With `email` and no `account_id` (a wrong
    password), the account is looked up by the email first: found, `wrong_password` for
    it, else `unknown_email` (an erased account's email is unknown).

    Returns True when this failure locked the account (or the email): its count reached
    LOCK_THRESHOLD; the lock is recorded (`account_locked`) as well."""
    try:
        if account_id is None:
            account = get_user_model()._default_manager.by_email(email).only('pk').first()
            if account is None:
                reason = UNKNOWN_EMAIL
            else:
                account_id, reason = account.pk, WRONG_PASSWORD
        failure = login_failures.record_failure(counter, account_id=account_id, email=email)
    except Exception as error:  # noqa: BLE001 - recording must never break the login
        _report_counter_failure('record_failure', error)
        return False
    for action, error in failure.errors:
        _report_counter_failure(action, error)
    if failure.count < 2:
        return failure.locked
    data = {'count': failure.count}
    if account_id is None:
        data['email'] = normalize_email_for_index(email)
    eventlog.record_activity(Attempt.LOGIN, LOGIN_FAILED_EVENT, request=request,
                             account_id=account_id, step=step, field=counter,
                             reason=reason, data=data)
    if not failure.locked:
        return False
    eventlog.record_activity(Attempt.LOGIN, ACCOUNT_LOCKED_EVENT, request=request,
                             account_id=account_id, step=step, field=counter,
                             reason=reason, data=data)
    return True


def _password_rejected(request, email):
    """A wrong password (`invalid_login`): counted for the account the email belongs to,
    else for the email itself (`_record_failure` looks it up). True when it locked."""
    return _record_failure(request, login_failures.PASSWORD, step=LOGIN_STEP, email=email)


def finish_login(request, user, pending, remembered=False):
    """The one place a login completes after the second factor (3.3's remembered device
    skips the code by calling it right after the password, with `remembered`). The
    account must still be as it was at the password; the pending login is removed before
    `auth_login`. The completed login is recorded (entry 4)."""
    user.refresh_from_db()
    if not _account_unchanged(user, pending):
        return _login_again(request)
    end_pending_login(request)
    response = complete_login(request, user, pending.get('next') or '')
    _record_login(request, user, remembered)
    return response


def login_code_rejected(request, user, form):
    """The one place a rejected login code is handled: called only for a wrong code
    (`invalid_code`) or one no longer valid (`code_expired`), never for an empty submit or
    a check the provider could not make. Counted (and, from the second in a row, recorded)
    as a code failure (entry 4). True when it locked the account (entry 5): the caller
    ends the pending login. No personal value or code is logged."""
    codes = sorted({error.code for errors in form.errors.as_data().values()
                    for error in errors})
    logger.info('login code rejected: reasons=%s', ','.join(codes))
    reason = CODE_EXPIRED if 'code_expired' in codes else WRONG_CODE
    return _record_failure(request, login_failures.CODE, reason, step=CODE_STEP,
                           account_id=user.pk)


def _reserve_login_attempt(request):
    """One of the IP's hourly login attempts (signup.limits LOGIN_ATTEMPTS). Returns
    (blocked, reservation). Blocked (over the limit, or its lock not got): the first
    block of the IP in the window is recorded. A limit error lets the login go on."""
    try:
        reservation = limits.ip_reserve(request, limits.LOGIN_ATTEMPTS)
    except Exception as error:  # noqa: BLE001 - the limit must never break the login
        _report_counter_failure('ip_reserve', error)
        return False, None
    if reservation is not None:
        return False, reservation
    # Over the limit: refused whatever happens to the block's record.
    try:
        first = limits.first_block(request, limits.LOGIN_ATTEMPTS)
    except Exception as error:  # noqa: BLE001 - the record must never lift the block
        _report_counter_failure('first_block', error)
        first = False
    if first:
        eventlog.record_activity(Attempt.LOGIN, IP_BLOCKED_EVENT, request=request,
                                 step=LOGIN_STEP, field='ip', reason=BLOCKED_RATE_LIMIT,
                                 data={'counter': limits.LOGIN_ATTEMPTS})
    return True, None


def _refund_login_attempt(request, reservation):
    """Give back a reserved attempt that did not reach the credentials check."""
    if reservation is None:
        return
    try:
        limits.ip_refund(request, limits.LOGIN_ATTEMPTS, reservation)
    except Exception as error:  # noqa: BLE001 - the limit must never break the login
        _report_counter_failure('ip_refund', error)


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
                # are the guard during an outage). Recorded in the event log, with no
                # account (the credentials are not checked yet).
                eventlog.record_activity(Attempt.LOGIN, CHECK_UNAVAILABLE_EVENT,
                                         request=request, step=LOGIN_STEP, field='captcha',
                                         reason=captcha.TURNSTILE_CHECK)
            blocked, reservation = _reserve_login_attempt(request)
            if blocked:
                # Over the IP's limit: the credentials are not checked.
                form.refuse('rate_limited_ip')
            elif _login_locked(request.POST.get('email', '')):
                # A locked account (or email): refused before the password is checked,
                # so even a right one (or a remembered device) does not log in.
                _refund_login_attempt(request, reservation)
                form.refuse('locked')
            elif form.is_valid():
                user = form.get_user()
                if _locked(account_id=user.pk):
                    # Locked meanwhile (a parallel 5th failure, after the check above):
                    # still refused, no pending login and no remembered-device skip.
                    form.refuse('locked')
                elif not user.phone:
                    # No mobile, no portal login (decision 2): the generic error, no code.
                    form.refuse_login()
                else:
                    start_pending_login(request, user, requested_next(request))
                    if (result == captcha.PASSED
                            and devices.is_remembered(request, user, _now())):
                        # A remembered device (entry 3), only with a passed CAPTCHA: the
                        # same pending login, ended by the one completion, without the code.
                        return finish_login(request, user,
                                            dict(request.session[PENDING_LOGIN_SESSION_KEY]),
                                            remembered=True)
                    return redirect('login_verify')
            elif form.has_error(NON_FIELD_ERRORS, 'invalid_login'):
                if _password_rejected(request, form.cleaned_data.get('email', '')):
                    form.refuse('locked')
            else:
                # An empty email or password: the credentials were not checked.
                _refund_login_attempt(request, reservation)
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
            if login_code_rejected(request, user, form):
                return _locked_out(request)
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
