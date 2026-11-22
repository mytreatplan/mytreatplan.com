"""The doctor's checkout (epic-payments entry 1, user decisions 2026-11-18).

- `checkout` (`/<lang>/checkout/<order id>/`): the order's lines and totals, the fiscal
  entity to bill (a choice among the usable ones; none: a link to add one that comes back
  here), the payment method, Pay and Cancel. Changing the entity re-prices the order; Pay
  with an entity that changed the total stops to show the new total first.
- `checkout_return`: where a gateway sends the doctor back: paid, waiting, or closed.
- `transfer_page`: the bank transfer's details while its attempt is open (epic-payments
  entry 5); the checkout and the return page lead there meanwhile.
- `checkout_failure`: a payment that did not go through: retry (the order is still
  `pending_payment`) or cancel.
- `fake_gateway`: the fake gateway's "hosted page" with Pay and Fail (only while
  PAYMENTS_FAKE_GATEWAY is on).
- `redsys_gateway`: the page that posts the signed form to Redsys's payment page (it
  submits itself; a button without JavaScript). `redsys_back`: where Redsys sends the
  doctor back (UrlOK / UrlKO, GET or POST, CSRF-exempt): 303 to the return or failure
  page; what Redsys posts is ignored (the payment is paid only by its signed
  notification).
- `test_order`: the hidden test-order page of dev and dev-ae (only while
  PAYMENTS_TEST_ORDERS is on): pick one product the doctor is offered and go through the
  real checkout.

Only the order's own doctor sees any of it: another doctor's id, a staff account or a flag
that is off is a 404. All writes go through `payments.service`; nothing here prices.
"""
import logging
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import get_language, gettext as _, gettext_lazy
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts import fiscal
from accounts.client_sync import is_client
from catalogue import pricing, resolver
from catalogue.models import Product
from patients import service as patients_service
from subscriptions import service as subscriptions_service

from . import bank_transfer, checks, fake, gateways, redsys, service, webhooks
from .models import Order

logger = logging.getLogger(__name__)

GATEWAY_LABELS = {
    'fake': gettext_lazy('Test payment (fake gateway)'),
    'redsys': gettext_lazy('Card'),
    'revolut': gettext_lazy('Revolut'),
    'bank_transfer': gettext_lazy('Bank transfer'),
}

REFUSALS = {
    pricing.NO_LINES: gettext_lazy('The order has nothing in it.'),
    pricing.COUNTRY_NOT_SERVED: gettext_lazy('We do not sell to your country yet.'),
    pricing.NO_BILLING_RULES: gettext_lazy('Orders cannot be billed yet.'),
    pricing.PRODUCT_NOT_OFFERED: gettext_lazy('This product is not offered to you.'),
    pricing.QUANTITY_REFUSED: gettext_lazy('This quantity cannot be ordered.'),
    pricing.ADD_ON_REFUSED: gettext_lazy('This option cannot be ordered with the product.'),
    pricing.TWO_COMPANIES: gettext_lazy(
        'These items are billed by different companies; order them separately.'),
    pricing.TWO_CURRENCIES: gettext_lazy(
        'These items are in different currencies; order them separately.'),
    pricing.ICE_TWICE: gettext_lazy('Only one item can take the ICE credit.'),
    pricing.ICE_NOT_PLANNING: gettext_lazy('The ICE credit applies to planning items only.'),
    pricing.ICE_CURRENCY: gettext_lazy('The ICE credit is in another currency.'),
    pricing.VOUCHER_NOT_USABLE: gettext_lazy('This voucher cannot be used.'),
    pricing.VOUCHER_TYPE: gettext_lazy('This voucher is not for this kind of order.'),
    pricing.VOUCHER_CURRENCY: gettext_lazy('This voucher is in another currency.'),
    pricing.VOUCHER_DOCTOR: gettext_lazy('This voucher is for another account.'),
    pricing.VOUCHER_USED: gettext_lazy('This voucher has already been used.'),
    pricing.VOUCHER_UNKNOWN: gettext_lazy('This voucher code is not valid.'),
    pricing.SUBSCRIPTION_NOT_OFFERED: gettext_lazy(
        'This subscription is not offered to you.'),
    pricing.PERIOD_NOT_OFFERED: gettext_lazy('This subscription is not sold for that period.'),
    pricing.PATIENT_REFUSED: gettext_lazy(
        'Choose one of your patients that has no case in progress.'),
}


def _refusal_message(refused):
    return str(REFUSALS.get(refused.reason, _('This order cannot be started.')))


def _doctor_order(request, pk):
    """The order `pk` of the signed-in doctor, else a 404 (another doctor's, a staff
    account's, or none)."""
    if not is_client(request.user):
        raise Http404('not a doctor')
    order = service.order_of(request.user, pk)
    if order is None:
        raise Http404('not your order')
    return order


def _actor(request):
    return service.doctor_actor(request.user)


def _version(request, order):
    try:
        return int(request.POST.get('version', ''))
    except ValueError:
        return -1  # never matches: stale


def _open_transfer(order):
    """The order's open bank transfer attempt whose 14 days have not ended, else None."""
    if order.state != Order.PENDING_PAYMENT:
        return None
    payment = service.open_payment(order)
    if (payment is None or payment.gateway != bank_transfer.NAME
            or (payment.session_expires_at is not None
                and payment.session_expires_at <= timezone.now())):
        return None
    return payment


def _gateway_choices(order):
    return [{'name': name, 'label': GATEWAY_LABELS.get(name, name)}
            for name in gateways.offered(order)]


def _go_to_gateway(request, order):
    """Redirect to what the order's gateway shows (a paid 0.00 order goes to the return)."""
    if order.state == Order.PAID or not order.gateway:
        return redirect('checkout_return', pk=order.pk)
    payment = service.open_payment(order)
    if payment is None:
        return redirect('checkout_return', pk=order.pk)
    try:
        return redirect(gateways.adapter(order.gateway).start(payment, request))
    except gateways.GatewayError as error:
        # The gateway could not open the payment (Revolut): no attempt is left live; the
        # failure page offers a retry.
        logger.warning('checkout: order %s did not start at %s (%s)', order.pk, order.gateway,
                       type(error).__name__)
        if payment.gateway_payment_id:
            # The gateway may still take this payment: the return page's re-read decides.
            return redirect('checkout_return', pk=order.pk)
        service.fail_payment(payment)
        _failed(request, error)
        return redirect('checkout_failure', pk=order.pk)


def _failed(request, error):
    """The message of a refusal of a write (nothing changed)."""
    if isinstance(error, service.EntityChanged):
        text = _('Your fiscal entity changed. Please review the order.')
    elif isinstance(error, service.StaleVersion):
        text = _('The order changed in the meantime. Please check it and try again.')
    elif isinstance(error, (service.NoEntity, service.EntityRefused)):
        text = _('Choose a fiscal entity you can bill to for this order.')
    elif isinstance(error, service.GatewayRefused):
        text = _('Choose one of the payment methods offered.')
    elif isinstance(error, service.NoPaymentMethod):
        text = _('There is no payment method for this order yet.')
    elif isinstance(error, service.AttemptOpen):
        text = _('A payment for this order is in progress. Please wait a few minutes, '
                 'or check your payment before trying again.')
    elif isinstance(error, service.InvalidTransition):
        text = _('This order can no longer be changed.')
    elif isinstance(error, service.HookFailed):
        text = _('Something went wrong. Please try again in a moment.')
    elif isinstance(error, pricing.OrderRefused):
        text = _refusal_message(error)
    else:
        text = _('Something went wrong. Please try again in a moment.')
    messages.error(request, text)


@login_required
@require_http_methods(['GET', 'POST'])
def checkout(request, pk):
    order = _doctor_order(request, pk)
    if request.method == 'POST':
        return _checkout_post(request, order)
    if order.state in (Order.PAID, Order.REFUNDED):
        return redirect('checkout_return', pk=order.pk)
    if request.GET.get('change') != '1' and _open_transfer(order) is not None:
        return redirect('checkout_transfer', pk=order.pk)
    entities = list(fiscal.usable_entities(request.user)) if order.state == Order.CHECKOUT \
        else []
    here = reverse('checkout', args=[order.pk])
    return render(request, 'payments/checkout.html', {
        'order': order, 'lines': list(order.lines.order_by('position')),
        'vat_groups': order.vat_groups,
        'entities': entities,
        'selected': order.fiscal_entity_id,
        'add_entity_url': f"{reverse('settings_fiscal_add')}?next={quote(here, safe='')}",
        'gateway_choices': _gateway_choices(order),
        'free': order.total <= 0,
    })


def _checkout_post(request, order):
    action = request.POST.get('action')
    actor, version = _actor(request), _version(request, order)
    here = redirect('checkout', pk=order.pk)
    try:
        if action == 'cancel':
            service.cancel(order, expected_version=version, actor=actor)
            messages.success(request, _('The order has been cancelled.'))
            return here
        if action not in ('entity', 'pay'):
            raise Http404('unknown action')
        if order.state == Order.CHECKOUT:
            chosen = request.POST.get('entity', '')
            if str(order.fiscal_entity_id) != chosen:
                order = service.choose_entity(order, chosen, expected_version=version,
                                              actor=actor)
                version = order.version
                if action == 'entity' or str(order.total) != request.POST.get('total'):
                    messages.info(request, _('The price was updated for this fiscal '
                                             'entity. Please review it.'))
                    return here
        if action == 'entity':
            return here
        order = service.start_payment(order, request.POST.get('gateway', ''),
                                      expected_version=version, actor=actor)
    except service.EntityChanged:
        try:
            service.choose_entity(order, order.fiscal_entity, expected_version=version,
                                  actor=actor)
        except (service.OrderError, pricing.OrderRefused, ValueError) as error:
            _failed(request, error)
            return here
        messages.info(request, _('Your fiscal entity changed. Please review the order.'))
        return here
    except (service.OrderError, pricing.OrderRefused, ValueError) as error:
        _failed(request, error)
        return here
    return _go_to_gateway(request, order)


@login_required
@require_http_methods(['GET'])
def checkout_return(request, pk):
    order = _doctor_order(request, pk)
    if _open_transfer(order) is not None:
        return redirect('checkout_transfer', pk=order.pk)
    waiting = False
    if order.state == Order.PENDING_PAYMENT:
        # The webhook may not have been handled yet: re-read the payment once, through
        # the same path as the webhook's task.
        payment = service.open_payment(order)
        if payment is not None and payment.gateway_payment_id:
            try:
                webhooks.reread_and_confirm(payment)
            except service.HookFailed:
                messages.error(request, _('Something went wrong. Please try again in a '
                                          'moment.'))
            except Exception as failure:  # noqa: BLE001 - the gateway cannot be read now: wait
                logger.warning('checkout return: order %s not re-read (%s)', order.pk,
                               type(failure).__name__)
            order = service.order_of(request.user, pk)
        waiting = order.state == Order.PENDING_PAYMENT and payment is not None
    return render(request, 'payments/return.html', {
        'order': order, 'waiting': waiting, 'needs_attention': service.needs_attention(order)})


@login_required
@never_cache
@require_http_methods(['GET'])
def transfer_page(request, pk):
    """The bank details of the order's open transfer attempt (only for the order's own
    doctor); any other state shows the return page."""
    order = _doctor_order(request, pk)
    payment = _open_transfer(order)
    if payment is None:
        return redirect('checkout_return', pk=order.pk)
    return render(request, 'payments/transfer.html', {
        'order': order, 'details': bank_transfer.details(order, payment)})


@login_required
@require_http_methods(['GET', 'POST'])
def checkout_failure(request, pk):
    order = _doctor_order(request, pk)
    if order.state == Order.PAID:
        return redirect('checkout_return', pk=order.pk)
    if request.method == 'POST':
        if order.state != Order.PENDING_PAYMENT and request.POST.get('action') != 'cancel':
            return redirect('checkout', pk=order.pk)  # the checkout reviews the total
        actor, version = _actor(request), _version(request, order)
        try:
            if request.POST.get('action') == 'cancel':
                service.cancel(order, expected_version=version, actor=actor)
                messages.success(request, _('The order has been cancelled.'))
                return redirect('checkout', pk=order.pk)
            order = service.start_payment(order, request.POST.get('gateway', ''),
                                          expected_version=version, actor=actor)
        except (service.OrderError, ValueError) as error:
            _failed(request, error)
            return redirect('checkout_failure', pk=order.pk)
        return _go_to_gateway(request, order)
    return render(request, 'payments/failure.html', {
        'order': order, 'gateway_choices': _gateway_choices(order)
        if order.state == Order.PENDING_PAYMENT else []})


@login_required
@require_http_methods(['GET', 'POST'])
def fake_gateway(request, pk):
    order = _doctor_order(request, pk)
    if (order.state != Order.PENDING_PAYMENT or order.gateway != gateways.FAKE
            or gateways.adapter(gateways.FAKE) is None):
        if order.state == Order.PAID:
            return redirect('checkout_return', pk=order.pk)
        raise Http404('no fake payment to make')
    payment = service.open_payment(order)
    if payment is None or not payment.gateway_payment_id:
        raise Http404('no fake payment to make')
    if request.method == 'POST':
        if request.POST.get('action') == 'pay':
            fake.pay(payment)  # a signed webhook through the real handler
            return redirect('checkout_return', pk=order.pk)
        fake.fail(payment)
        service.fail_payment(payment)
        return redirect('checkout_failure', pk=order.pk)
    return render(request, 'payments/fake_gateway.html', {'order': order})


@login_required
@never_cache
@require_http_methods(['GET'])
def redsys_gateway(request, pk):
    """The signed form for the order's open Redsys attempt, posted to Redsys by the page
    itself."""
    order = _doctor_order(request, pk)
    if order.state == Order.PAID:
        return redirect('checkout_return', pk=order.pk)
    if (order.state != Order.PENDING_PAYMENT or order.gateway != redsys.NAME
            or gateways.adapter(redsys.NAME) is None):
        raise Http404('no Redsys payment to make')
    payment = service.open_payment(order)
    if payment is None or payment.gateway != redsys.NAME or not payment.gateway_payment_id:
        raise Http404('no Redsys payment to make')
    if payment.session_expires_at is not None and payment.session_expires_at <= timezone.now():
        return redirect('checkout_failure', pk=order.pk)  # too late for this attempt
    try:
        fields = redsys.form(payment)
    except gateways.GatewayError as error:
        _failed(request, error)
        return redirect('checkout_failure', pk=order.pk)
    # Redsys's session starts when this form is posted: our clock moves with it.
    service.extend_session(payment, gateways.adapter(redsys.NAME).session_lifetime)
    return render(request, 'payments/redsys_form.html', {'order': order, 'form': fields})


@csrf_exempt
@never_cache
@require_http_methods(['GET', 'POST'])
def redsys_back(request, pk, outcome):
    """Redsys's UrlOK (`ok`) and UrlKO (`ko`): a 303 to our own GET page, which needs the
    doctor's session (a cross-site POST carries no Lax cookie; the GET after the 303
    does). Nothing posted is read."""
    if outcome not in ('ok', 'ko'):
        raise Http404('no such outcome')
    target = reverse('checkout_return' if outcome == 'ok' else 'checkout_failure', args=[pk])
    response = HttpResponseRedirect(target)
    response.status_code = 303
    return response


@login_required
@require_http_methods(['GET', 'POST'])
def test_order(request):
    if (not getattr(settings, 'PAYMENTS_TEST_ORDERS', False) or checks.public_site()
            or not is_client(request.user)):
        raise Http404('test orders are off')
    offers = subscriptions_service.offers_for(request.user)
    if request.method == 'POST':
        chosen = next((product for product, _price in offers
                       if str(product.pk) == request.POST.get('product')), None)
        if chosen is None:
            messages.error(request, _('Choose one of the products offered.'))
            return redirect('checkout_test_order')
        patient_code = (request.POST.get('patient', '')
                        if chosen.category == Product.ICE else '')
        try:
            order = service.create_order(
                request.user, [resolver.OrderLine(chosen, patient_code=patient_code)],
                actor=_actor(request))
        except (pricing.OrderRefused, service.OrderError) as error:
            _failed(request, error)
            return redirect('checkout_test_order')
        return redirect('checkout', pk=order.pk)
    language = get_language()
    return render(request, 'payments/test_order.html', {
        'offers': [{'id': product.pk, 'name': product.name_in(language), 'price': price,
                    'ice': product.category == Product.ICE}
                   for product, price in offers],
        # An ICE is a case for one of the doctor's patients (epic-ordering 9.1; the real
        # wizard is 9.3).
        'patients': [{'code': patient.code, 'label': patient.display_name}
                     for patient in patients_service.patients_of(request.user).filter(
                         status='active')]})
