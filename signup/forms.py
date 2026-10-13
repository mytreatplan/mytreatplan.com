from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.utils.translation import gettext_lazy as _

from .verification import CODE_LENGTH, EMAIL, PURPOSE_SIGNUP, get_provider


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


# Shown when the email belongs to an existing user (step 1, and at account creation).
EMAIL_REGISTERED_MESSAGE = _('This email address is already registered.')


def email_is_registered(email):
    """Whatever its case; through the blind index, since the email is encrypted."""
    return get_user_model().objects.by_email(email).exists()


class AccountForm(StyledForm):
    """Step 1: email (the username) and password, typed twice."""

    error_messages = {
        'email_registered': EMAIL_REGISTERED_MESSAGE,
        'password_mismatch': _('The two passwords do not match.'),
    }

    email = forms.EmailField(
        label=_('Email'),
        max_length=254,
        widget=forms.EmailInput(attrs={'autocomplete': 'email', 'autofocus': True}),
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

    def clean_email(self):
        email = normalize_email(self.cleaned_data['email'])
        if email_is_registered(email):
            # Signup entry 5 adds the recovery link.
            raise forms.ValidationError(self.error_messages['email_registered'],
                                        code='email_registered')
        return email

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            self.add_error('password2', forms.ValidationError(
                self.error_messages['password_mismatch'], code='password_mismatch'))
        elif password1:
            # Django's validators (min. 12, common passwords, numeric, similarity to the email).
            user = get_user_model()(email=cleaned_data.get('email') or '')
            try:
                password_validation.validate_password(password1, user)
            except forms.ValidationError as error:
                self.add_error('password1', error)
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
