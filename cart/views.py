"""The case wizard and the cart page (epic-ordering 9.3). Doctor-only (a staff account is a
404); a patient is chosen by code, and no patient name or maker credential is put in a URL,
a log line or a message. Step 1 picks an active patient or creates one (the 9.2 dialog,
`patients.views._create`); step 2 picks the maker or in-house, the size and the add-ons the
catalogue offers; the result is a `CartItem` (`cart.service`). Plain POSTs work without
JavaScript."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from urllib.parse import quote
from django.utils import translation
from django.utils.translation import gettext as _
from django.utils.translation import pgettext, pgettext_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods, require_POST

from accounts import fiscal, makers
from accounts.client_sync import is_client
from catalogue import resolver
from catalogue.models import Product
from patients import service as patients_service
from patients.forms import PatientForm
from patients.models import Patient
from patients.views import _create
from subscriptions import service as subscriptions

from . import service
from .models import OTHER, UNDECIDED, CartItem


def _refusal_texts():
    return {
        service.NOT_A_DOCTOR: _('Only doctors can order.'),
        service.NO_PATIENT: _('This patient cannot be chosen.'),
        service.NO_ENTITY: _('Add a fiscal entity before you order.'),
        service.MAKER_UNKNOWN: _('Choose a maker, "Not decided yet" or "Other".'),
        service.MAKER_LOGIN: _('Save your login for this maker first.'),
        service.OTHER_NAME: _('Enter the name of the maker (at most 60 characters).'),
        service.LIVE_WORK_ORDER: _('This patient already has a case in progress.'),
        service.IN_CART: _('This patient already has a service in your cart.'),
        service.NOT_OFFERED: _('This service is not available in that size.'),
        service.ADD_ON: _('One of the options chosen is not available for this service.'),
    }


SIZE_LABELS = {
    'ice': pgettext_lazy('TPS size', 'ICE'),
    'short': pgettext_lazy('TPS size', 'Short'),
    'medium': pgettext_lazy('TPS size', 'Medium'),
    'long': pgettext_lazy('TPS size', 'Long'),
}


def size_label(size):
    """The size's name in the current language; an unknown size shows as its code."""
    return str(SIZE_LABELS.get(size, size))


def _doctor_only(request):
    if not is_client(request.user) or request.user.is_erased:
        raise Http404('not a doctor')


def _own_patient(request, code):
    _doctor_only(request)
    patient = patients_service.patient_of(request.user, code)
    if patient is None:
        raise Http404
    return patient


def _fiscal_block(request, here):
    """The link of the fiscal-entity block (the wizard page to come back to), or None when
    the doctor has a usable entity."""
    if fiscal.has_usable_entity(request.user):
        return None
    return reverse('settings_fiscal_add') + '?next=' + quote(here, safe='')


def _step_one(request, form, open_dialog):
    in_cart = set(CartItem.objects.filter(doctor_id=request.user.pk)
                  .values_list('patient__code', flat=True))
    patients = patients_service.patients_of(request.user).filter(status=Patient.ACTIVE)
    return render(request, 'cart/start.html', {
        'patients': [(patient, patient.code in in_cart) for patient in patients],
        'form': form, 'open_dialog': open_dialog,
        'fiscal_link': _fiscal_block(request, reverse('start_a_case'))})


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'HEAD'])
def start_a_case(request):
    """Step 1: the doctor's active patients (each leads to step 2) and the add-patient
    dialog."""
    _doctor_only(request)
    return _step_one(request, PatientForm(), False)


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'HEAD', 'POST'])
def start_a_case_patient(request):
    """The wizard's add-patient form: its dialog posts here. A saved patient goes straight to
    step 2; an invalid post shows step 1 again with the dialog open. GET is the page without
    JavaScript."""
    _doctor_only(request)
    if request.method != 'POST':
        return render(request, 'cart/new_patient.html', {'form': PatientForm()})
    form = PatientForm(request.POST)
    patient = _create(request, form)
    if patient is not None:
        return redirect(reverse('start_a_case_tps', args=[patient.code]))
    return _step_one(request, form, True)


def _options(doctor):
    """What step 2 offers: [{'size', 'prices': {category: Money}}] by size order, and the
    add-ons [{'code', 'name', 'description', 'offered': 'branded:medium ...'}]."""
    sizes, add_ons = {}, {}
    for product, money in service.offered_tps(doctor):
        key = 'branded' if product.category == Product.TPS_BRANDED else 'in_house'
        sizes.setdefault(product.size, {})[key] = money
        for add_on in resolver.add_ons(product):
            entry = add_ons.setdefault(add_on.code, {
                'code': add_on.code, 'name': add_on.public_name,
                'description': add_on.description_in(translation.get_language()), 'offered': set()})
            entry['offered'].add(f'{key}:{product.size}')
    return ([{'size': size, 'label': size_label(size), 'branded': prices.get('branded'),
              'in_house': prices.get('in_house')}
             for size, prices in sorted(sizes.items(), key=lambda pair: service.size_rank(pair[0]))],
            [{**entry, 'offered': ' '.join(sorted(entry['offered']))}
             for entry in sorted(add_ons.values(), key=lambda entry: entry['code'])])


def _step_two_context(request, patient, posted=None, error=''):
    here = request.path
    held = subscriptions.effective(request.user).subscription
    sizes, add_ons = _options(request.user)
    maker_rows = [{'code': code, 'name': name, 'has_login': makers.has_login(request.user, code),
                   'save_url': reverse('settings_maker_save', args=[code]) + '?next=' + quote(here, safe='')}
                  for code, name in makers.MAKER_NAMES.items()]
    included = resolver.included_cases(held, Product.TPS_BRANDED) if held else 0
    return {'patient': patient, 'sizes': sizes, 'add_ons': add_ons, 'makers': maker_rows,
            'included': included, 'fiscal_link': _fiscal_block(request, here),
            'posted': posted or {}, 'error': error, 'undecided': UNDECIDED, 'other': OTHER}


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'HEAD', 'POST'])
def start_a_case_tps(request, code):
    """Step 2: the maker (or in-house), the size and the add-ons for one patient; a valid
    POST puts the item in the cart."""
    patient = _own_patient(request, code)
    posted, error = {}, ''
    if request.method == 'POST':
        data = request.POST
        posted = {'kind': data.get('kind', ''), 'maker': data.get('maker', ''),
                  'other_name': data.get('other_name', ''), 'size': data.get('size', ''),
                  'add_ons': data.getlist('add_on')}
        try:
            service.add_tps(
                request.user, patient, maker=posted['maker'], other_name=posted['other_name'],
                in_house=posted['kind'] == 'in_house', size=posted['size'],
                add_on_codes=posted['add_ons'])
        except service.CartRefused as refused:
            error = _refusal_texts()[refused.code]
        else:
            messages.success(request, _('The service has been added to your cart.'))
            return redirect('cart')
    return render(request, 'cart/choose.html', _step_two_context(request, patient, posted, error))


def _maker_label(item):
    if item.maker == '':
        return _('In-house printing')
    if item.maker == UNDECIDED:
        return _('Not decided yet')
    if item.maker == OTHER:
        return item.other_name
    return makers.MAKER_NAMES[item.maker]


def _line(item, held):
    product = resolver.product(item.product_id)
    add_ons = [resolver.add_on(code) for code in item.add_on_codes]
    price = None
    if product is not None and held is not None:
        try:
            price = resolver.price(product, held, [add_on for add_on in add_ons if add_on])
        except ValueError:
            price = None
    return {'item': item, 'patient': item.patient,
            'product': product.public_name if product else '',
            'size': size_label(product.size) if product else '',
            'add_ons': [add_on.public_name if add_on else code
                        for add_on, code in zip(add_ons, item.add_on_codes)],
            'maker': _maker_label(item), 'price': price}


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'HEAD'])
def cart(request):
    """The cart: the items by patient, each with Remove."""
    _doctor_only(request)
    held = subscriptions.effective(request.user).subscription
    return render(request, 'cart/cart.html', {
        'lines': [_line(item, held) for item in service.items_of(request.user)]})


@login_required
@csrf_protect
@never_cache
@require_POST
def cart_remove(request, item_id):
    _doctor_only(request)
    if not service.remove(request.user, item_id):
        raise Http404
    messages.success(request, _('The service has been removed from your cart.'))
    return redirect('cart')
