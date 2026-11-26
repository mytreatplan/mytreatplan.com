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
JavaScript every step is a plain POST.

`WorkOrderWizard` (epic-ordering 9.9): the same steps for a manual work order clinical ops
opened, under `start-a-case/work-order/<work order id>/<step>/` (the home page's "Add to
cart"), with the patient and the product fixed: the maker for a branded TPS or an ICE, the
VirtuaOrtho variant for an ODB, then the options of that one product (its add-ons, a Spark
product's aligners, the shipping address). It ends in `service.add_work_order`; anything
`service.check_work_order` refuses (another doctor's, held by a transfer, already in the
cart...) sends the doctor home with the reason."""
from urllib.parse import quote

from django.contrib import messages
from django.http import Http404
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
from .forms import (BRAND, TPS, VIRTUAORTHO, IN_HOUSE, MakerForm, ProductForm, TpsOptionsForm,
                    VoOptionsForm, VoTypeForm)
from .common import doctor_only, fiscal_block, own_patient, refusal_texts
from .views import address_label, size_label

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
        self.patient = own_patient(request, kwargs['code'])
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

    def options_step(self):
        """The wizard's step behind the URL step "options"."""
        return {TPS: 'options_tps', VIRTUAORTHO: 'options_vo'}.get(
            self._answer('product', 'product'))

    def _route(self, step):
        """(the wizard's step for the URL's step, None), or (None, a redirect) when the URL
        is not a step of this doctor's path or an earlier step is missing."""
        if step == OPTIONS:
            step = self.options_step()
        steps = list(self.get_form_list())
        if step not in steps:
            return None, redirect(self.get_step_url(steps[0]))
        for earlier in steps[:steps.index(step)]:
            if self.get_cleaned_data_for_step(earlier) is None:
                return None, redirect(self.get_step_url(earlier))
        return step, None

    def _blocked(self):
        return render(self.request, 'cart/wizard/blocked.html', {
            'patient': self.patient, 'fiscal_link': fiscal_block(self.request, self.request.path)})

    def get(self, *args, **kwargs):
        if fiscal_block(self.request, self.request.path):
            return self._blocked()
        step, response = self._route(kwargs['step'])
        if response is not None:
            return response
        self.storage.current_step = step
        return self.render(self.get_form(data=self.storage.current_step_data), **kwargs)

    def post(self, *args, **kwargs):
        if fiscal_block(self.request, self.request.path):
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

    def step_number(self, step):
        return STEP_NUMBER[step]

    def first_back_url(self):
        return reverse('start_a_case')

    def get_template_names(self):
        return [f'cart/wizard/{self.steps.current}.html']

    def get_context_data(self, form, **kwargs):
        context = super().get_context_data(form=form, **kwargs)
        step = self.steps.current
        here = self.get_step_url(step)
        previous = self.get_prev_step(step)
        context.update({
            'patient': self.patient, 'step': step, 'step_number': self.step_number(step),
            'back_url': self.get_step_url(previous) if previous else self.first_back_url(),
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
            return self.render(forms[last], error=refusal_texts()[refused.code], **kwargs)
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


# --- A manual work order (9.9) ----------------------------------------------------------------

WORK_ORDER_URL_NAME = 'work_order_case_step'
_TPS_CATEGORIES = (Product.TPS_BRANDED, Product.TPS_IN_HOUSE, Product.ICE)


def _wo_is_tps(wizard):
    return wizard.product.category in _TPS_CATEGORIES


def _wo_is_vo(wizard):
    return wizard.product.category in service.VIRTUAORTHO_CATEGORIES


def _wo_needs_maker(wizard):
    """A branded TPS (a brand only) or an ICE (either branch) asks who makes the aligners."""
    return wizard.product.category in (Product.TPS_BRANDED, Product.ICE)


def _wo_needs_variant(wizard):
    """Only an ODB is bought under either variant (DigitalTwin adds ODB DigitalTwin)."""
    return wizard.product.category == Product.ODB


def work_order_tps_options(doctor, product):
    """The TPS options step of a manual work order: its one length (ICE for an ICE) with the
    doctor's price now, and the add-ons offered on its product."""
    money = next((money for offered, money in subscriptions.offers_for(doctor)
                  if offered.pk == product.pk), None)
    value = service.ICE_SIZE if product.category == Product.ICE else product.size
    add_ons = {}
    for add_on in resolver.add_ons(product):
        add_ons.setdefault(add_on.code, _add_on_entry(add_on))['offered'].add(value)
    return {'sizes': [{'value': value, 'label': size_label(value), 'price': money}],
            'add_ons': _finish_add_ons(add_ons)}


def work_order_vo_options(doctor, patient, product, variant):
    """The VirtuaOrtho options step of a manual work order: `vo_options` for the variant,
    kept to the row of its product and the add-ons that go with it."""
    options = vo_options(doctor, patient, variant)
    rows = [row for row in options['rows'] if str(row['pk']) == str(product.pk)]
    values = {row['value'] for row in rows}
    options['rows'] = rows
    options['add_ons'] = [entry for entry in options['add_ons']
                          if values & set(entry['offered'].split())]
    return options


class WorkOrderWizard(CaseWizard):
    form_list = [('maker', MakerForm), ('vo_type', VoTypeForm),
                 ('options_tps', TpsOptionsForm), ('options_vo', VoOptionsForm)]
    condition_dict = {'maker': _wo_needs_maker, 'options_tps': _wo_is_tps,
                      'vo_type': _wo_needs_variant, 'options_vo': _wo_is_vo}

    def dispatch(self, request, *args, **kwargs):
        # A staff account, another doctor's work order, one already paid or gone: a 404.
        doctor_only(request)
        self.work_order = patients_service.manual_work_order_of(
            request.user, kwargs['work_order_id'])
        if self.work_order is None:
            raise Http404
        self.patient = self.work_order.patient
        self.product = resolver.product(self.work_order.product_id)
        if self.product is None:
            raise Http404
        return NamedUrlSessionWizardView.dispatch(self, request, *args, **kwargs)

    def get_prefix(self, request, *args, **kwargs):
        return f'case_wo_{self.work_order.pk.hex}'

    def get_step_url(self, step):
        return reverse(WORK_ORDER_URL_NAME,
                       args=[self.work_order.pk, URL_STEP.get(step, step)])

    def options_step(self):
        return 'options_tps' if _wo_is_tps(self) else 'options_vo'

    def _answer(self, step, name):
        # A step this product skips (the maker of an in-house TPS...) has no answer.
        return super()._answer(step, name) if step in self.get_form_list() else None

    def _variant(self):
        try:
            return service.work_order_variant(self.request.user, self.product,
                                              self._answer('vo_type', 'variant') or '')
        except service.CartRefused:
            return ''

    def get_form_kwargs(self, step=None):
        doctor = self.request.user
        kwargs = {'doctor': doctor, 'patient': self.patient}
        if step == 'maker' and self.product.category == Product.TPS_BRANDED:
            kwargs['options'] = {'kinds': [BRAND]}
        elif step == 'vo_type':
            kwargs['options'] = {'cards': variant_cards(doctor)}
        elif step == 'options_tps':
            kwargs['options'] = work_order_tps_options(doctor, self.product)
        elif step == 'options_vo':
            kwargs['options'] = work_order_vo_options(doctor, self.patient, self.product,
                                                      self._variant())
        return kwargs

    def get_form_initial(self, step):
        """The one length or product of the work order is chosen already (and a branded
        TPS's one kind, brand-made)."""
        if step == 'maker' and self.product.category == Product.TPS_BRANDED:
            return {'kind': BRAND}
        if step == 'options_tps':
            return {'size': service.ICE_SIZE if self.product.category == Product.ICE
                    else self.product.size}
        if step == 'options_vo':
            rows = self.get_form_kwargs(step)['options']['rows']
            return {'item': rows[0]['value']} if len(rows) == 1 else {}
        return {}

    def _refused(self):
        """None when the work order may go into the cart (a missing fiscal entity is the
        steps' own block), else a redirect home with the reason."""
        try:
            service.check_work_order(self.request.user, self.work_order)
        except service.CartRefused as refused:
            if refused.code == service.NO_ENTITY:
                return None
            messages.error(self.request, refusal_texts()[refused.code])
            return redirect('home')
        return None

    def get(self, *args, **kwargs):
        return self._refused() or super().get(*args, **kwargs)

    def post(self, *args, **kwargs):
        return self._refused() or super().post(*args, **kwargs)

    def step_number(self, step):
        return list(self.get_form_list()).index(step) + 1

    def first_back_url(self):
        return reverse('home')

    def get_context_data(self, form, **kwargs):
        context = super().get_context_data(form=form, **kwargs)
        context.update({'step_total': len(self.get_form_list()),
                        'fixed_product': self.product.public_name})
        if self.steps.current == 'options_tps' \
                and self.product.category != Product.TPS_BRANDED:
            context['included'] = 0  # the notice counts branded TPS cases only
        if self.steps.current == 'options_vo':
            context['variant_label'] = VARIANT_LABELS.get(self._variant(), '')
        return context

    def add_to_cart(self, forms):
        options = {}
        if 'maker' in forms:
            maker = forms['maker'].cleaned_data
            options.update(maker=maker['maker'], other_name=maker['other_name'],
                           in_house=maker['kind'] == IN_HOUSE)
        if 'vo_type' in forms:
            options['variant'] = forms['vo_type'].cleaned_data['variant']
        if 'options_tps' in forms:
            options['add_on_codes'] = forms['options_tps'].cleaned_data['add_on']
        if 'options_vo' in forms:
            chosen = forms['options_vo'].cleaned_data
            choice, _sep, size = chosen['item'].partition(':')
            options.update(add_on_codes=chosen['add_on'], address_id=chosen['address_id'],
                           quantity=chosen.get(f'quantity_{size}', '')
                           if choice == service.SPARK else 1)
        return service.add_work_order(self.request.user, self.work_order, **options)
