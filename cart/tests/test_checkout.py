"""Cart to order (epic-ordering 9.6): one test per row of the plan's matrix, on the service,
the pages and the payment hooks. The admin site is the fake of
integration/tests/snapshot_rows.py (no network)."""
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from accounts import fiscal, shipping
from cart import service
from cart.models import CartItem
from catalogue import pricing
from catalogue.models import BillingCompany
from integration.tests.snapshot_rows import IRELAND, pull, snapshot_writes, voucher
from patients import service as patients_service
from patients.models import WorkOrder
from patients.tests.conftest import (BRANDED, ICE, SPARK_ONE, VO_TWIN, virtuaortho_catalogue)
from payments import service as payments
from payments.models import Order, Payment
from subscriptions.models import VoucherRedemption

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]
CART, FINISH = '/en/cart/', '/en/cart/finish/'
TRY_ME = 'f0000001-0000-4000-8000-000000000001'
BIG = 'f0000004-0000-4000-8000-000000000004'
USD = 'f0000005-0000-4000-8000-000000000005'
CODE, BIG_CODE, USD_CODE = 'TRYME23456', 'BIGME23456', 'USDME23456'
ADDRESS = {'label': 'Clínica Madrid', 'recipient_name': 'Rosa Vidal',
           'clinic_name': 'Clínica Vidal', 'contact_phone': '612 34 56 78',
           'address_line1': 'Calle Mayor 1', 'city': 'Madrid', 'postal_code': '28001',
           'province': 'Madrid', 'country': 'ES'}


def browser(user):
    client = Client()
    client.force_login(user)
    return client


def actor(user):
    return payments.doctor_actor(user)


def load(admin, mutate=None, version=3):
    section = virtuaortho_catalogue()
    if mutate:
        mutate(section)
    assert pull(admin, version, section, vouchers=[
        voucher(TRY_ME, CODE), voucher(BIG, BIG_CODE, amount='5000.00'),
        voucher(USD, USD_CODE, currency='USD')]) == ('applied', version)


@pytest.fixture
def ready(advanced, entity, patient, admin, fake_on):
    """The Advanced doctor with a fiscal entity, an address, two patients and the vouchers."""
    load(admin)
    advanced.address = shipping.create(advanced, ADDRESS)
    advanced.second = patients_service.create_patient(
        advanced, first_name='Luis', surname='Gil', actor=patients_service.Actor(
            'doctor', advanced.uid))
    return advanced


def tps(doctor, patient, size='medium'):
    return service.add_tps(doctor, patient, maker='undecided', size=size)


def twin(doctor, patient):
    return service.add_virtuaortho(doctor, patient, variant='digitaltwin', choice='case',
                                   size='medium', address_id=str(doctor.address.pk))


def finish(doctor, code=''):
    return service.finish(doctor, code)


def refused(doctor, code=''):
    with pytest.raises(service.CartRefused) as caught:
        finish(doctor, code)
    assert not Order.objects.exists()
    return caught.value.code


def pay(order, user):
    order = payments.start_payment(order, 'fake', expected_version=order.version,
                                   actor=actor(user))
    return payments.mark_paid(order, expected_version=order.version,
                              actor=payments.SYSTEM_ACTOR)


def two_items(doctor, patient):
    tps(doctor, patient)
    twin(doctor, doctor.second)


BANK = {'bank_account_holder': 'MyTPDSO Spain S.L.', 'iban': 'ES9121000418450200051332',
        'bic': 'CAIXESBBXXX', 'bank_name': 'CaixaBank'}


@pytest.fixture
def bank(ready):
    with snapshot_writes():
        BillingCompany.objects.update(**BANK)
    return ready


def by_transfer(order, user):
    return payments.start_payment(order, 'bank_transfer', expected_version=order.version,
                                  actor=actor(user))


def third_patient(doctor):
    return patients_service.create_patient(
        doctor, first_name='Eva', surname='Ruiz', actor=patients_service.Actor(
            'doctor', doctor.uid))


# --- The matrix ---------------------------------------------------------------------------

def test_happy_two_patients_one_order_with_snapshots(ready, patient):
    two_items(ready, patient)
    order = finish(ready)
    first, second = order.lines.order_by('position')
    assert (order.state, order.kind, order.lines.count()) == ('checkout', 'service', 2)
    assert (first.patient_code, first.maker, first.shipping) == (
        patient.code, 'undecided', None)
    assert (second.patient_code, second.maker) == (ready.second.code, '')
    assert second.shipping['recipient_name'] == 'Rosa Vidal'
    assert second.shipping['id'] == str(ready.address.pk)
    assert order.changes.get(event='created').detail == {'source': 'cart'}
    assert CartItem.objects.count() == 2  # they stay until the order is paid


def test_two_patients_and_a_try_me_voucher_pay_with_the_fake_gateway(ready, patient):
    two_items(ready, patient)
    order = pay(finish(ready, CODE), ready)
    assert order.state == 'paid' and Order.objects.count() == 1
    assert order.lines.count() == 2
    assert VoucherRedemption.objects.get(order_id=order.pk).state == 'consumed'
    assert not CartItem.objects.exists()


def test_the_page_creates_the_order_and_goes_to_its_checkout(ready, patient):
    tps(ready, patient)
    client = browser(ready)
    html = client.get(CART).content.decode()
    assert 'data-finish' in html and 'data-voucher' in html and 'data-add-another' in html
    response = client.post(FINISH, {'voucher': ''})
    order = Order.objects.get()
    assert response.status_code == 302 and response['Location'] == f'/en/checkout/{order.pk}/'


def test_the_cart_persists_across_a_new_session(ready, patient):
    tps(ready, patient)
    browser(ready).get(CART)
    again = browser(ready).get(CART).content.decode()
    assert f'data-cart-item="{patient.code}"' in again


def test_a_try_me_voucher_reduces_the_checkout_total(ready, patient):
    two_items(ready, patient)
    order = finish(ready, ' tryme23456 ')
    assert order.voucher_taken == Decimal('50.00')
    assert order.total == order.net_total + order.vat_total
    assert VoucherRedemption.objects.get(order_id=order.pk).state == 'reserved'


def test_a_refused_voucher_shows_the_cart_with_the_reason(ready, patient):
    tps(ready, patient)
    client = browser(ready)
    for code, text in (('NOSUCHCODE', 'not valid'), (USD_CODE, 'another currency')):
        response = client.post(FINISH, {'voucher': code})
        html = response.content.decode()
        assert response.status_code == 200 and text in html and 'data-error' in html
        assert f'value="{code}"' in html and 'data-cart-item' in html
    assert not Order.objects.exists()


def test_a_voucher_covering_everything_pays_at_once_and_empties_the_cart(ready, patient):
    two_items(ready, patient)
    order = finish(ready, BIG_CODE)
    assert order.total == Decimal('0.00') and order.state == 'paid'
    assert VoucherRedemption.objects.get(order_id=order.pk).state == 'consumed'
    assert not CartItem.objects.exists()
    ready.address.refresh_from_db()
    assert ready.address.used_at is not None


def test_a_voucher_on_an_order_already_at_zero_is_refused_and_unused(ready, patient, admin):
    def free(section):
        for row in section['products']:
            if row['id'] == BRANDED['medium']:
                row['net_price']['amount'] = '0.00'
    load(admin, free, version=4)
    tps(ready, patient)
    with pytest.raises(pricing.OrderRefused) as caught:
        finish(ready, CODE)
    assert caught.value.reason == pricing.VOUCHER_NOTHING_TO_COVER
    assert not Order.objects.exists() and not VoucherRedemption.objects.exists()
    html = browser(ready).post(FINISH, {'voucher': CODE}).content.decode()
    assert 'stays unused' in html
    assert finish(ready).state == 'paid'  # without the voucher it is paid at once


def test_a_try_me_voucher_is_used_once_per_doctor(ready, patient):
    tps(ready, patient)
    assert pay(finish(ready, CODE), ready).state == 'paid'
    tps(ready, ready.second)  # 9.8: the first patient now has a live work order
    with pytest.raises(pricing.OrderRefused) as caught:
        finish(ready, CODE)
    assert caught.value.reason == pricing.VOUCHER_USED
    assert Order.objects.count() == 1


def test_finishing_twice_cancels_the_earlier_order_and_frees_its_voucher(ready, patient):
    two_items(ready, patient)
    first = finish(ready, CODE)
    second = finish(ready, CODE)
    first.refresh_from_db()
    assert first.state == 'cancelled' and second.state == 'checkout' and first.pk != second.pk
    states = dict(VoucherRedemption.objects.values_list('order_id', 'state'))
    assert states == {first.pk: 'released', second.pk: 'reserved'}
    assert CartItem.objects.count() == 2


def test_approving_by_transfer_empties_the_cart_of_its_patients(bank, patient):
    two_items(bank, patient)
    order = finish(bank)
    client = browser(bank)
    response = client.post(f'/en/checkout/{order.pk}/', {
        'action': 'pay', 'entity': str(order.fiscal_entity_id), 'gateway': 'bank_transfer',
        'version': order.version, 'total': str(order.total)})
    assert response['Location'] == f'/en/checkout/{order.pk}/transfer/'
    order.refresh_from_db()
    assert order.state == 'pending_payment' and not CartItem.objects.exists()
    html = client.get(response['Location']).content.decode()
    assert 'stays pending until we receive the transfer' in html
    assert 'data-transfer-deadline' not in html and 'expires' not in html
    assert 'data-transfer-pending' in html
    bank.address.refresh_from_db()
    assert bank.address.used_at is None  # marked used only when paid


def test_approving_by_transfer_keeps_the_items_of_other_patients(bank, patient):
    tps(bank, patient)
    order = finish(bank)
    third = third_patient(bank)
    tps(bank, third)
    by_transfer(order, bank)
    by_transfer(Order.objects.get(pk=order.pk), bank)  # chosen again: idempotent
    assert list(CartItem.objects.values_list('patient__code', flat=True)) == [third.code]


def test_paying_a_transfer_order_keeps_an_item_added_since_for_its_patient(bank, patient):
    tps(bank, patient)
    order = by_transfer(finish(bank), bank)
    assert not CartItem.objects.exists()
    # 9.8: the order's pending-payment work order keeps the patient out of the cart.
    with pytest.raises(service.CartRefused) as caught:
        tps(bank, patient)
    assert caught.value.code == service.LIVE_WORK_ORDER
    # An item that got in all the same (added before 9.8) stays when the order is paid.
    CartItem.objects.create(doctor=bank, patient=patient, product_id=BRANDED['medium'],
                            add_on_codes=[], maker='undecided')
    payments.mark_transfer_paid(order, order.total, expected_version=order.version,
                                actor=payments.SYSTEM_ACTOR)
    assert Order.objects.get(pk=order.pk).state == 'paid'
    assert list(CartItem.objects.values_list('patient__code', flat=True)) == [patient.code]


def test_paying_by_card_leaves_the_cart_until_paid(bank, patient):
    two_items(bank, patient)
    order = payments.start_payment(finish(bank), 'fake', expected_version=1,
                                   actor=actor(bank))
    assert order.state == 'pending_payment' and CartItem.objects.count() == 2


def test_a_cart_receiver_that_raises_rolls_the_transfer_approval_back(bank, patient,
                                                                      monkeypatch):
    two_items(bank, patient)
    order = finish(bank)

    def boom(order):
        raise RuntimeError('boom')
    monkeypatch.setattr(service, '_remove_ordered', boom)
    with pytest.raises(payments.HookFailed):
        by_transfer(order, bank)
    order.refresh_from_db()
    assert order.state == 'checkout' and CartItem.objects.count() == 2
    assert not Payment.objects.filter(order=order).exists()


@pytest.mark.parametrize('switched', [False, True])
def test_finishing_again_leaves_a_transfer_order_alone(bank, patient, switched):
    tps(bank, patient)
    first = by_transfer(finish(bank, CODE), bank)
    if switched:  # the doctor switched to a card and left it open
        first = payments.start_payment(first, 'fake', expected_version=first.version,
                                       actor=actor(bank))
    tps(bank, third_patient(bank))
    second = finish(bank)
    assert second.pk != first.pk and second.state == 'checkout'
    fresh = Order.objects.get(pk=first.pk)
    assert (fresh.state, fresh.version) == ('pending_payment', first.version)
    assert VoucherRedemption.objects.get(order_id=first.pk).state == 'reserved'


def test_a_transfer_order_frozen_20_days_ago_does_not_expire(bank, patient):
    two_items(bank, patient)
    order = by_transfer(finish(bank, CODE), bank)
    Order.objects.filter(pk=order.pk).update(pending_at=timezone.now() - timedelta(days=20))
    from django.core.management import call_command
    call_command('expire_orders')
    order.refresh_from_db()
    assert order.state == 'pending_payment'
    assert VoucherRedemption.objects.get(order_id=order.pk).state == 'reserved'
    payments.cancel(order, expected_version=order.version, actor=actor(bank))
    assert VoucherRedemption.objects.get(order_id=order.pk).state == 'released'


def test_a_refused_second_finish_leaves_the_earlier_order_alone(ready, patient):
    tps(ready, patient)
    first = finish(ready)
    with pytest.raises(pricing.OrderRefused):
        finish(ready, 'NOSUCHCODE')
    first.refresh_from_db()
    assert first.state == 'checkout'


def test_a_paid_order_empties_the_cart_of_its_patients_and_marks_the_address_used(
        ready, patient):
    two_items(ready, patient)
    order = pay(finish(ready), ready)
    assert order.state == 'paid' and not CartItem.objects.exists()
    ready.address.refresh_from_db()
    assert ready.address.used_at is not None


def test_a_paid_order_keeps_the_items_of_other_patients(ready, patient):
    tps(ready, patient)
    order = finish(ready)
    third = patients_service.create_patient(
        ready, first_name='Eva', surname='Ruiz', actor=patients_service.Actor(
            'doctor', ready.uid))
    tps(ready, third)
    pay(order, ready)
    assert list(CartItem.objects.values_list('patient__code', flat=True)) == [third.code]


def test_a_cart_needing_two_companies_is_refused(ready, patient, admin):
    def second_company(section):
        for row in section['billing_rules']:
            if row['category'] == 'virtuaortho_digitaltwin':
                row['company'] = IRELAND
    load(admin, second_company, version=4)
    two_items(ready, patient)
    with pytest.raises(pricing.OrderRefused) as caught:
        finish(ready)
    assert caught.value.reason == pricing.TWO_COMPANIES
    html = browser(ready).post(FINISH, {}).content.decode()
    assert 'order them separately' in html and not Order.objects.exists()


def test_an_item_whose_address_was_deleted_refuses_the_finish(ready, patient):
    twin(ready, patient)
    CartItem.objects.update(shipping_address=None)  # what deleting the address does
    assert refused(ready) == service.ADDRESS_GONE
    assert 'Remove that item' in browser(ready).post(FINISH, {}).content.decode()


def test_an_archived_address_refuses_the_finish_too(ready, patient):
    twin(ready, patient)
    ready.address.archived_at = ready.address.created_at
    ready.address.save()
    assert refused(ready) == service.ADDRESS_GONE


def test_a_patient_with_a_new_live_work_order_cancels_the_order_at_payment(ready, patient):
    tps(ready, patient)
    order = finish(ready, CODE)
    WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(),
                             product_id=BRANDED['short'], stage='pending_assignment',
                             actor_type='system')
    with pytest.raises(payments.PatientBusy):
        payments.start_payment(order, 'fake', expected_version=order.version,
                               actor=actor(ready))
    order.refresh_from_db()
    assert order.state == 'cancelled'
    assert VoucherRedemption.objects.get(order_id=order.pk).state == 'released'
    assert CartItem.objects.count() == 1


def test_the_page_shows_the_busy_patient_message(ready, patient):
    tps(ready, patient)
    order = finish(ready)
    WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(),
                             product_id=BRANDED['short'], stage='pending_assignment',
                             actor_type='system')
    client = browser(ready)
    response = client.post(f'/en/checkout/{order.pk}/', {
        'action': 'pay', 'entity': str(order.fiscal_entity_id), 'gateway': 'fake',
        'version': order.version, 'total': str(order.total)}, follow=True)
    assert 'now has a case in progress' in response.content.decode()


def test_a_live_ice_still_lets_a_tps_through(ready, patient):
    WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(), product_id=ICE,
                             stage='pending_assignment', actor_type='system')
    tps(ready, patient)
    order = finish(ready)
    assert pay(order, ready).state == 'paid'


def test_a_free_order_is_checked_again_before_it_is_paid(ready, patient):
    tps(ready, patient)
    order = finish(ready)
    # Force the total to 0.00 on an order still in checkout, as a free order waiting to be paid.
    Order.objects.filter(pk=order.pk).update(total=Decimal('0.00'))
    order.refresh_from_db()
    WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(),
                             product_id=BRANDED['short'], stage='pending_assignment',
                             actor_type='system')
    with pytest.raises(payments.PatientBusy):
        payments.start_payment(order, '', expected_version=order.version, actor=actor(ready))
    order.refresh_from_db()
    assert order.state == 'cancelled'


def test_a_spark_item_keeps_its_aligners_on_the_line(ready, patient):
    service.add_virtuaortho(ready, patient, variant='one', choice='spark', size=SPARK_ONE,
                            quantity=6, address_id=str(ready.address.pk))
    line = finish(ready).lines.get()
    assert (line.quantity, line.patient_code) == (6, patient.code)
    assert line.list_amount == Decimal('300.00')


def test_a_doctor_without_a_usable_entity_is_blocked_with_a_link(ready, patient):
    tps(ready, patient)
    for entity in fiscal.usable_entities(ready):
        entity.archived_at = entity.created_at
        entity.save()
    assert refused(ready) == service.NO_ENTITY
    html = browser(ready).post(FINISH, {}).content.decode()
    assert 'data-fiscal-block' in html and 'next=%2Fen%2Fcart%2F' in html
    assert 'data-finish>' not in html


def test_an_empty_cart_has_no_finish_button_and_the_post_is_refused(ready):
    html = browser(ready).get(CART).content.decode()
    assert 'data-finish' not in html and 'data-cart-empty' in html
    assert refused(ready) == service.EMPTY
    response = browser(ready).post(FINISH, {})
    assert response.status_code == 200 and not Order.objects.exists()


def test_only_a_doctor_may_finish_and_only_by_post(ready, patient, django_user_model):
    tps(ready, patient)
    assert browser(ready).get(FINISH).status_code == 405
    staff = django_user_model.objects.create_user('staff@example.com', 'pw-staff-1',
                                                  is_staff=True)
    assert browser(staff).post(FINISH, {}).status_code == 404


def test_a_hook_that_raises_rolls_the_payment_back_and_keeps_the_cart(ready, patient,
                                                                      monkeypatch):
    twin(ready, patient)
    order = finish(ready)
    order = payments.start_payment(order, 'fake', expected_version=order.version,
                                   actor=actor(ready))

    def boom(address, at=None):
        raise RuntimeError('boom')
    monkeypatch.setattr(shipping, 'mark_used', boom)
    with pytest.raises(payments.HookFailed):
        payments.mark_paid(order, expected_version=order.version,
                           actor=payments.SYSTEM_ACTOR)
    order.refresh_from_db()
    assert order.state == 'pending_payment' and CartItem.objects.count() == 1


def test_the_snapshot_never_reaches_the_invoice_and_no_name_is_in_the_order_texts(
        ready, patient):
    twin(ready, patient)
    order = pay(finish(ready), ready)
    from invoices.models import Invoice
    invoice = Invoice.objects.get(order_id=order.pk)
    line = invoice.lines.get()
    assert line.patient_code == patient.code
    dumped = repr([getattr(line, f.name) for f in line._meta.fields])
    assert 'Rosa Vidal' not in dumped and 'Calle Mayor' not in dumped
    assert 'Pérez' not in repr(list(order.changes.values_list('detail', flat=True)))


def test_the_snapshot_survives_choosing_the_fiscal_entity_again(ready, patient):
    twin(ready, patient)
    order = finish(ready)
    order = payments.choose_entity(order, order.fiscal_entity, expected_version=order.version,
                                   actor=actor(ready))
    line = order.lines.get()
    assert line.shipping['id'] == str(ready.address.pk) and line.patient_code == patient.code
    assert VO_TWIN['medium'] == str(line.product_id)


def test_the_cart_texts_are_translated(ready, patient):
    tps(ready, patient)
    html = browser(ready).get('/es/cart/').content.decode()
    assert 'Finalizar pedido' in html and 'Añadir otro' in html
    assert 'Código de cupón' in html


# --- The ICE deduction (epic-ordering 9.7) ------------------------------------------------

def test_the_checkout_shows_the_ice_deducted_under_its_line(ready, patient):
    service.add_tps(ready, patient, maker='undecided', size='ice')  # 50.00 EUR here
    pay(finish(ready), ready)
    tps(ready, patient)
    cart_html = browser(ready).get(CART).content.decode()
    assert 'ICE deducted' not in cart_html  # the cart page is unchanged
    order = finish(ready)
    line = order.lines.get()
    assert (line.ice, line.net) == (Decimal('50.00'), Decimal('44.00'))
    client = browser(ready)
    html = client.get(f'/en/checkout/{order.pk}/').content.decode()
    assert html.count('data-ice-deducted') == 1
    assert 'ICE deducted &minus;50.00 EUR' in html and '44.00 EUR' in html
    spanish = client.get(f'/es/checkout/{order.pk}/').content.decode()
    assert 'ICE descontado &minus;50,00 EUR' in spanish


def test_a_line_without_an_ice_credit_shows_no_note(ready, patient):
    tps(ready, patient)
    order = finish(ready)
    html = browser(ready).get(f'/en/checkout/{order.pk}/').content.decode()
    assert 'data-ice-deducted' not in html


def test_the_ice_taken_refusal_is_translated(ready, patient, monkeypatch):
    tps(ready, patient)

    def taken(*args, **kwargs):
        raise service.CartRefused(service.ICE_TAKEN)
    monkeypatch.setattr(service, 'finish', taken)
    html = browser(ready).post('/es/cart/finish/', {}).content.decode()
    assert 'está reservado por otro de sus' in html and 'se calcula sin él' in html
