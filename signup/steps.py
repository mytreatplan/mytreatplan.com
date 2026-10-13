"""The signup wizard's step registry.

`STEPS` is the ordered list of steps. The views in signup.views know nothing about any
particular step: later signup entries add a step, or extend one (more fields in step 1,
the legal step, the mobile code), by changing this module and its forms and templates.

A step saves its data into the PendingRegistration. After a step is saved, the wizard
skips the next steps that are already done (`is_done`, e.g. the email code once the email
is verified); when none is left it creates the account (signup.views._create_account).

Every step is logged in the event log (eventlog.service) by the views: its rejected
fields, and `saved_event` once saved, with the values `log_fields` names. A step lists
there only fields whose values may be logged: never a password or a verification code.
A form may also list external checks it could not make in `unavailable_checks`, as
(field, check) pairs; each is logged as `check_unavailable`.
"""
from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _

from eventlog import service as eventlog

from accounts.crypto import email_index

from legal.models import LegalChoice, LegalDocument

from .forms import AccountForm, EmailCodeForm, LegalForm, national_number
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

    def is_done(self, pending):
        """True if the wizard can skip this step for `pending` (nothing left to ask)."""
        return False

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
    """Step 1: personal, contact and login data. Creates (or updates) the pending
    registration."""

    slug = 'account'
    form_class = AccountForm
    template_name = 'signup/step_account.html'
    title = _('Create your account')
    # Stored on the pending registration (and later the user) as cleaned.
    stored_fields = ('title', 'first_name', 'middle_name', 'last_name', 'country', 'email',
                     'phone', 'how_found', 'how_found_other')
    # Every field but the passwords.
    log_fields = ('title', 'first_name', 'middle_name', 'last_name', 'country', 'email',
                  'phone_prefix', 'phone', 'how_found', 'how_found_other')

    def get_form_kwargs(self, pending):
        # With a saved hash (Back, or a resumed signup) the passwords may stay empty.
        return {'has_password': bool(pending and pending.password)}

    def get_initial(self, pending):
        if not pending:
            return {}
        initial = {name: getattr(pending, name) for name in self.stored_fields}
        initial['country'] = pending.country.code if pending.country else ''
        initial['phone_prefix'] = pending.phone_region
        initial['phone'] = national_number(pending.phone) if pending.phone else ''
        return initial

    def save(self, request, pending, form):
        pending = pending or PendingRegistration()
        if (pending.email_verified_at is not None
                and email_index(form.cleaned_data['email']) != pending.email_index):
            # Another email: it has to be verified again.
            pending.email_verified_at = None
        for name in self.stored_fields:
            setattr(pending, name, form.cleaned_data[name])
        pending.phone_region = form.cleaned_data['phone_prefix']
        # Only the hash (Argon2id, the first of PASSWORD_HASHERS) is kept between steps;
        # the user later gets this same hash. Empty passwords keep the saved hash.
        if form.cleaned_data.get('password1'):
            pending.password = make_password(form.cleaned_data['password1'])
        return pending


class LegalStep(Step):
    """Step 2: accept the Terms and the Conditions (required), the Ormco TPS contract and
    the commercial consent (optional), each in its current version (legal.models).

    Saving replaces the pending registration's choices: one LegalChoice per document,
    ticked or not, with the version shown and the time. They move to the user at account
    creation (signup.views._create_account) and are copied by a resume (_take_over).
    """

    slug = 'legal'
    form_class = LegalForm
    template_name = 'signup/step_legal.html'
    title = _('Terms and consent')

    def get_form_kwargs(self, pending):
        # Read on every request: a version published in the admin applies at once.
        return {'documents': LegalDocument.objects.current_by_kind()}

    def get_initial(self, pending):
        """Back refills the Terms and the Conditions accepted in their current version.
        The Ormco TPS contract and the commercial consent always start unticked."""
        if not pending:
            return {}
        current = LegalDocument.objects.current_by_kind()
        initial = {}
        for kind in LegalDocument.REQUIRED_KINDS:
            document = current.get(kind)
            if document and pending.legal_choices.filter(document=document,
                                                         accepted=True).exists():
                initial[kind] = True
        return initial

    def is_done(self, pending):
        """Done when the Terms and the Conditions are accepted in their current versions."""
        if pending._state.adding:
            return False  # step 1 of a new signup, not saved yet: no choices
        current = LegalDocument.objects.current_by_kind()
        documents = [current.get(kind) for kind in LegalDocument.REQUIRED_KINDS]
        if not all(documents):
            return False
        accepted = pending.legal_choices.filter(document__in=documents, accepted=True)
        return accepted.values('document').distinct().count() == len(documents)

    def save(self, request, pending, form):
        decided_at = timezone.now()
        pending.legal_choices.all().delete()
        LegalChoice.objects.bulk_create([
            LegalChoice(pending_registration=pending, document=form.documents[kind],
                        accepted=accepted, decided_at=decided_at)
            for kind, accepted in form.choices().items()])
        return pending

    def log_data(self, form):
        """Per kind: the version shown and whether it was ticked. No personal data."""
        cleaned = getattr(form, 'cleaned_data', {})
        data = {}
        for kind, document in form.documents.items():
            if kind in cleaned:
                accepted = bool(cleaned[kind])
            else:
                accepted = bool(form.is_bound and form[kind].value())
            data[kind] = {'version': document.version, 'accepted': accepted}
        return data


class EmailCodeStep(Step):
    """Step 3: the code sent to the email address."""

    slug = 'verify-email'
    form_class = EmailCodeForm
    template_name = 'signup/step_verify_email.html'
    title = _('Check your email')
    actions = {'resend': 'resend'}
    # The code itself is never logged: only that it was sent, accepted or rejected.
    saved_event = 'code_accepted'

    def get_form_kwargs(self, pending):
        return {'email': pending.email}

    def is_done(self, pending):
        return pending.email_verified_at is not None

    def save(self, request, pending, form):
        pending.email_verified_at = timezone.now()
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


STEPS = [AccountStep(), LegalStep(), EmailCodeStep()]


def get_step(slug):
    """(index, step) for a slug, or (None, None) if there is no such step."""
    for index, step in enumerate(STEPS):
        if step.slug == slug:
            return index, step
    return None, None
