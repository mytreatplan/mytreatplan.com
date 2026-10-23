import logging

import phonenumbers
from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.forms.utils import ErrorDict
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django_countries import countries

from accounts.models import HOW_FOUND_OTHER_MAX_LENGTH, HowFound, Title
from accounts.password_validation import BREACHED_PASSWORD_CHECK, collect_unavailable_checks
from legal.models import LegalDocument
from legal.rendering import render_markdown

from .disposable import DISPOSABLE_EMAIL_CHECK, email_domain, is_disposable_domain
from .verification import (APPROVED, CODE_LENGTH, EMAIL, NO_CODE, PURPOSE_RESUME, PURPOSE_SIGNUP,
                           SMS, CheckFailed, get_provider)

# The same logger as signup.steps (reported to Sentry): a code check failure is an error.
logger = logging.getLogger('signup.steps')


# Text-field classes (design tokens: field-border, radius-14, shadow-focus-field).
FIELD_CLASSES = (
    'min-h-[50px] w-full rounded-14 border border-field-border bg-surface px-4 text-[15px] '
    'leading-[1.3] text-ink outline-none transition-shadow hover:border-field-border-hover '
    'focus:border-green focus:shadow-focus-field aria-invalid:border-notice-border'
)


class StyledForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', FIELD_CLASSES)


def normalize_email(email):
    return get_user_model().objects.normalize_email(email)


# Shown when the email or the mobile belongs to an existing user (step 1, and at account
# creation), with the recovery note (signup/_recovery_note.html).
EMAIL_REGISTERED_MESSAGE = _('This email address is already registered.')
PHONE_REGISTERED_MESSAGE = _('This mobile phone number is already registered.')
# Error codes that show the recovery note.
REGISTERED_CODES = ('email_registered', 'phone_registered')
# Shown when the CAPTCHA fails or a per-IP limit blocks a request (signup entry 9): it
# says nothing of which check, limit or reset time. A non-field error on step 1; the
# `blocked` notice on the code pages (signup/_code_help.html).
BLOCKED_MESSAGE = _("We couldn't process your request. Please try again later.")


def refuse(form, code, message=BLOCKED_MESSAGE):
    """Give the (unvalidated) form only `message`, the generic error by default, as a
    non-field error with `code`: no field is validated, so no external check runs and no
    field error tells anything. Signup's step 1, the login and the password reset."""
    form._errors = ErrorDict()
    form.cleaned_data = {}
    form.add_error(None, forms.ValidationError(message, code=code))


# Failure reasons for the event log, in the wording of registration-failure-policies.md.
# Any other validation failure is logged with its form error code.
FAILURE_REASONS = {
    'email_registered': 'email exists',
    'phone_registered': 'phone exists',
    'email_pending': 'email pending',
    'email_disposable': 'email disposable',
    'invalid_code': 'OTP verification failed',
    'code_expired': 'OTP verification failed',
    'rate_limited': 'OTP rate limited',
    'send_failed': 'OTP send failed',
    # A resend or the switch to SMS asked for ("Code not received", QA A7).
    'not_delivered': 'OTP not delivered',
    'check_failed': 'OTP check failed',
    'captcha_failed': 'blocked: captcha',
    'rate_limited_ip': 'blocked: rate limit',
}
EMAIL_EXISTS = FAILURE_REASONS['email_registered']
PHONE_EXISTS = FAILURE_REASONS['phone_registered']
EMAIL_PENDING = FAILURE_REASONS['email_pending']
OTP_RATE_LIMITED = FAILURE_REASONS['rate_limited']
OTP_SEND_FAILED = FAILURE_REASONS['send_failed']
OTP_NOT_DELIVERED = FAILURE_REASONS['not_delivered']
BLOCKED_CAPTCHA = FAILURE_REASONS['captcha_failed']
BLOCKED_RATE_LIMIT = FAILURE_REASONS['rate_limited_ip']


def failure_reason(code):
    """The logged reason for a form error code."""
    return FAILURE_REASONS.get(code, code or 'invalid')


def email_is_registered(email):
    """Whatever its case; through the blind index, since the email is encrypted."""
    return get_user_model().objects.by_email(email).exists()


def phone_is_registered(phone):
    """A mobile (E.164) of an existing user; through the blind index, since it is
    encrypted."""
    return bool(phone) and get_user_model().objects.by_phone(phone).exists()


# The empty first option of the step 1 selects (so nothing is chosen by default).
BLANK_CHOICE = [('', '---------')]

# phonenumbers types accepted as a mobile number. Some plans (e.g. +1) cannot tell
# mobile from fixed lines, so FIXED_LINE_OR_MOBILE is accepted too.
MOBILE_TYPES = {phonenumbers.PhoneNumberType.MOBILE,
                phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE}


def country_choices():
    """Countries by name in the current language (django-countries)."""
    return BLANK_CHOICE + list(countries)


def phone_prefix_choices():
    """One option per country with a calling code: the region code, shown as
    "Spain (+34)", by name in the current language."""
    choices = []
    for code, name in countries:
        calling_code = phonenumbers.country_code_for_region(code)
        if calling_code:
            choices.append((code, f'{name} (+{calling_code})'))
    return BLANK_CHOICE + choices


def parse_mobile(number, region):
    """The mobile number in E.164 form, or None if it is not a valid mobile for the
    prefix of `region`."""
    if any(char.isalpha() for char in number):
        # Letters ("ext 5", a stray "x", vanity letters) would be dropped or converted.
        return None
    try:
        parsed = phonenumbers.parse(number, region)
    except phonenumbers.NumberParseException:
        return None
    if (parsed.extension  # E.164 would silently drop it
            or parsed.country_code != phonenumbers.country_code_for_region(region)
            or not phonenumbers.is_valid_number(parsed)
            or phonenumbers.number_type(parsed) not in MOBILE_TYPES):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def national_number(e164):
    """An E.164 number as the person would type it after the prefix (to refill step 1)."""
    try:
        parsed = phonenumbers.parse(e164, None)
    except phonenumbers.NumberParseException:
        return e164
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)


class AccountForm(StyledForm):
    """Step 1: personal, contact and login data (signup-fields.md; the CAPTCHA is entry 9).

    With `has_password` (the pending registration has a saved hash: Back within the same
    signup; a resumed signup has none) the passwords may stay empty, which keeps that
    hash; once either is filled in every password rule applies.

    External checks (breached password, disposable domain) that could not be made are
    listed in `unavailable_checks` as (field, check) pairs; the view logs them.
    """

    error_messages = {
        'email_registered': EMAIL_REGISTERED_MESSAGE,
        'phone_registered': PHONE_REGISTERED_MESSAGE,
        'email_disposable': _('Please use your professional email address: disposable email '
                              'addresses are not accepted.'),
        'password_mismatch': _('The two passwords do not match.'),
        'phone_invalid': _('Enter a valid mobile phone number for the selected prefix.'),
        'how_found_other_required': _('Please tell us how you found out about us.'),
    }

    # First, and checked as soon as it is left (htmx, signup.views.check_email): an
    # email in another signup in progress offers to resume it. Only the email and the
    # CSRF token are sent.
    email = forms.EmailField(
        label=_('Email'),
        max_length=254,
        widget=forms.EmailInput(attrs={
            'autocomplete': 'email',
            'autofocus': True,
            'hx-post': reverse_lazy('signup_check_email'),
            'hx-trigger': 'change',
            'hx-target': '#email-check',
            'hx-swap': 'innerHTML',
            'hx-params': 'email,csrfmiddlewaretoken',
        }),
    )
    title = forms.ChoiceField(
        label=_('Title / Treatment'),
        choices=BLANK_CHOICE + Title.choices,
        widget=forms.Select(attrs={'autocomplete': 'honorific-prefix'}),
    )
    first_name = forms.CharField(
        label=_('Name'),
        max_length=150,
        widget=forms.TextInput(attrs={'autocomplete': 'given-name'}),
    )
    middle_name = forms.CharField(
        label=_('Middle name or initial (optional)'),
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={'autocomplete': 'additional-name'}),
    )
    last_name = forms.CharField(
        label=_('Surname(s)'),
        max_length=150,
        widget=forms.TextInput(attrs={'autocomplete': 'family-name'}),
    )
    country = forms.ChoiceField(
        label=_('Country / Region'),
        choices=country_choices,
        widget=forms.Select(attrs={'autocomplete': 'country'}),
    )
    password1 = forms.CharField(
        label=_('Password'),
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text=_('At least 12 characters.'),
    )
    password2 = forms.CharField(
        label=_('Repeat password'),
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    phone_prefix = forms.ChoiceField(
        label=_('International prefix'),
        choices=phone_prefix_choices,
    )
    phone = forms.CharField(
        label=_('Mobile phone number'),
        max_length=32,
        widget=forms.TextInput(attrs={'type': 'tel', 'autocomplete': 'tel-national',
                                      'inputmode': 'tel'}),
    )
    how_found = forms.ChoiceField(
        label=_('How did you find out about us?'),
        choices=BLANK_CHOICE + HowFound.choices,
    )
    how_found_other = forms.CharField(
        label=_('Others: please specify'),
        max_length=HOW_FOUND_OTHER_MAX_LENGTH,
        required=False,
        help_text=_('Only if you chose "Others".'),
    )

    def __init__(self, *args, has_password=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.unavailable_checks = []
        self.has_password = has_password
        if has_password:
            for name in ('password1', 'password2'):
                self.fields[name].required = False
            self.fields['password1'].help_text = _(
                'Leave both password fields empty to keep the password you chose before. '
                'A new one needs at least 12 characters.')

    def clean_email(self):
        email = normalize_email(self.cleaned_data['email'])
        if email_is_registered(email):
            raise forms.ValidationError(self.error_messages['email_registered'],
                                        code='email_registered')
        # Only the domain goes to the disposable-email check.
        disposable = is_disposable_domain(email_domain(email))
        if disposable is None:
            self.unavailable_checks.append(('email', DISPOSABLE_EMAIL_CHECK))
        elif disposable:
            raise forms.ValidationError(self.error_messages['email_disposable'],
                                        code='email_disposable')
        return email

    def clean_phone(self):
        """The mobile in E.164 form, valid for the chosen prefix and not registered."""
        phone = self.cleaned_data['phone']
        region = self.cleaned_data.get('phone_prefix')
        if not region:
            return phone  # the prefix has its own error
        e164 = parse_mobile(phone, region)
        if e164 is None:
            raise forms.ValidationError(self.error_messages['phone_invalid'],
                                        code='phone_invalid')
        if phone_is_registered(e164):
            raise forms.ValidationError(self.error_messages['phone_registered'],
                                        code='phone_registered')
        return e164

    def clean_how_found_other(self):
        """Required only with "Others"; ignored (not stored) otherwise."""
        text = self.cleaned_data['how_found_other']
        if self.cleaned_data.get('how_found') != HowFound.OTHERS:
            return ''
        if not text:
            raise forms.ValidationError(self.error_messages['how_found_other_required'],
                                        code='required')
        return text

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if self.has_password and bool(password1) != bool(password2):
            # Optional passwords: once one is filled in, both are needed.
            empty = 'password2' if password1 else 'password1'
            self.add_error(empty, forms.ValidationError(
                self.fields[empty].error_messages['required'], code='required'))
        elif password1 and password2 and password1 != password2:
            self.add_error('password2', forms.ValidationError(
                self.error_messages['password_mismatch'], code='password_mismatch'))
        elif password1:
            # AUTH_PASSWORD_VALIDATORS: min. 12, common, numeric, similarity to the names
            # and email, and breached (Have I Been Pwned).
            user = get_user_model()(**{
                name: cleaned_data.get(name) or ''
                for name in ('email', 'first_name', 'middle_name', 'last_name')})
            with collect_unavailable_checks() as unavailable:
                try:
                    password_validation.validate_password(password1, user)
                except forms.ValidationError as error:
                    self.add_error('password1', error)
            if BREACHED_PASSWORD_CHECK in unavailable:
                self.unavailable_checks.append(('password1', BREACHED_PASSWORD_CHECK))
        return cleaned_data


CHECKBOX_CLASSES = 'mt-[3px] h-5 w-5 shrink-0 cursor-pointer accent-green-deep'

# The legal step's checkbox label for each kind of document (its title is the current
# version's, in the page language).
LEGAL_LABELS = {
    LegalDocument.Kind.TERMS: _('I have read and accept the %(title)s.'),
    LegalDocument.Kind.CONDITIONS: _('I have read and accept the %(title)s.'),
    LegalDocument.Kind.ORMCO_TPS: _('I accept the %(title)s (optional).'),
    LegalDocument.Kind.COMMERCIAL_CONSENT: _(
        'I agree to receive commercial communications from the MyTPDSO group, as '
        'described above (optional).'),
}
LEGAL_REQUIRED_MESSAGES = {
    LegalDocument.Kind.TERMS: _('You must accept the Terms to continue.'),
    LegalDocument.Kind.CONDITIONS: _('You must accept the Conditions to continue.'),
}


def shown_document_ids(documents):
    """The hidden `shown` value: the ids of the documents on the page, e.g. "3,4,7,9"."""
    return ','.join(str(document.pk) for document in documents)


class LegalForm(forms.Form):
    """The legal step: one checkbox per current document (`documents`, {kind: version},
    legal.models.LegalDocument.objects.current_by_kind), named by its kind. Terms and
    Conditions must be ticked (error code `required`); the Ormco TPS contract and the
    commercial consent are optional. None starts ticked but by `initial`.

    The hidden `shown` field carries the ids of the versions on the page. If any is no
    longer current when the form is sent (a version was published meanwhile), the form
    is refused (`documents_changed`), unticked, and shows the current versions again.

    While the Terms or the Conditions have no published version, every submission is
    refused (`documents_missing`): signup can't go on without them (CAP-4)."""

    error_messages = {
        'documents_changed': _('The documents have been updated. Please review them '
                               'again.'),
        'documents_missing': _('Signup is not available right now. Please try again '
                               'later.'),
    }

    shown = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, documents, **kwargs):
        super().__init__(*args, **kwargs)
        self.documents = {kind: documents[kind] for kind in LegalDocument.Kind.values
                          if kind in documents}
        self.shown_ids = shown_document_ids(self.documents.values())
        self.fields['shown'].initial = self.shown_ids
        for kind, document in self.documents.items():
            required = kind in LegalDocument.REQUIRED_KINDS
            field = forms.BooleanField(
                # The consent label names no title: a missing key is ignored.
                label=LEGAL_LABELS[kind] % {'title': document.title},
                required=required,
                widget=forms.CheckboxInput(attrs={'class': CHECKBOX_CLASSES}))
            if required:
                field.error_messages['required'] = LEGAL_REQUIRED_MESSAGES[kind]
            self.fields[kind] = field

    def clean(self):
        missing = [kind for kind in LegalDocument.REQUIRED_KINDS if kind not in self.documents]
        if missing:
            # Reported to Sentry: an operator has to publish them in the admin.
            logger.error('Signup legal step refused: no published version of %s',
                         ', '.join(missing))
            for kind in self.documents:  # only this error is shown and logged
                self._errors.pop(kind, None)
            raise forms.ValidationError(self.error_messages['documents_missing'],
                                        code='documents_missing')
        submitted = set(filter(None, self.cleaned_data.get('shown', '').split(',')))
        if submitted != set(self.shown_ids.split(',')):
            # Nothing ticked on an older version counts: show the current ones, unticked.
            data = self.data.copy()
            for kind in self.documents:
                self._errors.pop(kind, None)
                self.cleaned_data.pop(kind, None)
                data.pop(kind, None)
            data['shown'] = self.shown_ids
            self.data = data
            raise forms.ValidationError(self.error_messages['documents_changed'],
                                        code='documents_changed')
        return self.cleaned_data

    def rows(self):
        """(bound field, document, body as safe HTML) per document, in order."""
        return [(self[kind], document, render_markdown(document.body))
                for kind, document in self.documents.items()]

    def choices(self):
        """{kind: accepted} of the valid form."""
        return {kind: bool(self.cleaned_data.get(kind)) for kind in self.documents}


class CodeForm(StyledForm):
    """A verification code for `contact`, checked with the provider (signup.verification)
    over `channel` for `purpose`. A wrong code is `invalid_code`; no valid code (expired,
    or invalidated after too many wrong attempts) is `code_expired`; a check the provider
    could not make (CheckFailed) is `check_failed`."""

    channel = EMAIL
    purpose = PURPOSE_SIGNUP

    error_messages = {
        'invalid_code': _('This code is not correct. Check it, or send a new one.'),
        'code_expired': _('This code is no longer valid. Send a new code.'),
        'check_failed': _("We couldn't check your code right now. Please try again."),
    }

    code = forms.CharField(
        label=_('Code'),
        max_length=CODE_LENGTH + 4,  # room for stray spaces; checked after stripping
        widget=forms.TextInput(attrs={
            'autocomplete': 'one-time-code',
            'inputmode': 'numeric',
            'autofocus': True,
        }),
    )

    def __init__(self, *args, contact, **kwargs):
        self.contact = contact
        super().__init__(*args, **kwargs)

    def clean_code(self):
        code = self.cleaned_data['code'].replace(' ', '')
        try:
            result = get_provider().check(self.channel, self.contact, self.purpose, code)
        except CheckFailed as error:
            # Neither right nor wrong (not counted as a wrong attempt): the person tries
            # again. No contact or code here (the error's message holds only Twilio's error
            # code and HTTP status); the event log gets `OTP check failed`.
            logger.error('Signup code check failed: channel=%s purpose=%s error=%s',
                         self.channel, self.purpose, error)
            raise forms.ValidationError(self.error_messages['check_failed'],
                                        code='check_failed') from None
        if result == NO_CODE:
            raise forms.ValidationError(self.error_messages['code_expired'],
                                        code='code_expired')
        if result != APPROVED:
            raise forms.ValidationError(self.error_messages['invalid_code'],
                                        code='invalid_code')
        return code


class EmailCodeForm(CodeForm):
    """The code sent to the email address."""

    def __init__(self, *args, email, **kwargs):
        super().__init__(*args, contact=email, **kwargs)


class MobileCodeForm(CodeForm):
    """The code sent to the mobile (by WhatsApp or SMS: the same code)."""

    channel = SMS

    def __init__(self, *args, phone, **kwargs):
        super().__init__(*args, contact=phone, **kwargs)


class ResumeCodeForm(EmailCodeForm):
    """The code that resumes a signup in progress, sent to the matched registration's
    email, for the purpose `resume`. It also proves control of that email."""

    purpose = PURPOSE_RESUME
