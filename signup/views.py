"""The signup wizard. Generic over signup.steps.STEPS: no step is named here.

Plus the resume flow (signup entry 5): when step 1's email belongs to another active
pending registration (checked as soon as the email field is left, `check_email`, or on
Continue without JavaScript), the person can take it over with a code sent to that email.
The code also verifies the email; the saved data comes back into step 1 for review.

Plus cancel and the idle timeout (signup entry 8): "Cancel registration" (`cancel`)
removes the session's pending registration; after IDLE_TIMEOUT without a signup request
the signup session keys are cleared (`idle_check`), the pending registration stays and
the email check offers the resume.

Plus the anti-abuse protections (signup entry 9): Cloudflare Turnstile on every step 1
POST (signup.captcha), and the per-IP limits (signup.limits) on signup starts, live email
checks and code sends (signup.steps.send_code). A blocked request gets the generic
BLOCKED_MESSAGE, which reveals nothing, and is logged `rejected` with the reason.
"""
import functools
import math
import time

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import IntegrityError, transaction
from django.forms.utils import ErrorDict
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from eventlog import service as eventlog
from eventlog.models import Attempt
from legal.models import LegalChoice

from .forms import (BLOCKED_CAPTCHA, BLOCKED_MESSAGE, EMAIL_EXISTS, EMAIL_PENDING,
                    EMAIL_REGISTERED_MESSAGE, PHONE_EXISTS,
                    PHONE_REGISTERED_MESSAGE, REGISTERED_CODES, ResumeCodeForm,
                    email_is_registered, failure_reason, normalize_email, phone_is_registered)
from . import captcha, limits
from .models import PendingRegistration
from .retention import remove_pending
from .steps import (CODE_STATE_SESSION_KEY, HOURLY, SENT, STEPS, CodeStep,
                    MobileCodeStep, code_state, get_step, log_ip_blocked, send_code)
from .verification import CODE_TTL_SECONDS, EMAIL, PURPOSE_RESUME, SMS

# The session holds only the pending registration's id, and the event log attempt's id.
SESSION_KEY = 'signup_pending_id'
ATTEMPT_SESSION_KEY = 'signup_attempt_id'
# While a resume is offered: {'id': the matched row's id, 'sent': whether a code was
# sent}. Never a contact: the page reads it from the row.
RESUME_SESSION_KEY = 'signup_resume'

# The resume page's step name in the event log.
RESUME_STEP = 'resume'

# The time (epoch seconds) of the last signup request, for the idle timeout.
SEEN_SESSION_KEY = 'signup_seen_at'
IDLE_TIMEOUT = 30 * 60  # seconds
IDLE_EVENT = 'idle_timeout'
IDLE_REASON = 'idle timeout'
CANCELLED = 'cancelled'


def _now():
    """The current time (epoch seconds); a function so tests can move it."""
    return time.time()


def clear_signup_session(request):
    """Forget this browser's signup (cancel, idle timeout): the pending registration's
    id, the attempt, the resume on offer and the code pages' state. The rest of the
    Django session (language, CSRF, a logged-in user) stays."""
    for key in (SESSION_KEY, ATTEMPT_SESSION_KEY, RESUME_SESSION_KEY, CODE_STATE_SESSION_KEY):
        request.session.pop(key, None)


def _idle_timed_out(request):
    """True if the session holds a signup in progress (a pending registration, an
    attempt or a resume on offer) and the last signup request was more than IDLE_TIMEOUT
    ago. Then the signup session keys are cleared, the session's open attempt gets an
    `idle_timeout` event (it stays open: a resume ends it `abandoned`, else the hourly
    purge `timeout`) and the idle message is queued for the next page. Without signup
    state nothing is lost, so nothing times out. The time of this request is stored
    either way."""
    now = _now()
    seen = request.session.get(SEEN_SESSION_KEY)
    request.session[SEEN_SESSION_KEY] = now
    if not isinstance(seen, (int, float)) or now - seen <= IDLE_TIMEOUT:
        return False
    if not any(key in request.session
               for key in (SESSION_KEY, ATTEMPT_SESSION_KEY, RESUME_SESSION_KEY)):
        return False
    attempt = eventlog.get_attempt(request.session.get(ATTEMPT_SESSION_KEY))
    if attempt is not None and attempt.ended_at is None:
        eventlog.record(attempt, IDLE_EVENT, request=request, reason=IDLE_REASON)
    clear_signup_session(request)
    messages.info(request, _('Your session ended after 30 minutes without activity. '
                             'Enter your email to continue where you left off.'))
    return True


def idle_check(view):
    """Apply the idle timeout before a signup page. A request after the timeout is not
    processed: it goes to an empty step 1 with the idle message. (The live email check
    applies it itself: it answers with nothing, and the message shows on the next
    page.)"""
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if _idle_timed_out(request):
            return redirect('signup')
        return view(request, *args, **kwargs)
    return wrapper


def step_url(index):
    """The first step lives at the URL named `signup`; the others at signup/<slug>/."""
    if index == 0:
        return reverse('signup')
    return reverse('signup_step', args=[STEPS[index].slug])


def _pending_registration(request):
    pk = request.session.get(SESSION_KEY)
    if not pk:
        return None
    try:
        # A row past the 24 h window counts as gone: the hourly purge deletes it.
        return PendingRegistration.objects.active().get(pk=pk)
    except (PendingRegistration.DoesNotExist, ValidationError):
        request.session.pop(SESSION_KEY, None)
        request.session.pop(ATTEMPT_SESSION_KEY, None)
        return None


def _attempt(request, pending, index):
    """The open event log attempt of this signup (from the session, else the pending
    registration). Data received at step 1 with none starts one; page views do not."""
    attempt = eventlog.get_attempt(request.session.get(ATTEMPT_SESSION_KEY))
    if attempt is None and pending is not None and pending.attempt_id:
        attempt = eventlog.get_attempt(pending.attempt_id)
    if attempt is not None and attempt.ended_at is not None:
        attempt = None
    if attempt is None and index == 0:
        attempt = eventlog.start_attempt(Attempt.SIGNUP)
    if attempt is None:
        request.session.pop(ATTEMPT_SESSION_KEY, None)
    else:
        request.session[ATTEMPT_SESSION_KEY] = str(attempt.pk)
    return attempt


def _log_rejected(request, attempt, step, form):
    """One `rejected` event per form error: the field, the reason (policy wording or the
    error code) and the step's loggable values, never a password or a code."""
    data = step.log_data(form)
    for field, errors in form.errors.as_data().items():
        for error in errors:
            eventlog.record(attempt, 'rejected', request=request, step=step.slug,
                            field='' if field == NON_FIELD_ERRORS else field,
                            reason=failure_reason(error.code), data=data)


def _log_unavailable_checks(request, attempt, step, form):
    """One `check_unavailable` event per external check the form could not make (the
    value was let through): the field, the check, and the step's loggable values."""
    checks = getattr(form, 'unavailable_checks', ())
    if not checks:
        return
    data = step.log_data(form)
    for field, check in checks:
        eventlog.record(attempt, 'check_unavailable', request=request, step=step.slug,
                        field=field, reason=check, data=data)


def find_pending_match(email, exclude=None):
    """The latest other active pending registration with this email, or None. `exclude`
    is the session's own row. A mobile in another signup is allowed: that signup is
    removed when an account is created (`_create_account`)."""
    rows = PendingRegistration.objects.active()
    if exclude is not None:
        rows = rows.exclude(pk=exclude.pk)
    return rows.matching_email(email).order_by('-created_at').first()


def start_resume(request, row):
    """Offer to resume `row` (the resume page takes over)."""
    _clear_resume_code_state(request)
    request.session[RESUME_SESSION_KEY] = {'id': str(row.pk), 'sent': False}


def clear_resume(request):
    request.session.pop(RESUME_SESSION_KEY, None)
    _clear_resume_code_state(request)


def _clear_resume_code_state(request):
    states = request.session.get(CODE_STATE_SESSION_KEY)
    if states and RESUME_STEP in states:
        request.session[CODE_STATE_SESSION_KEY] = {
            page: value for page, value in states.items() if page != RESUME_STEP}


def _resume_state(request):
    """(state, row) of the resume on offer, or (None, None) if there is none, or its row
    is gone or no longer active (the state is then cleared)."""
    state = request.session.get(RESUME_SESSION_KEY)
    if not isinstance(state, dict) or not state.get('id'):
        clear_resume(request)
        return None, None
    try:
        row = PendingRegistration.objects.active().filter(pk=state.get('id')).first()
    except ValidationError:
        row = None
    if row is None:
        clear_resume(request)
        return None, None
    return state, row


def _registered_error(form):
    """True if the form has an error that shows the recovery note."""
    return any(error.code in REGISTERED_CODES
               for errors in form.errors.as_data().values() for error in errors)


def _current_index(pending):
    """Index of the first step not yet saved."""
    if pending is None:
        return 0
    index, _step = get_step(pending.step)
    return index if index is not None else 0


@require_http_methods(['GET', 'HEAD', 'POST'])
@idle_check
def signup(request, slug=None):
    if slug is None:
        index, step = 0, STEPS[0]
    else:
        index, step = get_step(slug)
        if step is None:
            raise Http404('No such signup step.')
        if index == 0:
            return redirect('signup')

    pending = _pending_registration(request)
    current = _current_index(pending)
    if index > current:
        # Earlier steps are not saved yet.
        return redirect(step_url(current))

    if request.method != 'POST':
        form = step.form_class(initial=step.get_initial(pending), **step.get_form_kwargs(pending))
        return _render(request, index, step, form, pending)

    form = step.form_class(request.POST, **step.get_form_kwargs(pending))
    reservation = None
    if index == 0:
        # Before the attempt: a request blocked by an IP limit after its first logged
        # block writes nothing to the event log.
        blocked, reservation = _guard_step1(
            request, lambda: _attempt(request, pending, index), step, form, pending)
        if blocked:
            return _render(request, index, step, form, pending)
    saved = False
    try:
        response = _submit(request, index, step, form, pending)
        # A start is saved once the session holds its new pending registration.
        saved = bool(request.session.get(SESSION_KEY))
        return response
    finally:
        if reservation is not None and not saved:
            # Not started (invalid, the resume offer, an error): the count goes back.
            limits.ip_refund(request, limits.SIGNUP_STARTS, reservation)


def _submit(request, index, step, form, pending):
    """A step's POST past the step 1 guard: its action, else its form."""
    attempt = _attempt(request, pending, index)
    action = request.POST.get('action')
    if action in step.actions:
        getattr(step, step.actions[action])(request, pending)
        return redirect(step_url(index))

    valid = form.is_valid()
    _log_unavailable_checks(request, attempt, step, form)
    if not valid:
        _log_rejected(request, attempt, step, form)
        return _render(request, index, step, form, pending)

    if index == 0:
        # Another active signup with this email: the same offer as the live check
        # (`check_email`), shown under the email. What was typed here is not stored
        # (only logged, without the password).
        row = find_pending_match(form.cleaned_data.get('email'), exclude=pending)
        if row is not None:
            eventlog.record(attempt, 'pending_found', request=request, step=step.slug,
                            field='email', reason=EMAIL_PENDING, data=step.log_data(form))
            start_resume(request, row)
            return _render(request, index, step, form, pending, email_check='pending')
        clear_resume(request)

    pending = step.save(request, pending, form)
    eventlog.record(attempt, step.saved_event, request=request, step=step.slug,
                    data=step.log_data(form))
    # The next step not done yet (e.g. the email code is skipped once the email is
    # verified); with none left, the account is created.
    next_index = index + 1
    while next_index < len(STEPS) and STEPS[next_index].is_done(pending):
        next_index += 1
    pending.step = STEPS[min(next_index, len(STEPS) - 1)].slug
    if attempt is not None and pending.attempt_id is None:
        pending.attempt = attempt
    pending.save()
    request.session[SESSION_KEY] = str(pending.pk)
    if next_index == len(STEPS):
        return _create_account(request, index, step, form, pending, attempt)

    STEPS[next_index].enter(request, pending)
    return redirect(step_url(next_index))


def _refuse(form, code):
    """Give the (unvalidated) form only the generic error: no field is validated, so no
    external check runs and no field error tells anything."""
    form._errors = ErrorDict()
    form.cleaned_data = {}
    form.add_error(None, ValidationError(BLOCKED_MESSAGE, code=code))


def _guard_step1(request, get_attempt, step, form, pending):
    """The CAPTCHA and the starts limit, before step 1's form is validated. Returns
    (blocked, reservation). Blocked: the form (not validated, nothing saved) then holds
    only the generic error. The token is used up either way; the page shown next renders
    a new widget. When Turnstile cannot be reached the POST goes on (`check_unavailable`,
    field `captcha`). Reservation: a POST that would start a signup reserves one of the
    IP's starts (signup.limits.ip_reserve), which the caller refunds if nothing is
    saved."""
    result = captcha.verify_turnstile(request.POST.get(captcha.TOKEN_FIELD, ''),
                                      eventlog.client_ip(request))
    if result == captcha.FAILED:
        eventlog.record(get_attempt(), 'rejected', request=request, step=step.slug,
                        field='captcha', reason=BLOCKED_CAPTCHA, data=step.log_data(form))
        _refuse(form, 'captcha_failed')
        return True, None
    if result == captcha.UNAVAILABLE:
        eventlog.record(get_attempt(), 'check_unavailable', request=request, step=step.slug,
                        field='captcha', reason=captcha.TURNSTILE_CHECK,
                        data=step.log_data(form))
    # A POST that would create a new pending registration (not a resave of this one).
    if pending is None:
        reservation = limits.ip_reserve(request, limits.SIGNUP_STARTS)
        if reservation is None:
            log_ip_blocked(request, get_attempt, step.slug, limits.SIGNUP_STARTS)
            _refuse(form, 'rate_limited_ip')
            return True, None
        return False, reservation
    return False, None


class _AlreadyRegistered(Exception):
    """The email or the mobile was registered while this signup was in progress."""

    def __init__(self, field):
        super().__init__(field)
        self.field = field


# field -> (logged reason, message, error code)
_REGISTERED = {
    'email': (EMAIL_EXISTS, EMAIL_REGISTERED_MESSAGE, 'email_registered'),
    'phone': (PHONE_EXISTS, PHONE_REGISTERED_MESSAGE, 'phone_registered'),
}


def _create_account(request, index, step, form, pending, attempt):
    """Once no step is left: create the user with the stored hash, move the legal choices
    to it, drop the pending registration and every other one with its email or mobile,
    and log the user in, all in one transaction. The attempts of the other registrations
    end `abandoned`."""
    user_model = get_user_model()
    other_attempt_ids = []
    try:
        with transaction.atomic():
            pending = PendingRegistration.objects.select_for_update().get(pk=pending.pk)
            if email_is_registered(pending.email):
                raise _AlreadyRegistered('email')
            # No database constraint on the mobile: this check guards the race.
            if phone_is_registered(pending.phone):
                raise _AlreadyRegistered('phone')
            user = user_model(email=pending.email, password=pending.password, **{
                name: getattr(pending, name) for name in PendingRegistration.USER_FIELDS})
            user.save()
            # The legal choices become the user's (same versions and times), before
            # the pending registration (and its CASCADE) goes.
            LegalChoice.objects.filter(pending_registration=pending).update(
                pending_registration=None, user=user)
            pending.delete()
            # Any other signup (active or not) with this email or mobile.
            others = (PendingRegistration.objects.matching_email(pending.email)
                      | PendingRegistration.objects.matching_phone(pending.phone))
            other_attempt_ids = [pk for pk in others.values_list('attempt_id', flat=True)
                                 if pk is not None]
            others.delete()
            request.session.pop(SESSION_KEY, None)
            request.session.pop(ATTEMPT_SESSION_KEY, None)
            login(request, user)
    except PendingRegistration.DoesNotExist:
        # Already completed (e.g. the form was sent twice); start again.
        request.session.pop(SESSION_KEY, None)
        request.session.pop(ATTEMPT_SESSION_KEY, None)
        return redirect('signup')
    except (_AlreadyRegistered, IntegrityError) as error:
        # Someone registered this email (or mobile) while this signup was in progress;
        # an IntegrityError is the unique email index losing the same race.
        field = getattr(error, 'field', 'email')
        reason, message, code = _REGISTERED[field]
        eventlog.record(attempt, 'rejected', request=request, step=step.slug, field=field,
                        reason=reason, data={field: getattr(pending, field)})
        form.add_error(None, ValidationError(message, code=code))
        return _render(request, index, step, form, pending)
    # Logged after the commit: the attempt outlives the pending registration.
    eventlog.record(attempt, 'completed', request=request, step=step.slug)
    eventlog.end_attempt(attempt, 'completed')
    _abandon_attempts(other_attempt_ids, keep=attempt)
    return redirect('welcome')


def _abandon_attempts(attempt_ids, keep=None):
    """End the open attempts among `attempt_ids` (but `keep`) with outcome `abandoned`."""
    keep_id = getattr(keep, 'pk', keep)
    for attempt_id in set(attempt_ids):
        if keep_id is not None and attempt_id == keep_id:
            continue
        other = eventlog.get_attempt(attempt_id)
        if other is not None and other.ended_at is None:
            eventlog.end_attempt(other, 'abandoned')


def _is_last(index, pending):
    """True if no later step is left to do for `pending` (Continue creates the account)."""
    if pending is None or pending._state.adding:
        return False
    return all(later.is_done(pending) for later in STEPS[index + 1:])


def _code_help(request, page, form, contact, purpose, change_url=None, sms=False):
    """The context of signup/_code_help.html for a code page: the state of its last send
    (signup.steps.code_state), the one-time notice, whether the support contact shows
    (the hourly limit, a failed send, or a code no longer valid), and whether a code
    went to `contact` for `purpose` within its validity (`sent_recently`: only then
    does the page say a code was sent)."""
    state = code_state(request, page)
    since = limits.seconds_since_last_send(contact, purpose)
    expired = form.is_bound and any(
        error.code == 'code_expired' for error in form.errors.as_data().get('code', ()))
    notice = state.get('notice')
    failed = bool(state.get('failed'))
    return {
        'page': page,
        'channel': state.get('channel'),
        'fallback': bool(state.get('fallback')),
        'failed': failed,
        'notice': notice,
        'wait_seconds': state.get('wait') or 0,
        'wait_minutes': math.ceil((state.get('wait') or 0) / 60),
        'expired': expired,
        'show_support': notice == HOURLY or failed or expired,
        'support_email': settings.SIGNUP_SUPPORT_EMAIL,
        'sent_recently': since is not None and since < CODE_TTL_SECONDS,
        'change_url': change_url,
        'sms': sms and state.get('channel') != SMS,
        # The `blocked` notice (the per-IP limit on code sends): the generic error.
        'blocked_message': BLOCKED_MESSAGE,
    }


def _back_index(index, pending):
    """The step Back goes to: the previous one, skipping code steps already done (their
    code is used up). Never skips the account or the legal step."""
    back = index - 1
    while (back > 0 and pending is not None and isinstance(STEPS[back], CodeStep)
           and STEPS[back].is_done(pending)):
        back -= 1
    return back


def _render(request, index, step, form, pending, email_check=None):
    context = {
        'step': step,
        'form': form,
        'pending': pending,
        'step_number': index + 1,
        'step_count': len(STEPS),
        'back_url': step_url(_back_index(index, pending)) if index > 0 else None,
        'show_recovery': _registered_error(form),
        # The offer under the email (signup/_email_check.html): 'pending' or None.
        'email_check': email_check,
        # The submit button reads "Create account" only on the last step left to do.
        'is_last': _is_last(index, pending),
        **step.get_context(),
    }
    if isinstance(step, CodeStep):
        context['code'] = _code_help(request, step.slug, form, step.contact(pending),
                                     step.purpose, change_url=step_url(0),
                                     sms=isinstance(step, MobileCodeStep))
    return render(request, step.template_name, context)


@require_POST
def check_email(request):
    """The live check of step 1's email (htmx, when the field is left): the recovery note
    for a registered email, the resume offer for an email in another active signup
    (logged `pending_found`), nothing otherwise. Reveals nothing of the matched signup.
    Returns the partial signup/_email_check.html (empty after the idle timeout, and when
    the IP's live email checks limit is reached)."""
    if _idle_timed_out(request):
        return render(request, 'signup/_email_check.html', {'email_check': None})
    if not _count_email_check(request):
        # Nothing about the email, nor that a limit was hit.
        log_ip_blocked(request, lambda: _attempt(request, _pending_registration(request), 0),
                       STEPS[0].slug, limits.EMAIL_CHECKS)
        return render(request, 'signup/_email_check.html', {'email_check': None})
    try:
        email = normalize_email(forms.EmailField(max_length=254).clean(
            request.POST.get('email', '')))
    except ValidationError:
        email = None
    email_check = None
    if email and email_is_registered(email):
        email_check = 'registered'
        clear_resume(request)
    elif email:
        pending = _pending_registration(request)
        row = find_pending_match(email, exclude=pending)
        if row is not None:
            attempt = _attempt(request, pending, 0)
            eventlog.record(attempt, 'pending_found', request=request, step=STEPS[0].slug,
                            field='email', reason=EMAIL_PENDING, data={'email': email})
            start_resume(request, row)
            email_check = 'pending'
        else:
            clear_resume(request)
    return render(request, 'signup/_email_check.html', {'email_check': email_check})


def _count_email_check(request):
    """Count a live email check against the IP's limit. False if blocked (over the
    limit, or the lock not got within the wait). The check always runs: no refund."""
    return limits.ip_reserve(request, limits.EMAIL_CHECKS) is not None


@require_http_methods(['GET', 'HEAD', 'POST'])
@idle_check
def resume(request):
    """Offer, send and check the code (to the email) that resumes another signup in
    progress.

    Until the code is correct nothing of the matched registration is shown but its
    email, which is what the person typed. A correct code moves its data (and its legal
    choices) to a new pending registration of this signup (new id, this attempt, email
    verified); the matched row is deleted and its attempt ends `abandoned`. Step 1 then
    shows the saved data for review. Outside the step loop of `signup`.
    """
    if 'cancel' in request.GET:  # "Use different details"
        clear_resume(request)
        return redirect('signup')
    state, row = _resume_state(request)
    if state is None:
        return redirect('signup')
    contact = row.email
    attempt = eventlog.get_attempt(request.session.get(ATTEMPT_SESSION_KEY))
    if attempt is not None and attempt.ended_at is not None:
        attempt = None

    if request.method != 'POST':
        return _render_resume(request, state, contact, ResumeCodeForm(email=contact))

    action = request.POST.get('action')
    if action in ('send', 'resend'):
        since = limits.seconds_since_last_send(contact, PURPOSE_RESUME)
        if action == 'send' and since is not None and since < limits.COOLDOWN_SECONDS:
            # A code went to this email moments ago and is still valid: no new send.
            status = SENT
        else:
            status = _send_resume_code(request, attempt, contact,
                                       resend=action == 'resend').status
            if action == 'resend' and status == SENT:
                messages.success(request, _('We have sent you a new code.'))
        if status == SENT:  # sent now, or one sent within the cooldown is still valid
            request.session[RESUME_SESSION_KEY] = {**state, 'sent': True}
        return redirect('signup_resume')

    form = ResumeCodeForm(request.POST, email=contact)
    if not form.is_valid():
        for errors in form.errors.as_data().values():
            for error in errors:
                eventlog.record(attempt, 'rejected', request=request, step=RESUME_STEP,
                                field='code', reason=failure_reason(error.code))
        return _render_resume(request, state, contact, form)
    return _take_over(request, row.pk, attempt)


def _send_resume_code(request, attempt, email, resend=False):
    """Within the same limits, and with the same answer to a failed send, as the code
    steps (signup.steps.send_code). Returns its SendOutcome."""
    return send_code(request, attempt, RESUME_STEP, email, PURPOSE_RESUME, EMAIL,
                     resend=resend)


def _take_over(request, matched_pk, attempt):
    """Move the matched row's data into a new pending registration of this signup, with
    the email verified (the code went to it), and go to step 1 for review."""
    own = _pending_registration(request)
    try:
        with transaction.atomic():
            old = PendingRegistration.objects.active().select_for_update().get(pk=matched_pk)
            # Step 1 is next (review); the old row's later steps are not reachable.
            new = old.copy_to_new_row(attempt=attempt, step=STEPS[0].slug)
            # The legal choices too: a legal step done before is skipped on Continue.
            old.legal_choices.all().copy_to(pending_registration=new)
            old_attempt_id = old.attempt_id
            old.delete()
            if own is not None and own.pk != old.pk:
                own.delete()
    except PendingRegistration.DoesNotExist:
        # Resumed elsewhere, or expired, meanwhile: start again.
        clear_resume(request)
        return redirect('signup')
    clear_resume(request)
    request.session[SESSION_KEY] = str(new.pk)
    if attempt is not None:
        request.session[ATTEMPT_SESSION_KEY] = str(attempt.pk)
    # Logged after the commit: the attempts outlive the rows.
    if old_attempt_id is not None:
        _abandon_attempts([old_attempt_id], keep=attempt)
    eventlog.record(attempt, 'resumed', request=request, step=RESUME_STEP,
                    data={'attempt': str(old_attempt_id) if old_attempt_id else ''})
    # Step 1 with the saved data; on Continue the steps already done (the legal step,
    # the verified email) are skipped.
    messages.success(request, _('We have restored your registration. Review your details '
                                'and continue.'))
    return redirect(step_url(0))


def _render_resume(request, state, contact, form):
    # "Use different details" (cancel_url) is this page's way to change the email.
    return render(request, 'signup/resume.html', {
        'form': form,
        'contact': contact,
        'sent': state.get('sent', False),
        'cancel_url': reverse('signup_resume') + '?cancel',
        'cancel_registration_url': reverse('signup_cancel') + '?from=resume',
        'code': _code_help(request, RESUME_STEP, form, contact, PURPOSE_RESUME),
    })


def _cancel_back_url(request, pending):
    """Where "Keep my registration" goes: the page "Cancel registration" was chosen on
    (`from`: a step slug, or `resume` while a resume is on offer), else the current
    step."""
    origin = request.GET.get('from', '')
    current = _current_index(pending)
    if origin == RESUME_STEP:
        if _resume_state(request)[0] is not None:
            return reverse('signup_resume')
    elif origin:
        index, _step = get_step(origin)
        if index is not None and index <= current:
            return step_url(index)
    return step_url(current)


@require_http_methods(['GET', 'HEAD', 'POST'])
@idle_check
def cancel(request):
    """"Cancel registration" (CAP-8). GET asks for confirmation; POST removes the
    session's pending registration (its legal choices go with it) through
    signup.retention.remove_pending: its attempt gets `purged` (reason `cancelled`) and
    ends `cancelled`. An open attempt with no row (step 1 never saved) ends `cancelled`
    too. The signup session keys are cleared and step 1 starts empty. The event log
    keeps its values until the 30-day anonymization (eventlog.retention)."""
    pending = _pending_registration(request)
    if request.method != 'POST':
        return render(request, 'signup/cancel.html', {
            'back_url': _cancel_back_url(request, pending)})

    attempt = eventlog.get_attempt(request.session.get(ATTEMPT_SESSION_KEY))
    if pending is not None:
        remove_pending(pending, CANCELLED)
    # The session's attempt if remove_pending did not end it (no row, or another one's).
    attempt = eventlog.get_attempt(getattr(attempt, 'pk', None))
    if attempt is not None and attempt.ended_at is None:
        eventlog.end_attempt(attempt, CANCELLED)
    clear_signup_session(request)
    messages.success(request, _('Your registration was cancelled.'))
    return redirect('signup')
