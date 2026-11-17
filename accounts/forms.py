import zoneinfo
from urllib.parse import urlsplit

import phonenumbers
from django import forms
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.hashers import check_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from legal.models import LegalDocument
from regions.countries import lookup_country
from signup.forms import (BLANK_CHOICE, CHECKBOX_CLASSES, COUNTRY_BANNED_MESSAGE, CodeForm,
                          StyledForm, country_choices, parse_mobile, phone_prefix_choices)
from signup.verification import EMAIL, MOBILE, PURPOSE_LOGIN, SMS

from . import fiscal
from .models import FiscalEntity, Profession, Title
from .profile import CHANGE_PURPOSE, current_contact, mobile_in_use

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


# The signing salt of the personal-data page's consent token (bound to the account's pk).
CONSENT_TOKEN_SALT = 'accounts.personal_data.consents'


def time_zone_choices():
    """Every standard (IANA) time zone, sorted, as (name, name)."""
    return [(name, name) for name in sorted(zoneinfo.available_timezones())]


def parse_landline(number, region):
    """A landline in E.164 form (the mobile's format), or None when it is not a valid
    number for the prefix of `region`. Any valid number type is accepted (some plans
    cannot tell a fixed line from a mobile)."""
    if any(char.isalpha() for char in number):
        return None
    try:
        parsed = phonenumbers.parse(number, region)
    except phonenumbers.NumberParseException:
        return None
    if (parsed.extension
            or parsed.country_code != phonenumbers.country_code_for_region(region)
            or not phonenumbers.is_valid_number(parsed)):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def split_landline(e164, default_region=''):
    """(prefix region, national number) of a stored landline, to refill the form; the
    `default_region` and '' without one."""
    if not e164:
        return default_region, ''
    try:
        parsed = phonenumbers.parse(e164, None)
    except phonenumbers.NumberParseException:
        return default_region, e164
    region = phonenumbers.region_code_for_number(parsed) or default_region
    return region, phonenumbers.format_number(parsed,
                                              phonenumbers.PhoneNumberFormat.NATIONAL)


class PersonalDataForm(StyledForm):
    """The settings area's personal-data page (epic-doctor-profile entry 2, Annex I).

    Declares only what the doctor may change: language (only on a site with more than
    one), city, time zone, title, profession, names, landline (prefix and number, stored
    in E.164) and the optional consents whose kind has a published document
    (`consent_state`). Country, mobile, login email and the alternative (contact) email
    are not fields: whatever a POST carries for them is ignored (and accounts.profile
    refuses them). The mobile and the contact email change on pages of their own, after
    a code (epic-doctor-profile entry 3, `MobileChangeForm`, `EmailChangeForm`).

    The hidden `consent_token` records what the page showed for each consent (document
    id, ticked), signed for this page and account. A missing or tampered token
    (`consents_stale`), or a document that is no longer the current one
    (`consents_changed`), refuses the whole form and shows the consents as they are now;
    otherwise `consents()` holds only the ticks the doctor changed on this page."""

    error_messages = {
        'landline_invalid': _('Enter a valid phone number for the selected prefix.'),
        'landline_prefix': _('Select the prefix of the landline.'),
        'consents_stale': _('This page has expired. Please reload it and try again.'),
        'consents_changed': _('The consent texts have been updated. Please review them '
                              'again and save.'),
    }

    language = forms.ChoiceField(label=_('Language'))
    city = forms.CharField(label=_('Location (city)'), max_length=150, required=False,
                           widget=forms.TextInput(attrs={'autocomplete': 'address-level2'}))
    time_zone = forms.ChoiceField(label=_('Time zone'), choices=time_zone_choices)
    title = forms.ChoiceField(label=_('Title / Treatment'), choices=BLANK_CHOICE + Title.choices,
                              widget=forms.Select(attrs={'autocomplete': 'honorific-prefix'}))
    profession = forms.ChoiceField(label=_('Profession'), required=False,
                                   choices=BLANK_CHOICE + Profession.choices)
    first_name = forms.CharField(label=_('Name'), max_length=150,
                                 widget=forms.TextInput(attrs={'autocomplete': 'given-name'}))
    middle_name = forms.CharField(
        label=_('Middle name or initial (optional)'), max_length=150, required=False,
        widget=forms.TextInput(attrs={'autocomplete': 'additional-name'}))
    last_name = forms.CharField(label=_('Surname(s)'), max_length=150,
                                widget=forms.TextInput(attrs={'autocomplete': 'family-name'}))
    landline_prefix = forms.ChoiceField(label=_('Landline prefix'), required=False,
                                        choices=phone_prefix_choices)
    landline = forms.CharField(label=_('Landline'), max_length=30, required=False,
                               widget=forms.TextInput(attrs={'autocomplete': 'tel-national',
                                                             'inputmode': 'tel'}))
    CONSENT_LABELS = {
        LegalDocument.Kind.WHATSAPP_CONTACT: _(
            'I agree to be contacted by MyTreatPlan via WhatsApp.'),
        LegalDocument.Kind.COMMERCIAL_CONSENT: _(
            'Yes, I want to receive commercial information from MyTreatPlan and its '
            'partners.'),
    }

    # The signed record of the consent section as the page showed it (review fix): per
    # kind, the document id shown and whether it was ticked.
    consent_token = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, user_pk, consent_state=None, **kwargs):
        """`consent_state`: {kind: (current document, accepted)} read now
        (accounts.profile.consents_of); `user_pk` binds the consent token to the account."""
        super().__init__(*args, **kwargs)
        if len(settings.LANGUAGES) > 1:
            self.fields['language'].choices = list(settings.LANGUAGES)
        else:
            del self.fields['language']  # one-language site (.ae): a posted value is ignored
        self.consent_state = dict(consent_state or {})
        self.consent_kinds = tuple(self.consent_state)
        self.token_salt = f'{CONSENT_TOKEN_SALT}:{user_pk}'
        self.shown_consents = {}
        for kind in self.consent_kinds:
            self.fields[kind] = forms.BooleanField(
                label=self.CONSENT_LABELS[kind], required=False,
                widget=forms.CheckboxInput(attrs={'class': CHECKBOX_CLASSES}))
        self.fields['consent_token'].initial = self.current_token()

    def current_token(self):
        """The consent token of the current state: {kind: [document id, ticked]}, signed
        and salted for this page and this account."""
        return signing.dumps({kind: [document.pk, bool(accepted)]
                              for kind, (document, accepted) in self.consent_state.items()},
                             salt=self.token_salt)

    def _shown(self):
        """{kind: (document id, ticked)} of the posted token; None when it is missing,
        tampered with, another account's or malformed."""
        try:
            shown = signing.loads(self.data.get('consent_token') or '', salt=self.token_salt)
        except signing.BadSignature:
            return None
        if not isinstance(shown, dict):
            return None
        result = {}
        for kind, value in shown.items():
            if (not isinstance(value, list) or len(value) != 2
                    or not isinstance(value[0], int) or not isinstance(value[1], bool)):
                return None
            result[kind] = (value[0], value[1])
        return result

    def _reset_consents(self):
        """Show the consents as they are now (current documents and choices), with a
        fresh token, after a refusal: nothing ticked on an old page counts."""
        data = self.data.copy()
        for kind, (_document, accepted) in self.consent_state.items():
            if accepted:
                data[kind] = 'on'
            else:
                data.pop(kind, None)
        data['consent_token'] = self.current_token()
        self.data = data

    def clean(self):
        cleaned = super().clean()
        number = (cleaned.get('landline') or '').strip()
        if number:
            region = cleaned.get('landline_prefix')
            if not region:
                self.add_error('landline_prefix', forms.ValidationError(
                    self.error_messages['landline_prefix'], code='landline_prefix'))
            else:
                e164 = parse_landline(number, region)
                if e164 is None:
                    self.add_error('landline', forms.ValidationError(
                        self.error_messages['landline_invalid'], code='landline_invalid'))
                else:
                    cleaned['landline'] = e164
        elif 'landline' in cleaned:
            cleaned['landline'] = ''
        shown = self._shown()
        if shown is None:
            self._reset_consents()
            raise forms.ValidationError(self.error_messages['consents_stale'],
                                        code='consents_stale')
        current = {kind: document.pk for kind, (document, _a) in self.consent_state.items()}
        if {kind: document_id for kind, (document_id, _t) in shown.items()} != current:
            # A version was published (or withdrawn) since the page was shown, as signup's
            # `documents_changed`: the doctor reviews the current texts first.
            self._reset_consents()
            raise forms.ValidationError(self.error_messages['consents_changed'],
                                        code='consents_changed')
        self.shown_consents = {kind: ticked for kind, (_id, ticked) in shown.items()}
        return cleaned

    def changes(self):
        """{field: value} of the profile and account fields whose value differs from the
        form's initial one (an untouched default, e.g. the preselected time zone, is not
        stored). The landline counts as changed when its prefix or number did."""
        names = ('language', 'city', 'time_zone', 'title', 'profession', 'first_name',
                 'middle_name', 'last_name')
        changed = {name: self.cleaned_data[name] for name in names
                   if name in self.fields and name in self.changed_data}
        if {'landline', 'landline_prefix'} & set(self.changed_data):
            changed['landline'] = self.cleaned_data['landline']
        return changed

    def consents(self):
        """{kind: ticked} of the consents the doctor changed on this page: only where the
        posted tick differs from the tick the page showed (its token), so a stale tab
        that edits another field never repeats a choice made elsewhere since."""
        posted = {kind: bool(self.cleaned_data.get(kind)) for kind in self.consent_kinds}
        return {kind: ticked for kind, ticked in posted.items()
                if ticked != self.shown_consents.get(kind)}


# --- The contact changes (epic-doctor-profile entry 3) ------------------------------------

class ContactChangeForm(StyledForm):
    """The start of a contact change: the new value and the account's current password.
    The value's checks that tell anything about other accounts (`check_value`) run only
    after a right password. A wrong password is the `password` error `wrong_password`
    (the view counts it on the login's password lockout). Checked with the hasher only:
    the account is never saved here."""

    error_messages = {
        'wrong_password': _('The password is not correct.'),
    }

    password = forms.CharField(
        label=_('Current password'),
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        # The password last: the new value is what the page is about.
        self.order_fields([name for name in self.fields if name != 'password'] + ['password'])

    def clean_password(self):
        password = self.cleaned_data['password']
        if not check_password(password, self.user.password):
            raise forms.ValidationError(self.error_messages['wrong_password'],
                                        code='wrong_password')
        return password

    def clean(self):
        cleaned = super().clean()
        if 'password' in cleaned and self.new_value():
            self.check_value(self.new_value())
        return cleaned

    def new_value(self):
        """The cleaned new value, or None."""
        raise NotImplementedError

    def check_value(self, value):
        """Add the errors of a valid new value (after a right password)."""


class MobileChangeForm(ContactChangeForm):
    """A new mobile: the signup prefix and number (signup.forms.AccountForm's fields),
    stored in E.164. Not the current one; not another account's (MOBILE_IN_USE)."""

    error_messages = {
        **ContactChangeForm.error_messages,
        'phone_invalid': _('Enter a valid mobile phone number for the selected prefix.'),
        'same_mobile': _('This is already your mobile phone number.'),
        'phone_in_use': _('This mobile phone number is already used by another account.'),
    }

    phone_prefix = forms.ChoiceField(label=_('International prefix'),
                                     choices=phone_prefix_choices)
    phone = forms.CharField(
        label=_('New mobile phone number'),
        max_length=32,
        widget=forms.TextInput(attrs={'type': 'tel', 'autocomplete': 'tel-national',
                                      'inputmode': 'tel', 'autofocus': True}),
    )

    def clean_phone(self):
        phone = self.cleaned_data['phone']
        region = self.cleaned_data.get('phone_prefix')
        if not region:
            return None  # the prefix has its own error
        e164 = parse_mobile(phone, region)
        if e164 is None:
            raise forms.ValidationError(self.error_messages['phone_invalid'],
                                        code='phone_invalid')
        return e164

    def new_value(self):
        return self.cleaned_data.get('phone')

    def check_value(self, value):
        if value == self.user.phone:
            self.add_error('phone', forms.ValidationError(
                self.error_messages['same_mobile'], code='same_mobile'))
        elif mobile_in_use(value, self.user):
            self.add_error('phone', forms.ValidationError(
                self.error_messages['phone_in_use'], code='phone_in_use'))


class EmailChangeForm(ContactChangeForm):
    """A new contact (alternative) email: not the login email, not the current one."""

    error_messages = {
        **ContactChangeForm.error_messages,
        'alternative_is_login': _('Enter an email address other than your login email.'),
        'same_email': _('This is already your contact email.'),
    }

    email = forms.EmailField(
        label=_('New contact email'), max_length=254,
        widget=forms.EmailInput(attrs={'autocomplete': 'email', 'autofocus': True}))

    def clean_email(self):
        return get_user_model().objects.normalize_email(self.cleaned_data['email'])

    def new_value(self):
        return self.cleaned_data.get('email')

    def check_value(self, value):
        if value.casefold() == (self.user.email or '').casefold():
            self.add_error('email', forms.ValidationError(
                self.error_messages['alternative_is_login'], code='alternative_is_login'))
        elif value.casefold() == current_contact(self.user, EMAIL).casefold():
            self.add_error('email', forms.ValidationError(
                self.error_messages['same_email'], code='same_email'))


class ChangeCodeForm(CodeForm):
    """The code of a contact change (purpose accounts.profile.CHANGE_PURPOSE), sent to the
    new value: `channel` EMAIL for an email; a mobile's code is checked over SMS (the same
    code as by WhatsApp). Wrong codes never count on the login lockout: after the
    provider's 5th wrong attempt the code is no longer valid and a new one is needed."""

    purpose = CHANGE_PURPOSE

    def __init__(self, *args, kind, contact, **kwargs):
        self.channel = EMAIL if kind == EMAIL else SMS
        super().__init__(*args, contact=contact, **kwargs)


# --- The fiscal entities (epic-doctor-profile entry 5) -------------------------------------

class FiscalEntityForm(StyledForm):
    """One form for both kinds of fiscal entity (accounts.fiscal): the kind's radio buttons
    and both kinds' name fields are always in the page (a small script only hides the other
    kind's), and the server ignores the fields of the kind not chosen. The VAT id is read
    only for billing country AE (the page shows it only then; any other country's is
    dropped). Required fields, the country's region and banned checks and the field limits
    are accounts.fiscal.problems, run here so the errors sit on their fields; the service
    runs them again at the save. The tax id and the VAT id are normalised (`entity_data`).

    A country of the other region is refused with the new message and `other_site_url`
    (the other site's address, for the page's link); a banned one with signup's message.
    `version` (hidden) is the entity's version the page showed, for the stale check."""

    error_messages = {
        'required': _('This field is required.'),
        'invalid': _('Enter a valid value.'),
        'invalid_email': _('Enter a valid email address.'),
        'too_long': _('This value is too long.'),
        'country_banned': COUNTRY_BANNED_MESSAGE,
        'country_other_region': _('This billing country is served by MyTreatPlan %(site)s. '
                                  'Billing data for it cannot be saved here.'),
        'limit': _('You can have at most %(max)d fiscal entities. Delete one to add '
                   'another.'),
        'changed_elsewhere': _('This fiscal entity was changed elsewhere. Please reload '
                               'the page and try again.'),
    }

    kind = forms.ChoiceField(label=_('Kind'), choices=FiscalEntity.KINDS,
                             widget=forms.RadioSelect, initial=FiscalEntity.PERSON)
    first_name = forms.CharField(label=_('Name'), max_length=150, required=False,
                                 widget=forms.TextInput(attrs={'autocomplete': 'given-name'}))
    last_name = forms.CharField(label=_('Surname(s)'), max_length=150, required=False,
                                widget=forms.TextInput(attrs={'autocomplete': 'family-name'}))
    legal_name = forms.CharField(label=_('Legal name'), max_length=200, required=False,
                                 widget=forms.TextInput(attrs={'autocomplete': 'organization'}))
    tax_id = forms.CharField(label=_('Tax id (NIF / CIF / VAT)'), max_length=60,
                             required=False,
                             widget=forms.TextInput(attrs={'autocomplete': 'off'}))
    vat_id = forms.CharField(label=_('VAT id'), max_length=60, required=False,
                             widget=forms.TextInput(attrs={'autocomplete': 'off'}))
    address_line1 = forms.CharField(
        label=_('Address (line 1)'), max_length=200, required=False,
        widget=forms.TextInput(attrs={'autocomplete': 'address-line1'}))
    address_line2 = forms.CharField(
        label=_('Address (line 2)'), max_length=200, required=False,
        widget=forms.TextInput(attrs={'autocomplete': 'address-line2'}))
    city = forms.CharField(label=_('City'), max_length=150, required=False,
                           widget=forms.TextInput(attrs={'autocomplete': 'address-level2'}))
    postal_code = forms.CharField(label=_('Postal code'), max_length=30, required=False,
                                  widget=forms.TextInput(attrs={'autocomplete': 'postal-code'}))
    province = forms.CharField(label=_('Province'), max_length=150, required=False,
                               widget=forms.TextInput(attrs={'autocomplete': 'address-level1'}))
    country = forms.ChoiceField(label=_('Billing country'), choices=country_choices,
                                required=False)
    billing_email = forms.CharField(
        label=_('Billing email'), max_length=254, required=False,
        help_text=_('Optional. Without it, invoices are sent to your login email.'),
        widget=forms.EmailInput(attrs={'autocomplete': 'off'}))
    version = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.other_site_url = ''
        self.stale = False
        self.fields['kind'].widget.attrs.pop('class', None)

    @property
    def country_code(self):
        """The billing country the page shows now: the posted one, else the initial."""
        raw = self.data.get('country') if self.is_bound else self.initial.get('country')
        return str(raw or '').strip().upper()

    @property
    def show_vat(self):
        return fiscal.is_vat_country(self.country_code)

    def _message(self, code, site_url=''):
        message = self.error_messages[code]
        if code == 'country_other_region':
            return message % {'site': urlsplit(site_url).netloc}
        if code == 'limit':
            return message % {'max': fiscal.MAX_ENTITIES}
        return message

    def clean(self):
        cleaned = super().clean()
        data = {name: cleaned[name] for name in fiscal.FIELDS if name in cleaned}
        data = fiscal.clean_data(data)
        for name in fiscal.FIELDS:
            data.setdefault(name, '')
        for name, code in fiscal.problems(data).items():
            if name in self.errors:
                continue
            site_url = ''
            if code == 'country_other_region':
                site_url = self.other_site_url = lookup_country(data['country']).site_url
            if code == 'invalid' and name == 'billing_email':
                code = 'invalid_email'
            self.add_error(name, forms.ValidationError(self._message(code, site_url),
                                                       code=code))
        return cleaned

    def entity_data(self):
        """The cleaned values to give accounts.fiscal (FIELDS only; normalised)."""
        return fiscal.clean_data({name: self.cleaned_data.get(name, '')
                                  for name in fiscal.FIELDS})

    def add_service_problems(self, problems):
        """Show a FiscalInvalid's problems the form did not find itself (the limit, a race)."""
        for name, code in problems.items():
            site_url = ''
            if code == 'country_other_region':
                site_url = self.other_site_url = lookup_country(
                    self.cleaned_data.get('country', '')).site_url
            self.add_error(None if name == fiscal.ALL else name,
                           forms.ValidationError(self._message(code, site_url), code=code))

    def refuse_stale(self):
        self.stale = True
        self.add_error(None, forms.ValidationError(self.error_messages['changed_elsewhere'],
                                                   code='changed_elsewhere'))
