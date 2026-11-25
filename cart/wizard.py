"""The case wizard from step 2 on (epic-ordering 9.16, replaces 9.3/9.4's one long page): a
django-formtools `NamedUrlSessionWizardView`, one URL per step under
`start-a-case/<patient code>/<step>/`:

    product -> maker (TPS) or vo_type (VirtuaOrtho) -> options -> "Add to cart"

Step 1 (the patient) stays `cart.views.start_a_case`. Every business rule is in
`cart.service`; this module only builds what each step offers (from the service's offer
helpers and the catalogue resolver) and calls `service.add_tps` / `service.add_virtuaortho`
once, from the last step. The session holds the patient's code (in its key) and the choices
(each step's own fields, never the CSRF token, a name or a maker credential). A refusal of the
service shows the options step again with its message and keeps the choices. Without
JavaScript every step is a plain POST."""
from urllib.parse import quote

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from formtools.wizard.views import NamedUrlSessionWizardView

from accounts import makers, shipping
from catalogue import resolver
from catalogue.models import Product
from patients import service as patients_service
from subscriptions import service as subscriptions

from . import service
from .forms import (TPS, VIRTUAORTHO, IN_HOUSE, MakerForm, ProductForm, TpsOptionsForm,
                    VoOptionsForm, VoTypeForm)
from .views import (_fiscal_block, _own_patient, _refusal_texts, address_label, size_label)

URL_NAME = 'start_a_case_step'
# The two options forms share the URL step "options"; the product decides which one it is.
OPTIONS = 'options'
URL_STEP = {'options_tps': OPTIONS, 'options_vo': OPTIONS}
STEP_NUMBER = {'product': 1, 'maker': 2, 'vo_type': 2, 'options_tps': 3, 'options_vo': 3}
VARIANT_LABELS = {service.ONE: gettext_lazy('VirtuaOrtho One'),
                  service.DIGITALTWIN: gettext_lazy('VirtuaOrtho DigitalTwin')}


def _add_on_entry(add_on):
    return {'code': add_on.code, 'name': add_on.public_name,
            'description': add_on.description_in(translation.get_language()), 'offered': set()}


def _finish_add_ons(add_ons):
    return [{**entry, 'offered': ' '.join(sorted(entry['offered']))}
            for entry in sorted(add_ons.values(), key=lambda entry: entry['code'])]


def product_cards(doctor):
    """The two cards of the first step; a product the doctor is not offered is unavailable
    (shown greyed out, not a choice)."""
    tps = bool(service.offered_tps(doctor) or service.offered_ice(doctor))
    vo = bool(service.variants_offered(service.offered_virtuaortho(doctor)))
    return [
        {'value': TPS, 'title': _('Treatment Planning'), 'available': tps,
         'text': _('A treatment plan for one patient: choose who makes the aligners and the '
                   'length of the plan.')},
        {'value': VIRTUAORTHO, 'title': _('VirtuaOrtho'), 'available': vo,
         'text': _('MyTreatPlan makes and ships the aligners, with or without patient '
                   'monitoring.')}]


def variant_cards(doctor):
    variants = service.variants_offered(service.offered_virtuaortho(doctor))
    text = {service.ONE: _('Without patient monitoring'),
            service.DIGITALTWIN: _('With patient monitoring')}
    return [{'value': variant, 'title': VARIANT_LABELS[variant], 'text': text[variant]}
            for variant in variants]


def tps_options(doctor, in_house):
    """What the TPS options step offers for printing in-house or a maker's: the lengths with
    their price ([{'value', 'label', 'price'}], ICE first) and the add-ons the service would
    accept on the product each length picks ([{'code', 'name', 'description', 'offered'}],
    'offered' the lengths it goes with)."""
    sizes, add_ons = [], {}

    def note_add_ons(product, size):
        for add_on in resolver.add_ons(product):
            add_ons.setdefault(add_on.code, _add_on_entry(add_on))['offered'].add(size)

    ice = service.offered_ice(doctor)
    if ice:
        sizes.append({'value': service.ICE_SIZE, 'label': size_label(service.ICE_SIZE),
                      'price': ice[1]})
        note_add_ons(ice[0], service.ICE_SIZE)
    category = service.category_of(in_house)
    found = {}
    for product, money in service.offered_tps(doctor):
        if product.category == category and product.size not in found:
            # The first by code, as `service.add_tps` picks it.
            found[product.size] = (product, money)
    for size, (product, money) in sorted(found.items(),
                                         key=lambda pair: service.size_rank(pair[0])):
        sizes.append({'value': size, 'label': size_label(size), 'price': money})
        note_add_ons(product, size)
    return {'sizes': sizes, 'add_ons': _finish_add_ons(add_ons)}


GROUP_LABELS = {service.CASE: lambda: _('Length'), service.ODB: lambda: _('ODB'),
                service.SPARK: lambda: _('Spark on Demand'),
                service.RETAINERS: lambda: _('Retainers')}


def vo_options(doctor, patient, variant):
    """What the VirtuaOrtho options step offers for `variant`: {'rows': [{'value', 'pk',
    'group', 'group_label', 'label', 'price', 'per_unit', 'min', 'max', 'auto_odb_dt'}],
    'add_ons': [...], 'addresses': [{'id', 'label', 'default'}]}. The rows are the service's
    `virtuaortho_lines`; retainers only after a finished treatment; ODB DigitalTwin is added by
    the service under DigitalTwin and never a choice."""
    offered = service.offered_virtuaortho(doctor)
    held = subscriptions.effective(doctor).subscription
    finished = patients_service.has_finished_treatment(patient)
    rows, add_ons = [], {}
    for line_variant, choice, product, price in service.virtuaortho_lines(doctor, offered, held):
        if line_variant != variant or (choice == service.RETAINERS and not finished):
            continue
        value = {service.CASE: f'case:{product.size}',
                 service.RETAINERS: 'retainers'}.get(choice, f'{choice}:{product.pk}')
        rows.append({'value': value, 'pk': product.pk, 'group': choice,
                     'group_label': GROUP_LABELS[choice](), 'rank': service.size_rank(
                         product.size), 'label': size_label(product.size)
                     if choice == service.CASE else product.public_name,
                     'price': price, 'per_unit': choice == service.SPARK,
                     'min': product.min_quantity, 'max': product.max_quantity,
                     'auto_odb_dt': choice == service.ODB and variant == service.DIGITALTWIN})
        for add_on in resolver.add_ons(product):
            if add_on.code != service.ODB_DT:
                add_ons.setdefault(add_on.code, _add_on_entry(add_on))['offered'].add(value)
    order = {service.CASE: 0, service.ODB: 1, service.SPARK: 2, service.RETAINERS: 3}
    rows.sort(key=lambda row: (order[row['group']], row['rank'], row['label']))
    return {'rows': rows, 'add_ons': _finish_add_ons(add_ons),
            'addresses': [{'id': str(address.pk), 'label': address_label(address),
                           'default': address.is_default}
                          for address in shipping.usable_addresses(doctor)]}


def _is_tps(wizard):
    return (wizard.get_cleaned_data_for_step('product') or {}).get('product') == TPS


def _is_vo(wizard):
    return (wizard.get_cleaned_data_for_step('product') or {}).get('product') == VIRTUAORTHO


class CaseWizard(NamedUrlSessionWizardView):
    form_list = [('product', ProductForm), ('maker', MakerForm), ('vo_type', VoTypeForm),
                 ('options_tps', TpsOptionsForm), ('options_vo', VoOptionsForm)]
    condition_dict = {'maker': _is_tps, 'options_tps': _is_tps,
                      'vo_type': _is_vo, 'options_vo': _is_vo}

    # --- Who and which patient --------------------------------------------------------

    def dispatch(self, request, *args, **kwargs):
        # A staff account, another doctor's patient or an inactive one: a 404.
        self.patient = _own_patient(request, kwargs['code'])
        return super().dispatch(request, *args, **kwargs)

    def get_prefix(self, request, *args, **kwargs):
        """One wizard (session entry, management form) per patient."""
        return f'case_{self.patient.code}'

    def get_form_prefix(self, step=None, form=None):
        return ''  # plain field names: each step is its own POST

    def get_step_url(self, step):
        return reverse(URL_NAME, args=[self.patient.code, URL_STEP.get(step, step)])

    # --- What a step offers -----------------------------------------------------------

    def _answer(self, step, name):
        return (self.get_cleaned_data_for_step(step) or {}).get(name)

    def get_form_kwargs(self, step=None):
        doctor, kwargs = self.request.user, {'doctor': self.request.user, 'patient': self.patient}
        if step == 'product':
            kwargs['options'] = {'cards': product_cards(doctor)}
        elif step == 'vo_type':
            kwargs['options'] = {'cards': variant_cards(doctor)}
        elif step == 'options_tps':
            kwargs['options'] = tps_options(doctor, self._answer('maker', 'kind') == IN_HOUSE)
        elif step == 'options_vo':
            kwargs['options'] = vo_options(doctor, self.patient, self._answer('vo_type', 'variant'))
        return kwargs

    def get_form_step_data(self, form):
        """Only the step's own fields are kept (never the CSRF token or the management form)."""
        return {name: form.data.getlist(name) for name in form.fields if name in form.data}

    # --- Routing ----------------------------------------------------------------------

    def _route(self, step):
        """(the wizard's step for the URL's step, None), or (None, a redirect) when the URL
        is not a step of this doctor's path or an earlier step is missing."""
        if step == OPTIONS:
            step = {TPS: 'options_tps', VIRTUAORTHO: 'options_vo'}.get(
                self._answer('product', 'product'))
        steps = list(self.get_form_list())
        if step not in steps:
            return None, redirect(self.get_step_url(steps[0]))
        for earlier in steps[:steps.index(step)]:
            if self.get_cleaned_data_for_step(earlier) is None:
                return None, redirect(self.get_step_url(earlier))
        return step, None

    def _blocked(self):
        return render(self.request, 'cart/wizard/blocked.html', {
            'patient': self.patient, 'fiscal_link': _fiscal_block(self.request, self.request.path)})

    def get(self, *args, **kwargs):
        if _fiscal_block(self.request, self.request.path):
            return self._blocked()
        step, response = self._route(kwargs['step'])
        if response is not None:
            return response
        self.storage.current_step = step
        return self.render(self.get_form(data=self.storage.current_step_data), **kwargs)

    def post(self, *args, **kwargs):
        if _fiscal_block(self.request, self.request.path):
            return self._blocked()
        step, response = self._route(kwargs['step'])
        if response is not None:
            return response
        # A stale tab or a forged value never overrides the routed step: nothing is processed.
        if self.request.POST.get(f'{self.prefix}-current_step') != step:
            return redirect(self.get_step_url(step))
        self.storage.current_step = step
        return super().post(*args, **kwargs)

    # --- Rendering --------------------------------------------------------------------

    def get_template_names(self):
        return [f'cart/wizard/{self.steps.current}.html']

    def get_context_data(self, form, **kwargs):
        context = super().get_context_data(form=form, **kwargs)
        step = self.steps.current
        here = reverse(URL_NAME, args=[self.patient.code, URL_STEP.get(step, step)])
        previous = self.get_prev_step(step)
        context.update({
            'patient': self.patient, 'step': step, 'step_number': STEP_NUMBER[step],
            'back_url': self.get_step_url(previous) if previous else reverse('start_a_case'),
            'error': kwargs.get('error', ''), 'last': step == self.steps.last})
        if step == 'product':
            context['upgrade_url'] = reverse('upgrade')
        elif step == 'maker':
            context['maker_rows'] = [
                {'code': code, 'name': name, 'has_login': makers.has_login(self.request.user, code),
                 'save_url': reverse('settings_maker_save', args=[code]) + '?next='
                 + quote(here, safe='')} for code, name in makers.MAKER_NAMES.items()]
        elif step == 'options_tps':
            held = subscriptions.effective(self.request.user).subscription
            included = resolver.included_cases(held, Product.TPS_BRANDED) if held else 0
            context['included'] = included if self._answer('maker', 'kind') != IN_HOUSE else 0
        elif step == 'options_vo':
            context['shipping_link'] = (
                None if shipping.has_usable_address(self.request.user) else
                reverse('settings_shipping_add') + '?next=' + quote(here, safe=''))
            context['variant_label'] = VARIANT_LABELS.get(self._answer('vo_type', 'variant'), '')
        return context

    # --- The end ----------------------------------------------------------------------

    def render_done(self, form, **kwargs):
        """Re-checks every step, then adds the item. The service's refusal shows the last
        step again with its message and keeps the choices (the wizard is reset only when the
        item is in the cart)."""
        forms = {}
        for step in self.get_form_list():
            checked = self.get_form(step=step, data=self.storage.get_step_data(step))
            if not checked.is_valid():
                return self.render_revalidation_failure(step, checked, **kwargs)
            forms[step] = checked
        last = self.steps.last
        try:
            self.add_to_cart(forms)
        except service.CartRefused as refused:
            self.storage.current_step = last
            return self.render(forms[last], error=_refusal_texts()[refused.code], **kwargs)
        self.storage.reset()
        messages.success(self.request, _('The service has been added to your cart.'))
        return redirect('cart')

    def add_to_cart(self, forms):
        doctor = self.request.user
        if 'options_tps' in forms:
            maker, options = forms['maker'].cleaned_data, forms['options_tps'].cleaned_data
            return service.add_tps(
                doctor, self.patient, maker=maker['maker'], other_name=maker['other_name'],
                in_house=maker['kind'] == IN_HOUSE, size=options['size'],
                add_on_codes=options['add_on'])
        variant = forms['vo_type'].cleaned_data['variant']
        options = forms['options_vo'].cleaned_data
        choice, _sep, size = options['item'].partition(':')
        return service.add_virtuaortho(
            doctor, self.patient, variant=variant, choice=choice, size=size,
            add_on_codes=options['add_on'],
            quantity=options.get(f'quantity_{size}', '') if choice == service.SPARK else 1,
            address_id=options['address_id'])
