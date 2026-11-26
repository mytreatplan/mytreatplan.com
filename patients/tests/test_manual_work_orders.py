"""Pending-payment (manual) work orders (epic-ordering 9.9): one test (or more) per row of the
plan's matrix (created, unknown patient, not blocking, added to the cart, paid, by transfer,
order cancelled, no decline), the tier price at the moment it is added, and the wizard that
adds it with the patient and the product fixed. Clinical ops are stood in for by
`patients.tests.conftest.manual_work_order` (the seam `create_manual_work_order`). The
doctor, catalogue and helpers are the cart checkout tests' (Advanced, two patients, a bank
transfer offered by the Spanish company)."""
import json
import re
import uuid
from decimal import Decimal

import pytest
from django.contrib.messages import get_messages

from accounts import makers
from cart import service as cart
from cart.models import CartItem
from cart.tests.test_checkout import (actor, bank, browser, by_transfer, finish, pay,  # noqa: F401
                                      ready, tps)
from catalogue import resolver
from integration.models import OutboxEvent
from patients import service
from patients.models import WorkOrder
from patients.tests.conftest import (BRANDED, CLINICAL_OPS, ICE, IN_HOUSE, ODB_TWO,
                                     RETAINERS_3, SPARK_ONE, VO_TWIN, manual_work_order)
from payments import hooks
from payments import service as payments
from payments.models import Order
from subscriptions import service as subscriptions
from subscriptions.tests.helpers import PREMIUM

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

SYSTEM = payments.SYSTEM_ACTOR
HOME = '/en/home/'
CREDENTIALS = {'username': 'dr-user', 'password': 'dr-password'}


def events():
    return list(OutboxEvent.objects.filter(type__startswith='work_order.').order_by('id'))


def stages_of(patient):
    return sorted(WorkOrder.objects.filter(patient=patient).values_list('stage', flat=True))


def add(doctor, work_order, **options):
    """The work order into the cart (a branded TPS: a maker is needed)."""
    options.setdefault('maker', 'undecided')
    return cart.add_work_order(doctor, work_order, **options)


def staff_paid(order):
    order = Order.objects.get(pk=order.pk)
    return payments.mark_transfer_paid(order, order.total, expected_version=order.version,
                                       actor=SYSTEM)


def home(doctor):
    return browser(doctor).get(HOME).content.decode()


def wizard_url(work_order, step):
    return f'/en/start-a-case/work-order/{work_order.pk}/{step}/'


def go(client, work_order, step, current=None, **data):
    return client.post(wizard_url(work_order, step),
                       {f'case_wo_{work_order.pk.hex}-current_step': current or step, **data})


# --- Created ------------------------------------------------------------------------------------

def test_created_pending_payment_without_an_order_with_its_event(ready, patient):
    row = manual_work_order(ready, patient)
    row.refresh_from_db()
    assert (row.patient, row.stage, row.version, row.order_id, row.order_line_id) == (
        patient, 'pending_payment', 1, None, None)
    assert (row.product_id, row.code) == (uuid.UUID(BRANDED['medium']), 'TPS-BR-MEDIUM')
    assert (row.actor_type, row.actor_uuid) == ('admin', CLINICAL_OPS.uuid)
    [event] = events()
    assert (event.type, event.aggregate_id, event.aggregate_version) == (
        'work_order.created', row.pk, 1)
    assert event.payload == {'work_order_id': str(row.pk), 'stage': 'pending_payment',
                             'product_category': 'tps_branded', 'at': event.payload['at']}
    text = json.dumps(event.payload)
    assert patient.code not in text and 'Ana' not in text
    # No order, no price.
    assert not Order.objects.exists() and not CartItem.objects.exists()
    assert service.manual_work_orders(ready) == [row]


def test_created_shows_on_the_home_page_with_add_to_cart(ready, patient):
    assert 'data-pending-payment' not in home(ready)  # nothing when there are none
    row = manual_work_order(ready, patient)
    html = home(ready)
    assert 'data-pending-payment' in html and 'Pending payment' in html
    assert f'data-pending-work-order="{row.pk}"' in html
    assert patient.code in html and 'TPS branded medium' in html
    assert f'href="{wizard_url(row, "options")}"' in html
    assert 'decline' not in html.lower()


def test_another_doctors_home_does_not_show_it(ready, patient, other_doctor):
    manual_work_order(ready, patient)
    assert 'data-pending-payment' not in home(other_doctor)


# --- Unknown patient --------------------------------------------------------------------------

@pytest.mark.parametrize('code', ['EU-NOSUCH00', ''])
def test_an_unknown_patient_is_refused_and_nothing_is_created(ready, code):
    with pytest.raises(service.WorkOrderRefused):
        service.create_manual_work_order(ready, code, BRANDED['medium'], actor=CLINICAL_OPS)
    assert not WorkOrder.objects.exists() and not events()


def test_another_doctors_patient_is_refused(ready, other_doctor):
    theirs = service.create_patient(other_doctor, first_name='Iris',
                                    actor=service.Actor('doctor', other_doctor.uid))
    with pytest.raises(service.WorkOrderRefused):
        service.create_manual_work_order(ready, theirs.code, BRANDED['medium'],
                                         actor=CLINICAL_OPS)
    assert not WorkOrder.objects.exists() and not events()


def test_an_inactive_patient_is_refused(ready, patient):
    from patients.models import Patient
    Patient.objects.filter(pk=patient.pk).update(status=Patient.INACTIVE)
    with pytest.raises(service.WorkOrderRefused):
        manual_work_order(ready, patient)
    assert not WorkOrder.objects.exists()


@pytest.mark.parametrize('product_id', [str(uuid.uuid4()), 'not-a-uuid', None])
def test_an_unknown_product_is_refused(ready, patient, product_id):
    with pytest.raises(service.WorkOrderRefused):
        service.create_manual_work_order(ready, patient.code, product_id, actor=CLINICAL_OPS)
    assert not WorkOrder.objects.exists()


def test_a_bad_actor_is_refused(ready, patient):
    with pytest.raises(service.InvalidActor):
        manual_work_order(ready, patient, actor=service.Actor('admin', None))
    assert not WorkOrder.objects.exists()


# --- Not blocking -------------------------------------------------------------------------------

def test_a_manual_work_order_never_blocks_another_service(ready, patient):
    row = manual_work_order(ready, patient)
    assert service.blocking_work_order(patient) is None
    tps(ready, patient, size='long')  # the cart
    order = pay(finish(ready), ready)  # the order and the payment checks
    assert order.state == 'paid'
    row.refresh_from_db()
    assert (row.stage, row.order_id, row.order_line_id) == ('pending_payment', None, None)
    assert stages_of(patient) == ['pending_assignment', 'pending_payment']


# --- Added to cart ------------------------------------------------------------------------------

def test_added_to_the_cart_linked_to_the_work_order(ready, patient):
    row = manual_work_order(ready, patient)
    item = add(ready, row, add_on_codes=['junior-1'])
    assert (item.patient, item.product_id, item.work_order_id, item.add_on_codes,
            item.maker) == (patient, uuid.UUID(BRANDED['medium']), row.pk, ['JUNIOR-1'],
                            'undecided')
    row.refresh_from_db()
    assert (row.stage, row.order_id, row.version) == ('pending_payment', None, 1)
    html = home(ready)
    assert 'data-in-cart' in html and 'data-add-to-cart' not in html
    # The order line carries it.
    order = finish(ready)
    line = order.lines.get()
    assert (line.work_order_id, line.patient_code) == (row.pk, patient.code)
    # And keeps it when the lines are priced again (another fiscal entity).
    assert payments._inputs(order)[0].work_order_id == row.pk


def test_added_twice_or_with_the_patient_in_the_cart_is_refused(ready, patient):
    row = manual_work_order(ready, patient)
    tps(ready, patient)
    with pytest.raises(cart.CartRefused) as caught:
        add(ready, row)
    assert caught.value.code == cart.IN_CART
    CartItem.objects.all().delete()
    add(ready, row)
    with pytest.raises(cart.CartRefused) as caught:
        add(ready, row)
    assert caught.value.code == cart.IN_CART
    assert CartItem.objects.count() == 1


def test_another_doctor_cannot_add_it(ready, patient, other_doctor):
    row = manual_work_order(ready, patient)
    with pytest.raises(cart.CartRefused) as caught:
        add(other_doctor, row)
    assert caught.value.code == cart.WORK_ORDER
    with pytest.raises(cart.CartRefused) as caught:
        add(ready, uuid.uuid4())
    assert caught.value.code == cart.WORK_ORDER


def test_a_branded_tps_is_never_in_house_and_an_in_house_one_has_no_maker(ready, patient):
    row = manual_work_order(ready, patient)
    item = add(ready, row, in_house=True)
    assert item.maker == 'undecided'
    other = manual_work_order(ready, ready.second, IN_HOUSE['short'])
    item = add(ready, other, maker='invisalign')
    assert (item.maker, item.product_id) == ('', uuid.UUID(IN_HOUSE['short']))


def test_a_patient_with_a_live_case_cannot_add_it(ready, patient):
    tps(ready, patient)
    pay(finish(ready), ready)
    row = manual_work_order(ready, patient)
    with pytest.raises(cart.CartRefused) as caught:
        add(ready, row)
    assert caught.value.code == cart.LIVE_WORK_ORDER


def test_a_virtuaortho_work_order_takes_its_address_and_options(ready, patient):
    row = manual_work_order(ready, patient, VO_TWIN['medium'])
    with pytest.raises(cart.CartRefused) as caught:
        cart.add_work_order(ready, row)
    assert caught.value.code == cart.ADDRESS
    item = cart.add_work_order(ready, row, address_id=str(ready.address.pk),
                               add_on_codes=['BYTE-SYNC'])
    assert (item.product_id, item.shipping_address, item.maker, item.add_on_codes) == (
        uuid.UUID(VO_TWIN['medium']), ready.address, '', ['BYTE-SYNC'])


def test_a_spark_work_order_takes_its_aligners(ready, patient):
    row = manual_work_order(ready, patient, SPARK_ONE)
    address = str(ready.address.pk)
    with pytest.raises(cart.CartRefused) as caught:
        cart.add_work_order(ready, row, address_id=address, quantity=2)
    assert caught.value.code == cart.QUANTITY
    assert cart.add_work_order(ready, row, address_id=address, quantity=6).quantity == 6


def test_an_odb_work_order_under_digitaltwin_carries_odb_digitaltwin(ready, patient):
    row = manual_work_order(ready, patient, ODB_TWO)
    address = str(ready.address.pk)
    with pytest.raises(cart.CartRefused) as caught:
        cart.add_work_order(ready, row, address_id=address)  # no variant chosen
    assert caught.value.code == cart.NOT_OFFERED
    item = cart.add_work_order(ready, row, address_id=address, variant='digitaltwin')
    assert item.add_on_codes == ['ODB-DT']


# --- Tier price at add time (acceptance criterion 1) ----------------------------------------

def test_the_cart_shows_the_tier_price_of_the_moment_it_is_added(ready, patient):
    row = manual_work_order(ready, patient)
    product = resolver.product(BRANDED['medium'])
    advanced = resolver.price(product, subscriptions.effective(ready).subscription)
    assert advanced.amount == Decimal('94.00')  # Advanced: 6 % off branded TPS
    record = subscriptions.record_of(ready)
    subscriptions.change(ready, expected_version=record.version, actor=subscriptions.SYSTEM_ACTOR,
                         reason='test', tier='premium', subscription_id=PREMIUM)
    add(ready, row)
    html = browser(ready).get('/en/cart/').content.decode()
    assert '88.00 EUR' in html and '94.00 EUR' not in html  # Premium: 12 % off
    assert finish(ready).lines.get().net == Decimal('88.00')
    assert not hasattr(row, 'price') and 'price' not in {
        field.name for field in WorkOrder._meta.get_fields()}


# --- Paid (acceptance criterion 2) --------------------------------------------------------------

def test_paid_moves_the_same_row_to_pending_assignment(ready, patient):
    row = manual_work_order(ready, patient)
    add(ready, row)
    order = pay(finish(ready), ready)
    assert order.state == 'paid'
    line = order.lines.get()
    rows = list(WorkOrder.objects.filter(patient=patient, product_id=BRANDED['medium']))
    assert len(rows) == 1
    [adopted] = rows
    assert (adopted.pk, adopted.stage, adopted.order_line_id, adopted.order_id,
            adopted.version) == (row.pk, 'pending_assignment', line.pk, order.pk, 2)
    assert [(event.type, event.aggregate_id, event.payload['stage']) for event in events()] == [
        ('work_order.created', row.pk, 'pending_payment'),
        ('work_order.stage_changed', row.pk, 'pending_assignment')]
    assert service.manual_work_orders(ready) == [] and not CartItem.objects.exists()
    assert 'data-pending-payment' not in home(ready)
    # Replayed: nothing more.
    hooks.fire('paid', order)
    service.create_work_orders_for_order(order)
    assert WorkOrder.objects.count() == 1 and len(events()) == 2


def test_a_live_conflict_at_payment_rolls_the_payment_back(ready, patient):
    row = manual_work_order(ready, patient)
    add(ready, row)
    first = finish(ready)
    first = payments.start_payment(first, 'fake', expected_version=first.version,
                                   actor=actor(ready))
    # Meanwhile the patient's other service is paid (the manual one never blocked it).
    other = payments.create_order(ready, [resolver.OrderLine(
        resolver.product(BRANDED['long']), patient_code=patient.code)], actor=actor(ready))
    pay(other, ready)
    before = len(events())
    with pytest.raises(payments.HookFailed):
        payments.mark_paid(first, expected_version=first.version, actor=SYSTEM)
    assert Order.objects.get(pk=first.pk).state == 'pending_payment'
    row.refresh_from_db()
    assert (row.stage, row.order_line_id, row.order_id, row.version) == (
        'pending_payment', None, None, 1)
    assert stages_of(patient) == ['pending_assignment', 'pending_payment']
    assert len(events()) == before


def test_a_work_order_gone_meanwhile_falls_back_to_a_new_row(ready, patient):
    row = manual_work_order(ready, patient)
    add(ready, row)
    order = finish(ready)
    service.move_work_order(row, 'cancelled', 1, SYSTEM)  # clinical ops cancel it (later)
    pay(order, ready)
    assert stages_of(patient) == ['cancelled', 'pending_assignment']


# --- By transfer --------------------------------------------------------------------------------

def test_by_transfer_the_manual_row_gets_the_order_and_blocks(bank, patient):
    row = manual_work_order(bank, patient)
    add(bank, row)
    order = by_transfer(finish(bank), bank)
    assert order.state == 'pending_payment'
    held = WorkOrder.objects.get()
    assert (held.pk, held.stage, held.order_id, held.order_line_id, held.version) == (
        row.pk, 'pending_payment', order.pk, None, 2)
    assert len(events()) == 1  # no second row, no stage change
    assert service.blocking_work_order(patient) == held
    assert service.blocking_work_order(patient, exclude_order=order) is None
    assert not CartItem.objects.exists()
    with pytest.raises(cart.CartRefused) as caught:
        tps(bank, patient)
    assert caught.value.code == cart.LIVE_WORK_ORDER
    html = home(bank)
    assert 'data-waiting' in html and 'data-add-to-cart' not in html
    # Chosen again: nothing more.
    order = by_transfer(Order.objects.get(pk=order.pk), bank)
    hooks.fire('transfer_approved', order)
    assert WorkOrder.objects.count() == 1
    # Paid by staff: the same row moves.
    assert staff_paid(order).state == 'paid'
    paid = WorkOrder.objects.get()
    assert (paid.pk, paid.stage, paid.order_line_id, paid.order_id) == (
        row.pk, 'pending_assignment', order.lines.get().pk, order.pk)


def test_the_wizard_refuses_a_work_order_waiting_for_its_transfer(bank, patient):
    row = manual_work_order(bank, patient)
    add(bank, row)
    by_transfer(finish(bank), bank)
    client = browser(bank)
    response = client.get(wizard_url(row, 'options'))
    assert response.status_code == 302 and response.url == HOME
    assert [str(message) for message in get_messages(response.wsgi_request)] == [
        'This service can no longer be added to your cart.']


# --- Order cancelled or expired ---------------------------------------------------------------

def test_a_cancelled_transfer_order_gives_the_manual_row_back(bank, patient):
    row = manual_work_order(bank, patient)
    add(bank, row)
    order = by_transfer(finish(bank), bank)
    order = payments.cancel(Order.objects.get(pk=order.pk), expected_version=order.version,
                            actor=actor(bank))
    assert order.state == 'cancelled'
    back = WorkOrder.objects.get()
    assert (back.pk, back.stage, back.order_id, back.order_line_id) == (
        row.pk, 'pending_payment', None, None)
    assert service.blocking_work_order(patient) is None
    assert len(events()) == 1  # never cancelled
    html = home(bank)
    assert f'data-pending-work-order="{row.pk}"' in html and 'data-add-to-cart' in html
    # Replayed (cancelled, then expired): nothing more; it can be added again.
    service.cancel_work_orders_for_order(order)
    hooks.fire('expired', order)
    assert WorkOrder.objects.get().order_id is None
    assert add(bank, row).work_order_id == row.pk


def test_an_expired_transfer_order_gives_the_manual_row_back(bank, patient):
    row = manual_work_order(bank, patient)
    add(bank, row)
    order = by_transfer(finish(bank), bank)
    hooks.fire('expired', Order.objects.get(pk=order.pk))  # the `expired` receivers
    row.refresh_from_db()
    assert (row.stage, row.order_id) == ('pending_payment', None)


def test_a_9_8_hold_in_the_same_order_is_still_cancelled(bank, patient):
    row = manual_work_order(bank, patient)
    add(bank, row)
    tps(bank, bank.second)
    order = by_transfer(finish(bank), bank)
    payments.cancel(Order.objects.get(pk=order.pk), expected_version=order.version,
                    actor=actor(bank))
    row.refresh_from_db()
    assert (row.stage, row.order_id) == ('pending_payment', None)
    assert stages_of(bank.second) == ['cancelled']


def test_a_cancelled_card_order_leaves_the_manual_row_as_it_was(ready, patient):
    row = manual_work_order(ready, patient)
    add(ready, row)
    order = finish(ready)
    payments.cancel(order, expected_version=order.version, actor=actor(ready))
    row.refresh_from_db()
    assert (row.stage, row.order_id, row.version) == ('pending_payment', None, 1)
    assert len(events()) == 1


# --- Doctor declines ----------------------------------------------------------------------------

def test_there_is_no_decline_action(ready, patient):
    from django.urls import get_resolver
    manual_work_order(ready, patient)
    assert not [name for name in dir(service) if 'decline' in name.lower()]
    assert not [name for name in dir(cart) if 'decline' in name.lower()]
    names = [str(name) for name in get_resolver().reverse_dict.keys() if isinstance(name, str)]
    assert not [name for name in names if 'decline' in name.lower()]


# --- The wizard, patient and product fixed ---------------------------------------------------

def test_the_wizard_adds_a_branded_tps_after_the_maker(ready, patient):
    makers.save(ready, 'invisalign', CREDENTIALS)
    row = manual_work_order(ready, patient)
    client = browser(ready)
    response = client.get(wizard_url(row, 'options'))  # the maker comes first
    assert response.status_code == 302 and response.url == wizard_url(row, 'maker')
    html = client.get(wizard_url(row, 'maker')).content.decode()
    assert 'Step 1 of 2' in html and patient.code in html and 'TPS branded medium' in html
    assert 'value="brand"' in html and 'value="in_house"' not in html
    assert re.search(r'value="brand"[^>]*checked', html)
    assert f'href="{HOME}"' in html  # Back goes home
    go(client, row, 'maker', kind='brand', maker='invisalign')
    html = client.get(wizard_url(row, 'options')).content.decode()
    assert 'Step 2 of 2' in html and '94.00 EUR' in html
    assert 'value="medium"' in html and 'value="long"' not in html
    response = go(client, row, 'options', 'options_tps', size='medium', add_on=['JUNIOR-1'])
    assert response.status_code == 302 and response.url == '/en/cart/'
    item = CartItem.objects.get()
    assert (item.work_order_id, item.patient, item.product_id, item.maker,
            item.add_on_codes) == (row.pk, patient, uuid.UUID(BRANDED['medium']),
                                   'invisalign', ['JUNIOR-1'])


def test_the_wizard_of_an_in_house_tps_is_its_options_only(ready, patient):
    row = manual_work_order(ready, patient, IN_HOUSE['long'])
    client = browser(ready)
    html = client.get(wizard_url(row, 'options')).content.decode()
    assert 'Step 1 of 1' in html and 'value="long"' in html and 'checked' in html
    go(client, row, 'options', 'options_tps', size='long')
    item = CartItem.objects.get()
    assert (item.work_order_id, item.maker, item.product_id) == (
        row.pk, '', uuid.UUID(IN_HOUSE['long']))


def test_the_in_house_options_never_show_the_branded_included_cases(ready, patient,
                                                                    monkeypatch):
    monkeypatch.setattr(resolver, 'included_cases', lambda subscription, category: 2)
    row = manual_work_order(ready, patient, IN_HOUSE['long'])
    html = browser(ready).get(wizard_url(row, 'options')).content.decode()
    assert 'value="long"' in html and 'data-included' not in html


def test_the_wizard_of_an_ice_asks_who_makes_the_aligners(ready, patient):
    row = manual_work_order(ready, patient, ICE)
    client = browser(ready)
    html = client.get(wizard_url(row, 'maker')).content.decode()
    assert 'value="brand"' in html and 'value="in_house"' in html
    go(client, row, 'maker', kind='in_house')
    go(client, row, 'options', 'options_tps', size='ice')
    item = CartItem.objects.get()
    assert (item.work_order_id, item.product_id, item.maker) == (row.pk, uuid.UUID(ICE), '')


def test_the_wizard_of_a_virtuaortho_product_takes_the_address(ready, patient):
    row = manual_work_order(ready, patient, VO_TWIN['medium'])
    client = browser(ready)
    html = client.get(wizard_url(row, 'options')).content.decode()
    assert 'Step 1 of 1' in html and 'VirtuaOrtho DigitalTwin' in html
    assert 'value="case:medium"' in html and 'value="case:long"' not in html
    go(client, row, 'options', 'options_vo', item='case:medium',
       address_id=str(ready.address.pk))
    item = CartItem.objects.get()
    assert (item.work_order_id, item.product_id, item.shipping_address) == (
        row.pk, uuid.UUID(VO_TWIN['medium']), ready.address)


def test_the_wizard_of_an_odb_asks_the_variant(ready, patient):
    row = manual_work_order(ready, patient, ODB_TWO)
    client = browser(ready)
    response = client.get(wizard_url(row, 'options'))
    assert response.url == wizard_url(row, 'vo_type')
    go(client, row, 'vo_type', variant='digitaltwin')
    go(client, row, 'options', 'options_vo', item=f'odb:{ODB_TWO}',
       address_id=str(ready.address.pk))
    assert CartItem.objects.get().add_on_codes == ['ODB-DT']


def test_the_wizard_is_a_404_for_another_doctor_or_a_paid_work_order(ready, patient,
                                                                      other_doctor):
    row = manual_work_order(ready, patient)
    assert browser(other_doctor).get(wizard_url(row, 'maker')).status_code == 404
    assert browser(ready).get(
        f'/en/start-a-case/work-order/{uuid.uuid4()}/maker/').status_code == 404
    add(ready, row)
    pay(finish(ready), ready)
    assert browser(ready).get(wizard_url(row, 'maker')).status_code == 404


def test_the_wizard_sends_a_patient_already_in_the_cart_home(ready, patient):
    row = manual_work_order(ready, patient)
    tps(ready, patient)
    response = browser(ready).get(wizard_url(row, 'maker'))
    assert response.status_code == 302 and response.url == HOME
    assert [str(message) for message in get_messages(response.wsgi_request)] == [
        'This patient already has a service in your cart.']


# --- Refused before the wizard: the home row shows why ---------------------------------------

def refused_everywhere(doctor, row, code, text):
    with pytest.raises(cart.CartRefused) as caught:
        cart.check_work_order(doctor, row)
    assert caught.value.code == code
    html = home(doctor)
    assert f'data-pending-work-order="{row.pk}"' in html and 'data-refused' in html
    assert text in html and 'data-add-to-cart' not in html
    response = browser(doctor).get(wizard_url(row, 'options'))
    assert response.status_code == 302 and response.url == HOME


def test_retainers_without_a_finished_treatment_are_refused(ready, patient):
    row = manual_work_order(ready, patient, RETAINERS_3)
    refused_everywhere(ready, row, cart.NOT_FINISHED,
                       'Retainers are for a patient whose treatment is finished.')


def test_a_product_the_doctor_is_no_longer_offered_is_refused(ready, patient):
    row = manual_work_order(ready, patient, VO_TWIN['medium'])
    record = subscriptions.record_of(ready)
    subscriptions.change(ready, expected_version=record.version,
                         actor=subscriptions.SYSTEM_ACTOR, reason='test', tier='basic',
                         subscription_id=None)  # Basic has no VirtuaOrtho
    refused_everywhere(ready, row, cart.NOT_OFFERED,
                       'This service is not available in that size.')


def test_a_product_gone_from_the_catalogue_is_refused_not_linked(ready, patient):
    row = manual_work_order(ready, patient)
    WorkOrder.objects.filter(pk=row.pk).update(product_id=uuid.uuid4())
    with pytest.raises(cart.CartRefused) as caught:
        cart.check_work_order(ready, row)
    assert caught.value.code == cart.NOT_OFFERED
    html = home(ready)
    assert f'data-pending-work-order="{row.pk}"' in html and 'data-refused' in html
    assert wizard_url(row, 'options') not in html


# --- Held by another order's transfer, paid by card -------------------------------------------

def test_a_card_payment_never_takes_a_row_another_transfer_holds(bank, patient):
    row = manual_work_order(bank, patient)
    line = resolver.OrderLine(resolver.product(BRANDED['medium']), patient_code=patient.code,
                              work_order_id=row.pk)
    card = payments.create_order(bank, [line], actor=actor(bank))
    card = payments.start_payment(card, 'fake', expected_version=card.version,
                                  actor=actor(bank))
    transfer = by_transfer(payments.create_order(bank, [line], actor=actor(bank)), bank)
    row.refresh_from_db()
    assert row.order_id == transfer.pk
    with pytest.raises(payments.HookFailed):
        payments.mark_paid(card, expected_version=card.version, actor=SYSTEM)
    assert Order.objects.get(pk=card.pk).state == 'pending_payment'
    row.refresh_from_db()
    assert (row.stage, row.order_id, row.order_line_id) == (
        'pending_payment', transfer.pk, None)
    assert WorkOrder.objects.count() == 1


# --- An expired card order ---------------------------------------------------------------------

def test_an_expired_card_order_leaves_the_manual_row_waiting(ready, patient):
    row = manual_work_order(ready, patient)
    add(ready, row)
    order = finish(ready)
    assert payments.expire(order, expected_version=order.version).state == 'expired'
    row.refresh_from_db()
    assert (row.stage, row.order_id, row.order_line_id, row.version) == (
        'pending_payment', None, None, 1)
    assert f'data-pending-work-order="{row.pk}"' in home(ready)
