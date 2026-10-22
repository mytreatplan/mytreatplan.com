import phonenumbers
from django import forms
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from signup.forms import CHECKBOX_CLASSES, CodeForm, StyledForm
from signup.verification import EMAIL, MOBILE, PURPOSE_LOGIN, SMS

# The one error of every failed login (a wrong password, an unknown email, an inactive or
# erased account): it never tells which.
INVALID_LOGIN_MESSAGE = _('The email address or password is not correct.')


class LoginForm(StyledForm):
    """Email and password, checked with `authenticate` (accounts.backends.EmailBackend:
    the email is found through its blind index, whatever its case; inactive accounts,
    erased ones among them, are refused). Every failure is the non-field error
    `invalid_login`. The CAPTCHA is checked by the view before the form is validated."""

    error_messages = {
        'invalid_login': INVALID_LOGIN_MESSAGE,
    }

    # Not an EmailField: a malformed address gets the same generic error as any other.
    email = forms.CharField(
        label=_('Email'),
        max_length=254,
        widget=forms.EmailInput(attrs={'autocomplete': 'username', 'autofocus': True}),
    )
    password = forms.CharField(
        label=_('Password'),
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
    )

    def __init__(self, request, *args, **kwargs):
        self.request = request
        self.user = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        email, password = cleaned_data.get('email'), cleaned_data.get('password')
        if email and password:
            self.user = authenticate(self.request, username=email, password=password)
            if self.user is None:
                raise ValidationError(self.error_messages['invalid_login'],
                                      code='invalid_login')
        return cleaned_data

    def get_user(self):
        return self.user

    def refuse_login(self):
        """Give the valid form the generic `invalid_login` error after all (an account
        without a mobile, which cannot log in: decision 2), as a wrong password would."""
        self.user = None
        self.add_error(None, ValidationError(self.error_messages['invalid_login'],
                                             code='invalid_login'))


# --- The second factor (epic-login entry 2) -------------------------------------------

MASK = '\u2022\u2022\u2022'  # •••


def mask_email(email):
    """The first character, •••, then @ and the domain: `a•••@example.com`."""
    local, _at, domain = (email or '').rpartition('@')
    return f'{local[:1]}{MASK}@{domain}'


def mask_phone(phone):
    """The country code and the last 3 digits of an E.164 number: `+34 ••• ••• 374`."""
    try:
        prefix = f'+{phonenumbers.parse(phone).country_code} '
    except phonenumbers.NumberParseException:
        prefix = ''
    return f'{prefix}{MASK} {MASK} {(phone or "")[-3:]}'


class LoginContactForm(forms.Form):
    """Where the login code goes: the account's email or its mobile (WhatsApp, else SMS).
    The page posts it with one button per contact."""

    contact = forms.ChoiceField(choices=[(EMAIL, EMAIL), (MOBILE, MOBILE)])


class LoginCodeForm(CodeForm):
    """The login code (purpose `login`) sent to the chosen contact: `channel` is EMAIL for
    the email; a mobile's code is checked over SMS (the same code as by WhatsApp). With
    `remember` ticked, an approved code also remembers this browser for 90 days."""

    purpose = PURPOSE_LOGIN

    # Remember this browser for this account (epic-login entry 3, accounts.devices).
    remember = forms.BooleanField(
        label=_('Remember me on this device/browser for 90 days'),
        required=False,
        widget=forms.CheckboxInput(attrs={'class': CHECKBOX_CLASSES}),
    )

    def __init__(self, *args, kind, contact, **kwargs):
        self.channel = EMAIL if kind == EMAIL else SMS
        super().__init__(*args, contact=contact, **kwargs)
