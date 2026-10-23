"""The contact form's fields and validation (story 1.5), as enviar.php's: control characters
become spaces and the value is trimmed, then the lengths, the required fields, the email,
the phone pattern and the yes/no answers are checked. The anti-spam checks and the limits
are mytp_publicsite.contact's."""
import re

from django import forms
from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _

from signup.forms import FIELD_CLASSES

from .models import (COUNTRY_MAX_LENGTH, EMAIL_MAX_LENGTH, NAME_MAX_LENGTH, PHONE_MAX_LENGTH,
                     Answer)

# Control characters (line breaks included, the way to inject a mail header).
CONTROL_CHARACTERS = re.compile(r'[\x00-\x1f\x7f]')
PHONE_PATTERN = r'^[0-9 +().\-]{6,32}$'
PHONE_MESSAGE = _('Enter a valid phone number: 6 to 32 digits, spaces and + ( ) . - only.')

# The yes/no questions: (field, label).
QUESTIONS = (
    ('aligners', _('Interested in aligners?')),
    ('club', _('Wants to know about the UAE club?')),
    ('decision', _('Makes purchasing decisions?')),
)


def clean_text(value):
    """Control characters become spaces, then the value is trimmed."""
    return CONTROL_CHARACTERS.sub(' ', value).strip()


class CleanedFieldMixin:
    def to_python(self, value):
        if isinstance(value, str):
            value = clean_text(value)
        return super().to_python(value)


class TextField(CleanedFieldMixin, forms.CharField):
    pass


class EmailField(CleanedFieldMixin, forms.EmailField):
    pass


class AnswerField(CleanedFieldMixin, forms.ChoiceField):
    """Empty, "yes" or "no"."""

    def __init__(self, label):
        super().__init__(label=label, choices=Answer.choices, required=False,
                         widget=forms.RadioSelect)


class ContactForm(forms.Form):
    first_name = TextField(label=_('First name'), max_length=NAME_MAX_LENGTH,
                           widget=forms.TextInput(attrs={'autocomplete': 'given-name'}))
    middle_name = TextField(label=_('Middle name or initial (optional)'),
                            max_length=NAME_MAX_LENGTH, required=False,
                            widget=forms.TextInput(attrs={'autocomplete': 'additional-name'}))
    surname = TextField(label=_('Surname'), max_length=NAME_MAX_LENGTH,
                        widget=forms.TextInput(attrs={'autocomplete': 'family-name'}))
    country = TextField(label=_('Country'), max_length=COUNTRY_MAX_LENGTH,
                        widget=forms.TextInput(attrs={'autocomplete': 'country-name'}))
    phone = TextField(label=_('Phone'), max_length=PHONE_MAX_LENGTH,
                      validators=[RegexValidator(PHONE_PATTERN, PHONE_MESSAGE, code='phone')],
                      widget=forms.TextInput(attrs={'type': 'tel', 'autocomplete': 'tel'}))
    email = EmailField(label=_('Email'), max_length=EMAIL_MAX_LENGTH,
                       widget=forms.EmailInput(attrs={'autocomplete': 'email'}))
    aligners = AnswerField(QUESTIONS[0][1])
    club = AnswerField(QUESTIONS[1][1])
    decision = AnswerField(QUESTIONS[2][1])

    # The text fields, in the page's order. The template picks the yes/no questions it
    # shows: not the UAE club one for now (decision 3, publicsite/contact/_form.html).
    TEXT_FIELDS = ('first_name', 'middle_name', 'surname', 'country', 'phone', 'email')

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('auto_id', 'contact_%s')
        super().__init__(*args, **kwargs)
        for name in self.TEXT_FIELDS:
            self.fields[name].widget.attrs.setdefault('class', FIELD_CLASSES)
