"""The signup wizard's step registry.

`STEPS` is the ordered list of steps. The views in signup.views know nothing about any
particular step: later signup entries add a step, or extend one (more fields in step 1,
the legal step, the mobile code), by changing this module and its forms and templates.

A step saves its data into the PendingRegistration. After a step is saved, the wizard
skips the next steps that are already done (`is_done`, e.g. the email code once the email
is verified); when none is left it creates the account (signup.views._create_account).
The code steps (`CodeStep`: the email, then the mobile) share the sending, our limits
(signup.limits) and the answer to a failed send (`send_code`, also used by the resume
page).

Every step is logged in the event log (eventlog.service) by the views: its rejected
fields, and `saved_event` once saved, with the values `log_fields` names. A step lists
there only fields whose values may be logged: never a password or a verification code.
A form may also list external checks it could not make in `unavailable_checks`, as
(field, check) pairs; each is logged as `check_unavailable`.
"""
import logging
from collections import namedtuple

from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _

from eventlog import service as eventlog

from accounts.crypto import email_index
from accounts.models import phone_index_or_blank

from legal.models import LegalChoice, LegalDocument

from . import limits
from .forms import (OTP_RATE_LIMITED, OTP_SEND_FAILED, AccountForm, EmailCodeForm, LegalForm,
                    MobileCodeForm, national_number)
from .models import PendingRegistration
from .verification import (EMAIL, PURPOSE_SIGNUP, SMS, WHATSAPP, ChannelUnavailable,
                           VerificationError, contact_kind, get_provider)

logger = logging.getLogger(__name__)


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
        if (pending.phone_verified_at is not None
                and phone_index_or_blank(form.cleaned_data['phone']) != pending.phone_index):
            # Another mobile: the same.
            pending.phone_verified_at = None
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


# The state of each code page (a code step's slug, or the resume page's
# signup.views.RESUME_STEP) in the session:
# {'channel': the channel of the last code sent, 'fallback': whether it went by SMS
#  because WhatsApp could not deliver, 'failed': whether the last send failed,
#  'notice': a one-time notice ('cooldown' or 'hourly', with 'wait' in seconds)}.
# Never a contact or a code.
CODE_STATE_SESSION_KEY = 'signup_code_state'

SendOutcome = namedtuple('SendOutcome', 'status channel fallback wait_seconds')
SENT, COOLDOWN, HOURLY, FAILED = 'sent', 'cooldown', 'hourly', 'failed'


def code_state(request, page):
    """This code page's state (a copy); the one-time notice is removed from the session."""
    states = request.session.get(CODE_STATE_SESSION_KEY) or {}
    state = dict(states.get(page) or {})
    if 'notice' in state:
        states[page] = {k: v for k, v in state.items() if k not in ('notice', 'wait')}
        request.session[CODE_STATE_SESSION_KEY] = states
    return state


def _store_outcome(request, page, outcome):
    states = request.session.get(CODE_STATE_SESSION_KEY) or {}
    state = {k: v for k, v in (states.get(page) or {}).items() if k not in ('notice', 'wait')}
    if outcome.status == SENT:
        state.update(channel=outcome.channel, fallback=outcome.fallback, failed=False)
    elif outcome.status == FAILED:
        # The channel tried, so a retry uses it (and the SMS switch shows after WhatsApp).
        state.update(channel=outcome.channel, fallback=False, failed=True)
    else:
        state.update(notice=outcome.status, wait=outcome.wait_seconds)
    states[page] = state
    request.session[CODE_STATE_SESSION_KEY] = states


def send_code(request, attempt, page, contact, purpose, channel, resend=False):
    """Send a code to `contact` within our limits (signup.limits) and log it.

    A mobile goes by `channel` (WhatsApp, or SMS when asked); if WhatsApp cannot deliver
    to the number the code goes by SMS at once (`fallback`). Returns a SendOutcome:
    SENT; COOLDOWN or HOURLY (not sent; the hourly limit is logged `rejected`); or
    FAILED (the provider failed: logged `code_send_failed` and reported to Sentry, with
    no personal data). The page's session state is updated (`code_state`). A short lock
    per contact and purpose makes parallel sends (a double click) answer COOLDOWN.
    """
    kind = contact_kind(channel)
    field = 'email' if kind == EMAIL else 'phone'
    log_data = {field: contact, 'channel': channel, 'purpose': purpose}
    if not limits.acquire_send_lock(contact, purpose):
        # Another request is sending to this contact right now (e.g. a double click).
        outcome = SendOutcome(COOLDOWN, channel, False, limits.COOLDOWN_SECONDS)
        _store_outcome(request, page, outcome)
        return outcome
    try:
        return _send_code_locked(request, attempt, page, contact, purpose, channel, resend,
                                 field, log_data)
    finally:
        limits.release_send_lock(contact, purpose)


def _send_code_locked(request, attempt, page, contact, purpose, channel, resend, field,
                      log_data):
    check = limits.check_send(contact, purpose)
    if not check.allowed:
        status = HOURLY if check.hourly_blocked else COOLDOWN
        if status == HOURLY:
            eventlog.record(attempt, 'rejected', request=request, step=page, field=field,
                            reason=OTP_RATE_LIMITED, data=log_data)
        outcome = SendOutcome(status, channel, False, check.wait_seconds)
        _store_outcome(request, page, outcome)
        return outcome

    provider = get_provider()
    language = translation.get_language()
    fallback = False
    try:
        try:
            provider.send(channel, contact, purpose, language)
        except ChannelUnavailable:
            if channel != WHATSAPP:
                raise
            channel, fallback = SMS, True
            provider.send(channel, contact, purpose, language)
    except VerificationError as error:
        # No contact here: Sentry alerts on spikes of this message.
        logger.error('Signup code send failed: channel=%s purpose=%s error=%s',
                     channel, purpose, type(error).__name__)
        eventlog.record(attempt, 'code_send_failed', request=request, step=page,
                        field=field, reason=OTP_SEND_FAILED,
                        data={**log_data, 'channel': channel})
        outcome = SendOutcome(FAILED, channel, fallback, 0)
        _store_outcome(request, page, outcome)
        return outcome

    limits.record_send(contact, purpose)
    data = {**log_data, 'channel': channel, 'fallback': fallback}
    if resend:
        data['resend'] = True
    eventlog.record(attempt, 'code_sent', request=request, step=page, data=data)
    outcome = SendOutcome(SENT, channel, fallback, 0)
    _store_outcome(request, page, outcome)
    return outcome


class CodeStep(Step):
    """A step that sends a code to one of the pending registration's contacts and checks
    it. Entering the step sends a code, unless one went to that contact within the
    cooldown (that code is still valid). `resend` sends a new one within the limits."""

    # The pending registration's field holding the contact, and the first channel used.
    contact_field = None
    channel = EMAIL
    purpose = PURPOSE_SIGNUP
    actions = {'resend': 'resend'}
    # The code itself is never logged: only that it was sent, accepted or rejected.
    saved_event = 'code_accepted'

    def contact(self, pending):
        return getattr(pending, self.contact_field)

    def enter(self, request, pending):
        since = limits.seconds_since_last_send(self.contact(pending), self.purpose)
        if since is not None and since < limits.COOLDOWN_SECONDS:
            return
        self._send(request, pending, self.channel)

    def resend(self, request, pending):
        if self._send(request, pending, self.channel, resend=True).status == SENT:
            messages.success(request, _('We have sent you a new code.'))

    def _send(self, request, pending, channel, resend=False):
        return send_code(request, pending.attempt_id, self.slug, self.contact(pending),
                         self.purpose, channel, resend=resend)


class EmailCodeStep(CodeStep):
    """The code sent to the email address."""

    slug = 'verify-email'
    form_class = EmailCodeForm
    template_name = 'signup/step_verify_email.html'
    title = _('Check your email')
    contact_field = 'email'
    channel = EMAIL

    def get_form_kwargs(self, pending):
        return {'email': pending.email}

    def is_done(self, pending):
        return pending.email_verified_at is not None

    def save(self, request, pending, form):
        pending.email_verified_at = timezone.now()
        return pending


class MobileCodeStep(CodeStep):
    """The code sent to the mobile: by WhatsApp, else by SMS (and by SMS when asked)."""

    slug = 'verify-mobile'
    form_class = MobileCodeForm
    template_name = 'signup/step_verify_mobile.html'
    title = _('Check your mobile')
    contact_field = 'phone'
    channel = WHATSAPP
    actions = {'resend': 'resend', 'sms': 'send_sms'}

    def get_form_kwargs(self, pending):
        return {'phone': pending.phone}

    def is_done(self, pending):
        return pending.phone_verified_at is not None

    def save(self, request, pending, form):
        pending.phone_verified_at = timezone.now()
        return pending

    def resend(self, request, pending):
        """A new code over the channel used last (WhatsApp unless SMS was used)."""
        state = code_state(request, self.slug)
        channel = SMS if state.get('channel') == SMS else WHATSAPP
        if self._send(request, pending, channel, resend=True).status == SENT:
            messages.success(request, _('We have sent you a new code.'))

    def send_sms(self, request, pending):
        """"Send by SMS instead": a resend by SMS."""
        if self._send(request, pending, SMS, resend=True).status == SENT:
            messages.success(request, _('We have sent you a new code by SMS.'))


STEPS = [AccountStep(), LegalStep(), EmailCodeStep(), MobileCodeStep()]


def get_step(slug):
    """(index, step) for a slug, or (None, None) if there is no such step."""
    for index, step in enumerate(STEPS):
        if step.slug == slug:
            return index, step
    return None, None
