"""The signup wizard. Generic over signup.steps.STEPS: no step is named here.

Plus the resume flow (signup entry 5): when step 1's email belongs to another active
pending registration (checked as soon as the email field is left, `check_email`, or on
Continue without JavaScript), the person can take it over with a code sent to that email.
The code also verifies the email; the saved data comes back into step 1 for review.
"""
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from eventlog import service as eventlog
from eventlog.models import Attempt
from legal.models import LegalChoice

from .forms import (EMAIL_EXISTS, EMAIL_PENDING, EMAIL_REGISTERED_MESSAGE, PHONE_EXISTS,
                    PHONE_REGISTERED_MESSAGE, REGISTERED_CODES, ResumeCodeForm,
                    email_is_registered, failure_reason, normalize_email, phone_is_registered)
from .models import PendingRegistration
from .steps import STEPS, get_step
from .verification import EMAIL, PURPOSE_RESUME, get_provider

# The session holds only the pending registration's id, and the event log attempt's id.
SESSION_KEY = 'signup_pending_id'
ATTEMPT_SESSION_KEY = 'signup_attempt_id'
# While a resume is offered: {'id': the matched row's id, 'sent': whether a code was
# sent}. Never a contact: the page reads it from the row.
RESUME_SESSION_KEY = 'signup_resume'

# The resume page's step name in the event log.
RESUME_STEP = 'resume'


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
        return PendingRegistration.objects.get(pk=pk)
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
    request.session[RESUME_SESSION_KEY] = {'id': str(row.pk), 'sent': False}


def clear_resume(request):
    request.session.pop(RESUME_SESSION_KEY, None)


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

    attempt = _attempt(request, pending, index)
    action = request.POST.get('action')
    if action in step.actions:
        getattr(step, step.actions[action])(request, pending)
        return redirect(step_url(index))

    form = step.form_class(request.POST, **step.get_form_kwargs(pending))
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


def _render(request, index, step, form, pending, email_check=None):
    return render(request, step.template_name, {
        'step': step,
        'form': form,
        'pending': pending,
        'step_number': index + 1,
        'step_count': len(STEPS),
        'back_url': step_url(index - 1) if index > 0 else None,
        'show_recovery': _registered_error(form),
        # The offer under the email (signup/_email_check.html): 'pending' or None.
        'email_check': email_check,
    })


@require_POST
def check_email(request):
    """The live check of step 1's email (htmx, when the field is left): the recovery note
    for a registered email, the resume offer for an email in another active signup
    (logged `pending_found`), nothing otherwise. Reveals nothing of the matched signup.
    Returns the partial signup/_email_check.html."""
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


@require_http_methods(['GET', 'HEAD', 'POST'])
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
        _send_resume_code(request, attempt, contact, resend=action == 'resend')
        request.session[RESUME_SESSION_KEY] = {**state, 'sent': True}
        if action == 'resend':
            messages.success(request, _('We have sent you a new code.'))
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
    get_provider().send(EMAIL, email, PURPOSE_RESUME, translation.get_language())
    data = {'email': email, 'channel': EMAIL, 'purpose': PURPOSE_RESUME}
    if resend:
        data['resend'] = True
    eventlog.record(attempt, 'code_sent', request=request, step=RESUME_STEP, data=data)


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
    return render(request, 'signup/resume.html', {
        'form': form,
        'contact': contact,
        'sent': state.get('sent', False),
        'cancel_url': reverse('signup_resume') + '?cancel',
    })
