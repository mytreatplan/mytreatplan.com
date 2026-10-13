"""The signup wizard's step registry.

`STEPS` is the ordered list of steps. The views in signup.views know nothing about any
particular step: later signup entries add a step, or extend one (more fields in step 1,
the legal step, the mobile code), by changing this module and its forms and templates.

A step saves its data into the PendingRegistration. After the last step is saved, the
wizard creates the account (signup.views.create_account).

Every step is logged in the event log (eventlog.service) by the views: its rejected
fields, and `saved_event` once saved, with the values `log_fields` names. A step lists
there only fields whose values may be logged: never a password or a verification code.
"""
from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from eventlog import service as eventlog

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
    # Event logged once the step's form is valid and saved.
    saved_event = 'step_saved'
    # Fields whose submitted values the event log keeps (encrypted). Never passwords or codes.
    log_fields = ()

    def get_form_kwargs(self, pending):
        """Keyword arguments for the form (other than data)."""
        return {}

    def get_initial(self, pending):
        return {}

    def save(self, request, pending, form):
        """Store the valid form's data. Returns the PendingRegistration (created by step 1)."""
        raise NotImplementedError

    def log_data(self, form):
        """The loggable values of the form (`log_fields`): the cleaned value when there is
        one, else the value as submitted (e.g. an email refused as already registered)."""
        cleaned = getattr(form, 'cleaned_data', {})
        data = {}
        for name in self.log_fields:
            if name in cleaned:
                data[name] = cleaned[name]
            elif form.is_bound:
                data[name] = form[name].value()
        return data

    def enter(self, request, pending):
        """Called once each time the wizard moves into this step (e.g. to send a code)."""


class AccountStep(Step):
    """Step 1: email and password. Creates (or updates) the pending registration."""

    slug = 'account'
    form_class = AccountForm
    template_name = 'signup/step_account.html'
    title = _('Create your account')
    log_fields = ('email',)  # never the passwords

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
    # The code itself is never logged: only that it was sent, accepted or rejected.
    saved_event = 'code_accepted'

    def get_form_kwargs(self, pending):
        return {'email': pending.email}

    def save(self, request, pending, form):
        return pending

    def enter(self, request, pending):
        self._send(request, pending)

    def resend(self, request, pending):
        """Send a new code (no limits until signup entry 7)."""
        self._send(request, pending, resend=True)
        messages.success(request, _('We have sent you a new code.'))

    def _send(self, request, pending, resend=False):
        get_provider().send(EMAIL, pending.email, PURPOSE_SIGNUP, translation.get_language())
        data = {'email': pending.email, 'channel': EMAIL}
        if resend:
            data['resend'] = True
        eventlog.record(pending.attempt_id, 'code_sent', request=request, step=self.slug,
                        data=data)


STEPS = [AccountStep(), EmailCodeStep()]


def get_step(slug):
    """(index, step) for a slug, or (None, None) if there is no such step."""
    for index, step in enumerate(STEPS):
        if step.slug == slug:
            return index, step
    return None, None
