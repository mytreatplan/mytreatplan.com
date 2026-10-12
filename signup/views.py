"""The signup wizard. Generic over signup.steps.STEPS: no step is named here."""
from django.contrib.auth import get_user_model, login
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .forms import EMAIL_REGISTERED_MESSAGE, email_is_registered
from .models import PendingRegistration
from .steps import STEPS, get_step

# The session holds only the pending registration's id.
SESSION_KEY = 'signup_pending_id'


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
        return None


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

    action = request.POST.get('action')
    if action in step.actions:
        getattr(step, step.actions[action])(request, pending)
        return redirect(step_url(index))

    form = step.form_class(request.POST, **step.get_form_kwargs(pending))
    if not form.is_valid():
        return _render(request, index, step, form, pending)

    pending = step.save(request, pending, form)
    if index + 1 == len(STEPS):
        return _create_account(request, index, step, form, pending)

    next_step = STEPS[index + 1]
    pending.step = next_step.slug
    pending.save()
    request.session[SESSION_KEY] = str(pending.pk)
    next_step.enter(request, pending)
    return redirect(step_url(index + 1))


def _create_account(request, index, step, form, pending):
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
            login(request, user)
    except PendingRegistration.DoesNotExist:
        # Already completed (e.g. the form was sent twice); start again.
        request.session.pop(SESSION_KEY, None)
        return redirect('signup')
    except IntegrityError:
        # Someone registered this email while this signup was in progress.
        form.add_error(None, EMAIL_REGISTERED_MESSAGE)
        return _render(request, index, step, form, pending)
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
