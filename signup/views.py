"""The signup wizard. Generic over signup.steps.STEPS: no step is named here."""
from django.contrib.auth import get_user_model, login
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from eventlog import service as eventlog
from eventlog.models import Attempt

from .forms import EMAIL_EXISTS, EMAIL_REGISTERED_MESSAGE, email_is_registered, failure_reason
from .models import PendingRegistration
from .steps import STEPS, get_step

# The session holds only the pending registration's id, and the event log attempt's id.
SESSION_KEY = 'signup_pending_id'
ATTEMPT_SESSION_KEY = 'signup_attempt_id'


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
    if not form.is_valid():
        _log_rejected(request, attempt, step, form)
        return _render(request, index, step, form, pending)

    pending = step.save(request, pending, form)
    eventlog.record(attempt, step.saved_event, request=request, step=step.slug,
                    data=step.log_data(form))
    if index + 1 == len(STEPS):
        return _create_account(request, index, step, form, pending, attempt)

    next_step = STEPS[index + 1]
    pending.step = next_step.slug
    if attempt is not None and pending.attempt_id is None:
        pending.attempt = attempt
    pending.save()
    request.session[SESSION_KEY] = str(pending.pk)
    next_step.enter(request, pending)
    return redirect(step_url(index + 1))


def _create_account(request, index, step, form, pending, attempt):
    """After the last step: create the user with the stored hash, drop the pending
    registration and log the user in, all in one transaction."""
    user_model = get_user_model()
    try:
        with transaction.atomic():
            pending = PendingRegistration.objects.select_for_update().get(pk=pending.pk)
            if email_is_registered(pending.email):
                raise IntegrityError('email already registered')
            user = user_model(email=pending.email, password=pending.password)
            user.save()
            pending.delete()
            request.session.pop(SESSION_KEY, None)
            request.session.pop(ATTEMPT_SESSION_KEY, None)
            login(request, user)
    except PendingRegistration.DoesNotExist:
        # Already completed (e.g. the form was sent twice); start again.
        request.session.pop(SESSION_KEY, None)
        request.session.pop(ATTEMPT_SESSION_KEY, None)
        return redirect('signup')
    except IntegrityError:
        # Someone registered this email while this signup was in progress.
        eventlog.record(attempt, 'rejected', request=request, step=step.slug, field='email',
                        reason=EMAIL_EXISTS, data={'email': pending.email})
        form.add_error(None, EMAIL_REGISTERED_MESSAGE)
        return _render(request, index, step, form, pending)
    # Logged after the commit: the attempt outlives the pending registration.
    eventlog.record(attempt, 'completed', request=request, step=step.slug)
    eventlog.end_attempt(attempt, 'completed')
    return redirect('welcome')


def _render(request, index, step, form, pending):
    return render(request, step.template_name, {
        'step': step,
        'form': form,
        'pending': pending,
        'step_number': index + 1,
        'step_count': len(STEPS),
        'back_url': step_url(index - 1) if index > 0 else None,
    })
