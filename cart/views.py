"""The case wizard and the cart page (epic-ordering 9.3). Doctor-only (a staff account is a
404); a patient is chosen by code, and no patient name or maker credential is put in a URL,
a log line or a message. Step 1 picks an active patient or creates one (the 9.2 dialog,
`patients.views.create_patient_from_form`); the steps after it (product, maker or
VirtuaOrtho type, options) are the formtools wizard in `cart.wizard` (9.16); the result is a
`CartItem` (`cart.service`). Plain POSTs work without JavaScript."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import pgettext_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods, require_POST

from accounts import makers
from catalogue import pricing, resolver
from catalogue.models import Product
from patients import service as patients_service
from patients.forms import PatientForm
from patients.models import Patient
from patients.views import create_patient_from_form
from payments import service as payments
from payments.views import refusal_message
from subscriptions import service as subscriptions

from . import service
from .common import doctor_only, fiscal_block, own_patient, refusal_texts
from .models import OTHER, UNDECIDED, CartItem


SIZE_LABELS = {
    'ice': pgettext_lazy('TPS size', 'ICE'),
    'short': pgettext_lazy('TPS size', 'Short'),
    'medium': pgettext_lazy('TPS size', 'Medium'),
    'long': pgettext_lazy('TPS size', 'Long'),
}


def size_label(size):
    """The size's name in the current language; an unknown size shows as its code."""
    return str(SIZE_LABELS.get(size, size))


def address_label(address):
    """How an address reads in the wizard and the cart: its label or recipient, and its
    city; the doctor's own, never in a URL or a log."""
    return ' · '.join(part for part in (address.display_name, address.city) if part) \
        or _('Shipping address')


def _step_one(request, form, open_dialog):
    in_cart = set(CartItem.objects.filter(doctor_id=request.user.pk)
                  .values_list('patient__code', flat=True))
    patients = patients_service.patients_of(request.user).filter(status=Patient.ACTIVE)
    return render(request, 'cart/start.html', {
        'rows': [{'patient': patient, 'search': patients_service.search_text(patient),
                  'in_cart': patient.code in in_cart} for patient in patients],
        'form': form, 'open_dialog': open_dialog,
        'fiscal_link': fiscal_block(request, reverse('start_a_case'))})


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'HEAD'])
def start_a_case(request):
    """Step 1: the doctor's active patients (each leads to the wizard) and the add-patient
    dialog."""
    doctor_only(request)
    return _step_one(request, PatientForm(), False)


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'HEAD', 'POST'])
def start_a_case_patient(request):
    """The wizard's add-patient form: its dialog posts here. A saved patient goes straight to
    the wizard; an invalid post shows step 1 again with the dialog open. GET is the page without
    JavaScript."""
    doctor_only(request)
    if request.method != 'POST':
        return render(request, 'patients/new.html', {
            'form': PatientForm(), 'action': reverse('start_a_case_patient'),
            'back_url': reverse('start_a_case'), 'back_text': _('Back to the patients')})
    form = PatientForm(request.POST)
    patient = create_patient_from_form(request, form)
    if patient is not None:
        return redirect(reverse('start_a_case_step', args=[patient.code, 'product']))
    return _step_one(request, form, True)


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'HEAD'])
def start_a_case_tps(request, code):
    """The old step-2 address (`start-a-case/<code>/`, 9.3): the wizard's first step now
    (cart.wizard, 9.16)."""
    patient = own_patient(request, code)
    return redirect(reverse('start_a_case_step', args=[patient.code, 'product']))


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
            price = resolver.price(product, held, [add_on for add_on in add_ons if add_on],
                                   item.quantity)
        except ValueError:
            price = None
    # MyTreatPlan makes a VirtuaOrtho item: no maker to show.
    virtuaortho = product is not None and product.category in service.VIRTUAORTHO_CATEGORIES
    return {'item': item, 'patient': item.patient,
            'product': product.public_name if product else '',
            'size': (size_label(service.ICE_SIZE) if product.category == Product.ICE
                     else size_label(product.size)) if product else '',
            'add_ons': [add_on.public_name if add_on else code
                        for add_on, code in zip(add_ons, item.add_on_codes)],
            'maker': '' if virtuaortho else _maker_label(item),
            'aligners': item.quantity if product is not None and product.per_unit else None,
            'address': address_label(item.shipping_address) if item.shipping_address_id else '',
            'price': price}


def _cart_page(request, error='', voucher_code=''):
    held = subscriptions.effective(request.user).subscription
    return render(request, 'cart/cart.html', {
        'lines': [_line(item, held) for item in service.items_of(request.user)],
        'error': error, 'voucher_code': voucher_code,
        'fiscal_link': fiscal_block(request, reverse('cart'))})


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'HEAD'])
def cart(request):
    """The cart: the items by patient, each with Remove, a try-me voucher field and
    "Finish order" (9.6)."""
    doctor_only(request)
    return _cart_page(request)


def _finish_error(refused):
    """The text of a refused "Finish order" (never a patient's name or code)."""
    if isinstance(refused, service.CartRefused):
        return refusal_texts()[refused.code]
    if isinstance(refused, payments.NoPaymentMethod):
        return _('There is no payment method for this order yet.')
    if isinstance(refused, payments.OrderError):
        return _('Something went wrong. Please try again in a moment.')
    if refused.reason == pricing.PATIENT_REFUSED:
        return _('A patient in your cart now has a case in progress. Remove that item and '
                 'try again.')
    return refusal_message(refused)


@login_required
@csrf_protect
@never_cache
@require_POST
def cart_finish(request):
    """Finish the order: the cart becomes one service order (with the optional voucher
    code) and the doctor goes to its checkout; a refusal shows the cart again with the
    reason."""
    doctor_only(request)
    voucher_code = (request.POST.get('voucher') or '').strip()[:40]
    try:
        order = service.finish(request.user, voucher_code)
    except (service.CartRefused, pricing.OrderRefused, payments.OrderError) as refused:
        return _cart_page(request, _finish_error(refused), voucher_code)
    return redirect('checkout', pk=order.pk)


@login_required
@csrf_protect
@never_cache
@require_POST
def cart_remove(request, item_id):
    doctor_only(request)
    if not service.remove(request.user, item_id):
        raise Http404
    messages.success(request, _('The service has been removed from your cart.'))
    return redirect('cart')
