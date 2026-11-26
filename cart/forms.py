"""The case wizard's step forms (epic-ordering 9.16). Each step is one plain POST (no JavaScript
needed). The choices are built by `cart.wizard` per doctor and patient (`options`), so `clean()`
only checks that a value is one of them; `cart.service` stays the authority on every rule
(offers, add-ons, live work orders, quantities) and refuses again when the cart item is added.
A form's `selected(name)` lists the stored values, so the templates tick what was chosen when
the doctor comes back with Back."""
from django import forms
from django.utils.translation import gettext_lazy as _

from accounts import makers

from . import service
from .common import refusal_texts
from .models import MAKER_CHOICES, OTHER, OTHER_NAME_MAX_LENGTH, UNDECIDED

TPS, VIRTUAORTHO = 'tps', 'vo'
IN_HOUSE, BRAND = 'in_house', 'brand'


class StepForm(forms.Form):
    """A step's form: `doctor`, `patient` and the `options` the wizard built for them."""

    def __init__(self, *args, doctor=None, patient=None, options=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.doctor, self.patient, self.options = doctor, patient, options or {}

    def selected(self, name):
        """The values of field `name` as posted or stored, else its initial value."""
        value = self[name].value()
        if value is None or value == '':
            return []
        return [str(item) for item in (value if isinstance(value, (list, tuple)) else [value])]

    def checked(self, name, rows, key='value'):
        """`rows` (dicts) with 'checked' set when their `key` is a selected value of `name`."""
        chosen = self.selected(name)
        return [{**row, 'checked': str(row[key]) in chosen} for row in rows]


class ProductForm(StepForm):
    """TPS or VirtuaOrtho. A product the doctor is not offered is not a choice at all (a
    forged post is invalid); the page shows it greyed out."""
    product = forms.ChoiceField(widget=forms.RadioSelect, choices=())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].choices = [(card['value'], card['title'])
                                          for card in self.options.get('cards', ())
                                          if card['available']]

    @property
    def cards(self):
        return self.checked('product', self.options.get('cards', ()))


class MakerForm(StepForm):
    """TPS: in-house printing or a brand's aligners (a named maker, "not decided yet" or
    "other" with a name). A named maker needs a saved login. `options['kinds']`, when given,
    limits the kinds offered (a manual work order's branded TPS: a brand only, 9.9)."""
    kind = forms.ChoiceField(widget=forms.RadioSelect,
                             choices=[(IN_HOUSE, _('In-house printing')),
                                      (BRAND, _('Brand-made aligners'))])
    maker = forms.ChoiceField(required=False, choices=[
        ('', '---------'), *[(code, name) for code, name in MAKER_CHOICES
                             if code not in (UNDECIDED, OTHER)],
        (UNDECIDED, _('Not decided yet')), (OTHER, _('Other'))])
    other_name = forms.CharField(required=False, max_length=OTHER_NAME_MAX_LENGTH, strip=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        kinds = self.options.get('kinds')
        if kinds:
            self.fields['kind'].choices = [(value, label) for value, label
                                           in self.fields['kind'].choices if value in kinds]

    @property
    def selected_maker(self):
        return self.selected('maker')

    @property
    def kinds(self):
        allowed = {value for value, _label in self.fields['kind'].choices}
        return [card for card in self._kind_cards() if card['value'] in allowed]

    def _kind_cards(self):
        return self.checked('kind', [
            {'value': IN_HOUSE, 'title': _('In-house printing'),
             'text': _('You print the aligners yourself.')},
            {'value': BRAND, 'title': _('Brand-made aligners'),
             'text': _('A brand makes the aligners; choose which one next.')}])

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('kind') != BRAND:
            # In-house: no maker is kept.
            cleaned['maker'] = cleaned['other_name'] = ''
            return cleaned
        maker = cleaned.get('maker')
        texts = refusal_texts()
        if 'maker' in self.errors:
            return cleaned
        if not maker:
            self.add_error('maker', texts[service.MAKER_UNKNOWN])
        elif maker in makers.NAMED and not makers.has_login(self.doctor, maker):
            self.add_error('maker', texts[service.MAKER_LOGIN])
        elif maker == OTHER and not cleaned.get('other_name'):
            self.add_error('other_name', texts[service.OTHER_NAME])
        if maker != OTHER:
            cleaned['other_name'] = ''
        return cleaned


class VoTypeForm(StepForm):
    """VirtuaOrtho: One (without patient monitoring) or DigitalTwin (with it)."""
    variant = forms.ChoiceField(widget=forms.RadioSelect, choices=())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['variant'].choices = [(card['value'], card['title'])
                                          for card in self.options.get('cards', ())]

    @property
    def cards(self):
        return self.checked('variant', self.options.get('cards', ()))


class TpsOptionsForm(StepForm):
    """TPS: the length (ICE, short, medium, long) and the add-ons. Which add-on goes with
    which length is the service's rule; a wrong pair is refused when the item is added."""
    size = forms.ChoiceField(widget=forms.RadioSelect, choices=())
    add_on = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple,
                                       choices=())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['size'].choices = [(row['value'], row['label'])
                                       for row in self.options.get('sizes', ())]
        self.fields['add_on'].choices = [(row['code'], row['name'])
                                         for row in self.options.get('add_ons', ())]

    @property
    def sizes(self):
        return self.checked('size', self.options.get('sizes', ()))

    @property
    def add_ons(self):
        return self.checked('add_on', self.options.get('add_ons', ()), key='code')


class VoOptionsForm(StepForm):
    """VirtuaOrtho: what to buy ("<choice>:<size or product id>": a length, an ODB, retainers
    or Spark on Demand with its quantity), the add-ons and the shipping address."""
    item = forms.ChoiceField(widget=forms.RadioSelect, choices=())
    add_on = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple,
                                       choices=())
    address_id = forms.ChoiceField(required=False, choices=())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        rows = self.options.get('rows', ())
        self.fields['item'].choices = [(row['value'], row['label']) for row in rows]
        self.fields['add_on'].choices = [(row['code'], row['name'])
                                         for row in self.options.get('add_ons', ())]
        addresses = self.options.get('addresses', ())
        self.fields['address_id'].choices = [(row['id'], row['label']) for row in addresses]
        self.fields['address_id'].required = bool(addresses)
        default = next((row['id'] for row in addresses if row['default']), None)
        if default and not self.is_bound:
            self.initial.setdefault('address_id', default)
        # One aligner count per Spark on Demand product; the service checks its range.
        for row in rows:
            if row['per_unit']:
                self.fields[f'quantity_{row["pk"]}'] = forms.CharField(required=False)

    @property
    def rows(self):
        return self.checked('item', [{**row, 'quantity': (self[f'quantity_{row["pk"]}'].value()
                                                          or '') if row['per_unit'] else ''}
                                     for row in self.options.get('rows', ())])

    @property
    def add_ons(self):
        return self.checked('add_on', self.options.get('add_ons', ()), key='code')

    @property
    def addresses(self):
        return self.checked('address_id', self.options.get('addresses', ()), key='id')
