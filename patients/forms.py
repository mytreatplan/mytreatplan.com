from django import forms
from django.utils.translation import gettext_lazy as _

from signup.forms import StyledForm


class PatientForm(StyledForm):
    """The create-patient form: a first name, a surname and a reference of the doctor's
    own, all optional but at least one of them; and optional comments."""

    error_messages = {
        'needed': _('Enter a first name, a surname or a reference.'),
    }

    first_name = forms.CharField(
        label=_('First name'), max_length=150, required=False,
        widget=forms.TextInput(attrs={'autocomplete': 'off'}))
    surname = forms.CharField(
        label=_('Surname'), max_length=150, required=False,
        widget=forms.TextInput(attrs={'autocomplete': 'off'}))
    reference = forms.CharField(
        label=_('Your reference'), max_length=100, required=False,
        help_text=_('Optional. Your own reference for this patient.'),
        widget=forms.TextInput(attrs={'autocomplete': 'off'}))
    comments = forms.CharField(
        label=_('Comments'), max_length=2000, required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'autocomplete': 'off'}))

    def clean(self):
        cleaned = super().clean()
        if not any(cleaned.get(name) for name in ('first_name', 'surname', 'reference')):
            self.add_error('first_name', self.error_messages['needed'])
        return cleaned
