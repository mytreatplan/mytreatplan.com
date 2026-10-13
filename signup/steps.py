"""The signup wizard's step registry.

`STEPS` is the ordered list of steps. The views in signup.views know nothing about any
particular step: later signup entries add a step, or extend one (more fields in step 1,
the legal step, the mobile code), by changing this module and its forms and templates.

A step saves its data into the PendingRegistration. After the last step is saved, the
wizard creates the account (signup.views.create_account).
"""
from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from .forms import AccountForm, EmailCodeForm
from .models import PendingRegistration
from .verification import EMAIL, PURPOSE_SIGNUP, get_provider


class Step:
    """One page of the wizard: a slug (its URL), a form, a template and its handlers."""

    slug = None
    form_class = None
    template_name = None
    title = None
    # Extra buttons the step handles besides its form (name -> method name), e.g. resend.
    actions = {}

    def get_form_kwargs(self, pending):
        """Keyword arguments for the form (other than data)."""
        return {}

    def get_initial(self, pending):
        return {}

    def save(self, request, pending, form):
        """Store the valid form's data. Returns the PendingRegistration (created by step 1)."""
        raise NotImplementedError

    def enter(self, request, pending):
        """Called once each time the wizard moves into this step (e.g. to send a code)."""


class AccountStep(Step):
    """Step 1: email and password. Creates (or updates) the pending registration."""

    slug = 'account'
    form_class = AccountForm
    template_name = 'signup/step_account.html'
    title = _('Create your account')

    def get_initial(self, pending):
        return {'email': pending.email} if pending else {}

    def save(self, request, pending, form):
        pending = pending or PendingRegistration()
        pending.email = form.cleaned_data['email']
        # Only the hash (Argon2id, the first of PASSWORD_HASHERS) is kept between steps;
        # the user later gets this same hash.
        pending.password = make_password(form.cleaned_data['password1'])
        return pending


class EmailCodeStep(Step):
    """Step 2: the code sent to the email address."""

    slug = 'verify-email'
    form_class = EmailCodeForm
    template_name = 'signup/step_verify_email.html'
    title = _('Check your email')
    actions = {'resend': 'resend'}

    def get_form_kwargs(self, pending):
        return {'email': pending.email}

    def save(self, request, pending, form):
        return pending

    def enter(self, request, pending):
        self._send(pending)

    def resend(self, request, pending):
        """Send a new code (no limits until signup entry 7)."""
        self._send(pending)
        messages.success(request, _('We have sent you a new code.'))

    @staticmethod
    def _send(pending):
        get_provider().send(EMAIL, pending.email, PURPOSE_SIGNUP, translation.get_language())


STEPS = [AccountStep(), EmailCodeStep()]


def get_step(slug):
    """(index, step) for a slug, or (None, None) if there is no such step."""
    for index, step in enumerate(STEPS):
        if step.slug == slug:
            return index, step
    return None, None
