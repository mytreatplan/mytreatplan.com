"""The login's guard (accounts.views): the login records, the lockout and the per-IP limit.

Every completed login, and every failed one from the second in a row onwards (wrong
passwords and wrong codes counted apart, accounts.login_failures), is recorded in the
event log (`eventlog.service.record_activity`, kind `login`), tied to the account's pk
(epic-login entry 4, CAP-3): `record_login` from `finish_login`, `record_failure` from
the password check and `login_code_rejected`. Recording never stops a login.

The lockout and the per-IP limit (entry 5): LOCK_THRESHOLD wrong passwords, or as many
wrong codes, in a row lock the account (an unknown email: its blind index) for
LOCK_SECONDS (accounts.login_failures). After the CAPTCHA, a password submission first
reserves one of the IP's hourly login attempts (signup.limits LOGIN_ATTEMPTS,
`reserve_login_attempt`), then the lock is checked (`login_locked`), and only then the
credentials. Locks (`account_locked`) and the first IP block in a window (`rejected`,
field `ip`) are recorded; a counter, lock or limit error never stops a login (it is
reported, without values).

The password reset (accounts.password_reset) shares the IP reservation
(`reserve_ip_attempt`), the unavailable-CAPTCHA record (`record_unavailable_captcha`) and
the error reporter (`report_error`).
"""
import logging

import sentry_sdk
from django.contrib.auth import get_user_model

from eventlog import service as eventlog
from eventlog.models import Attempt
from signup import captcha, limits
from signup.forms import BLOCKED_RATE_LIMIT

from . import login_failures
from .crypto import normalize_email_for_index

# The login's logger: the guard's lines keep the name they had in accounts.views.
logger = logging.getLogger('accounts.views')

# The login records (entry 4): their event-log steps, events and reasons.
LOGIN_STEP = 'login'  # the login page (the password, the CAPTCHA)
CODE_STEP = 'login-code'  # the login code page
LOGIN_EVENT = 'login'
LOGIN_FAILED_EVENT = 'login_failed'
# Also the password reset's (accounts.password_reset).
CHECK_UNAVAILABLE_EVENT = 'check_unavailable'
# The lockout and the per-IP limit (entry 5). A block (also the password reset's): the
# IP's first in a window (field `ip`), or a reset mail over its per-email limit (`email`).
ACCOUNT_LOCKED_EVENT = 'account_locked'
REJECTED_EVENT = 'rejected'
# How a login passed the second factor.
BY_CODE, BY_REMEMBERED_DEVICE = 'code', 'remembered_device'
# Why a login failed.
WRONG_PASSWORD, UNKNOWN_EMAIL = 'wrong_password', 'unknown_email'
WRONG_CODE, CODE_EXPIRED = 'wrong_code', 'code_expired'


def report_error(log, prefix, tag, action, error):
    """Report a failed step without any personal value: `prefix`, the action and the
    error class only, as a warning on `log` and a Sentry error tagged `tag`: `action`."""
    message = f'{prefix}: {action} ({type(error).__name__})'
    log.warning(message)
    with sentry_sdk.new_scope() as scope:
        scope.set_tag(tag, action)
        sentry_sdk.capture_message(message, level='error')


def report_counter_failure(action, error):
    """Report a failed login counter call (or the account lookup before it) without any
    personal value: the action and the error class only."""
    report_error(logger, 'Login failure counter error', 'login_failures.action', action,
                 error)


def record_unavailable_captcha(request, step):
    """The CAPTCHA could not be checked (captcha.UNAVAILABLE) and the page goes on: recorded
    in the event log, with no account (nothing is checked yet)."""
    eventlog.record_activity(Attempt.LOGIN, CHECK_UNAVAILABLE_EVENT, request=request,
                             step=step, field='captcha', reason=captcha.TURNSTILE_CHECK)


def reserve_ip_attempt(request, counter, *, step, report):
    """One of the IP's hourly `counter` attempts (signup.limits). Returns (blocked,
    reservation). Blocked (over the limit, or its lock not got): the first block of the
    IP in the window is recorded (`rejected`, field `ip`, at `step`). A limit error,
    reported through `report`, lets the request go on."""
    try:
        reservation = limits.ip_reserve(request, counter)
    except Exception as error:  # noqa: BLE001 - the limit must never break the page
        report('ip_reserve', error)
        return False, None
    if reservation is not None:
        return False, reservation
    # Over the limit: refused whatever happens to the block's record.
    try:
        first = limits.first_block(request, counter)
    except Exception as error:  # noqa: BLE001 - the record must never lift the block
        report('first_block', error)
        first = False
    if first:
        eventlog.record_activity(Attempt.LOGIN, REJECTED_EVENT, request=request,
                                 step=step, field='ip', reason=BLOCKED_RATE_LIMIT,
                                 data={'counter': counter})
    return True, None


# --- The login records (entry 4) --------------------------------------------------------

def record_login(request, user, remembered):
    """A completed login: both failure counts of the account start again, and the login
    is recorded (its time, IP and user agent; `reason` says whether a code was needed)."""
    try:
        login_failures.reset(user.pk)
    except Exception as error:  # noqa: BLE001 - recording must never break the login
        report_counter_failure('reset', error)
    eventlog.record_activity(
        Attempt.LOGIN, LOGIN_EVENT, request=request, account_id=user.pk,
        step=LOGIN_STEP if remembered else CODE_STEP,
        reason=BY_REMEMBERED_DEVICE if remembered else BY_CODE,
        data={'code_needed': not remembered})


def _account_by_email(email):
    """The account of `email` (only its pk loaded), or None."""
    return get_user_model()._default_manager.by_email(email).only('pk').first()


def account_locked(account_id=None, email=None):
    """Whether the account (or the typed email) is locked; False when the check fails
    (reported: a lock error never stops a login)."""
    try:
        return login_failures.is_locked(account_id=account_id, email=email)
    except Exception as error:  # noqa: BLE001 - the lockout must never break the login
        report_counter_failure('is_locked', error)
        return False


def login_locked(email):
    """Whether the typed `email` may not log in now: its account is locked, or the email
    itself (failures with no account lock it the same way). An empty email is never
    locked (the form refuses it anyway)."""
    if not (email or '').strip():
        return False
    try:
        account = _account_by_email(email)
    except Exception as error:  # noqa: BLE001 - the lockout must never break the login
        report_counter_failure('lock_lookup', error)
        account = None
    return account_locked(account_id=account.pk if account else None, email=email)


def record_failure(request, counter, reason=None, *, step, account_id=None, email=None):
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
            account = _account_by_email(email)
            if account is None:
                reason = UNKNOWN_EMAIL
            else:
                account_id, reason = account.pk, WRONG_PASSWORD
        failure = login_failures.record_failure(counter, account_id=account_id, email=email)
    except Exception as error:  # noqa: BLE001 - recording must never break the login
        report_counter_failure('record_failure', error)
        return False
    for action, error in failure.errors:
        report_counter_failure(action, error)
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


def password_rejected(request, email):
    """A wrong password (`invalid_login`): counted for the account the email belongs to,
    else for the email itself (`record_failure` looks it up). True when it locked."""
    return record_failure(request, login_failures.PASSWORD, step=LOGIN_STEP, email=email)


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
    return record_failure(request, login_failures.CODE, reason, step=CODE_STEP,
                          account_id=user.pk)


# --- The per-IP limit (entry 5) ---------------------------------------------------------

def reserve_login_attempt(request):
    """One of the IP's hourly login attempts (signup.limits LOGIN_ATTEMPTS). Returns
    (blocked, reservation), as `reserve_ip_attempt`."""
    return reserve_ip_attempt(request, limits.LOGIN_ATTEMPTS, step=LOGIN_STEP,
                              report=report_counter_failure)


def refund_login_attempt(request, reservation):
    """Give back a reserved attempt that did not reach the credentials check."""
    if reservation is None:
        return
    try:
        limits.ip_refund(request, limits.LOGIN_ATTEMPTS, reservation)
    except Exception as error:  # noqa: BLE001 - the limit must never break the login
        report_counter_failure('ip_refund', error)
