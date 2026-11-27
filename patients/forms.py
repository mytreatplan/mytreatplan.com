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


class CommentsForm(StyledForm):
    """The comments of a patient's file (epic-case-lifecycle 10.4): a textarea of at most
    2000 characters and the version of the patient the form was shown for (the stale-edit
    check of `patients.service.update_comments`)."""

    # No `max_length` on the field: a browser submits a line break as CRLF (2 characters)
    # though its `maxlength` counts 1, so the limit is checked after normalising.
    comments = forms.CharField(
        label=_('Comments'), required=False,
        widget=forms.Textarea(attrs={'rows': 5, 'autocomplete': 'off', 'maxlength': 2000}))
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)

    def clean_comments(self):
        text = self.cleaned_data['comments'].replace('\r\n', '\n').replace('\r', '\n')
        if len(text) > 2000:
            raise forms.ValidationError(
                _('Ensure this value has at most %(limit_value)d characters (it has '
                  '%(show_value)d).'), code='max_length',
                params={'limit_value': 2000, 'show_value': len(text)})
        return text


class StatusForm(forms.Form):
    """Set the patient active or inactive; `version` as in `CommentsForm`."""

    active = forms.BooleanField(required=False)
    version = forms.IntegerField(min_value=1)
