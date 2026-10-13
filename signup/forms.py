import phonenumbers
from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.utils.translation import gettext_lazy as _
from django_countries import countries

from accounts.crypto import phone_index
from accounts.models import HOW_FOUND_OTHER_MAX_LENGTH, HowFound, Title
from accounts.password_validation import BREACHED_PASSWORD_CHECK, collect_unavailable_checks

from .checks import DISPOSABLE_EMAIL_CHECK, email_domain, is_disposable_domain
from .verification import (CHANNELS, CODE_LENGTH, EMAIL, PURPOSE_RESUME, PURPOSE_SIGNUP,
                           get_provider)


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


# Failure reasons for the event log, in the wording of registration-failure-policies.md.
# Any other validation failure is logged with its form error code.
FAILURE_REASONS = {
    'email_registered': 'email exists',
    'phone_registered': 'phone exists',
    'email_pending': 'email pending',
    'phone_pending': 'phone pending',
    'email_disposable': 'email disposable',
    'invalid_code': 'OTP verification failed',
}
EMAIL_EXISTS = FAILURE_REASONS['email_registered']
PHONE_EXISTS = FAILURE_REASONS['phone_registered']
EMAIL_PENDING = FAILURE_REASONS['email_pending']
PHONE_PENDING = FAILURE_REASONS['phone_pending']


def failure_reason(code):
    """The logged reason for a form error code."""
    return FAILURE_REASONS.get(code, code or 'invalid')


def email_is_registered(email):
    """Whatever its case; through the blind index, since the email is encrypted."""
    return get_user_model().objects.by_email(email).exists()


def phone_is_registered(phone):
    """A mobile (E.164) of an existing user; through the blind index, since it is
    encrypted."""
    return bool(phone) and get_user_model().objects.filter(
        phone_index=phone_index(phone)).exists()


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

    title = forms.ChoiceField(
        label=_('Title / Treatment'),
        choices=BLANK_CHOICE + Title.choices,
        widget=forms.Select(attrs={'autofocus': True, 'autocomplete': 'honorific-prefix'}),
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
    email = forms.EmailField(
        label=_('Email'),
        max_length=254,
        widget=forms.EmailInput(attrs={'autocomplete': 'email'}),
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.unavailable_checks = []

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
        if password1 and password2 and password1 != password2:
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


class EmailCodeForm(StyledForm):
    """Step 2: the code sent to the email address."""

    error_messages = {
        'invalid_code': _('This code is not correct. Check it, or send a new one.'),
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

    def __init__(self, *args, email, **kwargs):
        self.email = email
        super().__init__(*args, **kwargs)

    def clean_code(self):
        code = self.cleaned_data['code'].replace(' ', '')
        if not get_provider().check(EMAIL, self.email, PURPOSE_SIGNUP, code):
            raise forms.ValidationError(self.error_messages['invalid_code'],
                                        code='invalid_code')
        return code


class ResumeCodeForm(StyledForm):
    """The code that resumes a signup in progress, sent to the matched registration's
    email or mobile (`channel`, `contact`), for the purpose `resume`."""

    error_messages = {
        'invalid_code': _('This code is not correct. Check it, or send a new one.'),
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

    def __init__(self, *args, channel, contact, **kwargs):
        if channel not in CHANNELS:
            raise ValueError(f'Unknown verification channel: {channel!r}')
        self.channel = channel
        self.contact = contact
        super().__init__(*args, **kwargs)

    def clean_code(self):
        code = self.cleaned_data['code'].replace(' ', '')
        if not get_provider().check(self.channel, self.contact, PURPOSE_RESUME, code):
            raise forms.ValidationError(self.error_messages['invalid_code'],
                                        code='invalid_code')
        return code
