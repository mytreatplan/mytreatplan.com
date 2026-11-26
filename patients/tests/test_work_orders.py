"""Work orders per service (epic-ordering 9.8): one test (or more) per row of the plan's
matrix, the replay of each hook, the blocking check in the cart, the order and
start_payment, the ICE closed by a paid planning, and the work-order events (no patient
name or code). The doctor, catalogue and helpers are the cart checkout tests' (Advanced,
two patients, a bank transfer offered by the Spanish company)."""
import json
import uuid

import pytest

from cart import service as cart
from cart.models import CartItem
from cart.tests.test_checkout import (actor, bank, by_transfer, finish, pay,  # noqa: F401
                                      ready, tps, twin)
from catalogue import pricing, resolver
from integration.models import OutboxEvent
from patients import service
from patients.models import WorkOrder
from patients.tests.conftest import BRANDED
from payments import hooks, service as payments
from payments.models import Order

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

SYSTEM = payments.SYSTEM_ACTOR


def events(event_type=None):
    rows = OutboxEvent.objects.filter(type__startswith='work_order.').order_by('id')
    if event_type:
        rows = rows.filter(type=event_type)
    return list(rows)


def stages_of(patient):
    return sorted(WorkOrder.objects.filter(patient=patient).values_list('stage', flat=True))


def paid_ice(doctor, patient):
    """A paid ICE of the patient (through the cart): its live work order."""
    cart.add_tps(doctor, patient, maker='undecided', size='ice')
    assert pay(finish(doctor), doctor).state == 'paid'
    return service.live_work_order(patient)


def transfer_order(doctor, patient):
    """A TPS for the patient, approved by bank transfer (its order, `pending_payment`)."""
    tps(doctor, patient)
    order = by_transfer(finish(doctor), doctor)
    assert order.state == 'pending_payment'
    return order


def staff_paid(order):
    order = Order.objects.get(pk=order.pk)
    return payments.mark_transfer_paid(order, order.total, expected_version=order.version,
                                       actor=SYSTEM)


def refused_order(doctor, patient):
    with pytest.raises(pricing.OrderRefused) as caught:
        payments.create_order(doctor, [resolver.OrderLine(resolver.product(BRANDED['medium']),
                                                          patient_code=patient.code)],
                              actor=actor(doctor))
    assert caught.value.reason == pricing.PATIENT_REFUSED


def refused_cart(doctor, patient):
    with pytest.raises(cart.CartRefused) as caught:
        tps(doctor, patient)
    assert caught.value.code == cart.LIVE_WORK_ORDER


# --- Two patients paid ----------------------------------------------------------------------

def test_a_paid_order_gives_each_patient_one_work_order_and_an_event(ready, patient):
    tps(ready, patient)
    twin(ready, ready.second)
    order = pay(finish(ready), ready)
    first, second = order.lines.order_by('position')
    rows = {row.patient_id: row for row in WorkOrder.objects.all()}
    assert len(rows) == 2
    for line, who, category in ((first, patient, 'tps_branded'),
                                (second, ready.second, 'virtuaortho_digitaltwin')):
        row = rows[who.pk]
        assert (row.stage, row.version, row.order_line_id, row.order_id, row.product_id) == (
            'pending_assignment', 1, line.pk, order.pk, line.product_id)
    created = events('work_order.created')
    assert len(events()) == len(created) == 2
    assert {(event.aggregate_id, event.aggregate_version) for event in created} == {
        (row.pk, 1) for row in rows.values()}
    by_id = {str(row.pk): row for row in rows.values()}
    for event in created:
        row = by_id[event.payload['work_order_id']]
        assert event.payload == {
            'work_order_id': str(row.pk), 'stage': 'pending_assignment',
            'product_category': resolver.product(row.product_id).category,
            'at': event.payload['at']}
        assert event.payload['at'].endswith('Z')
    assert sorted(event.payload['product_category'] for event in created) == [
        'tps_branded', 'virtuaortho_digitaltwin']


def test_the_events_carry_no_patient_name_or_code(ready, patient):
    tps(ready, patient)
    twin(ready, ready.second)
    pay(finish(ready), ready)
    for event in events():
        text = json.dumps(event.payload)
        for secret in (patient.code, ready.second.code, 'Ana', 'Pérez', 'Luis', 'Gil'):
            assert secret not in text
        assert set(event.payload) == {'work_order_id', 'stage', 'product_category', 'at'}


def test_a_replayed_paid_hook_adds_nothing(ready, patient):
    tps(ready, patient)
    order = pay(finish(ready), ready)
    hooks.fire('paid', order)
    service.create_work_orders_for_order(order)
    assert WorkOrder.objects.count() == 1 and len(events()) == 1


def test_an_order_line_without_a_patient_code_has_no_work_order(ready, patient):
    order = payments.create_order(ready, [resolver.OrderLine(resolver.product(
        BRANDED['medium']))], actor=actor(ready))
    pay(order, ready)
    assert not WorkOrder.objects.exists() and not events()


# --- Transfer approved, chosen again, paid, cancelled ---------------------------------------

def test_a_transfer_holds_a_pending_payment_work_order_that_blocks(bank, patient):
    order = transfer_order(bank, patient)
    row = WorkOrder.objects.get()
    line = order.lines.get()
    assert (row.patient, row.stage, row.version, row.order_id, row.order_line_id) == (
        patient, 'pending_payment', 1, order.pk, line.pk)
    assert [(event.type, event.payload['stage']) for event in events()] == [
        ('work_order.created', 'pending_payment')]
    assert service.live_work_order(patient) is None
    assert service.blocking_work_order(patient) == row
    assert service.blocking_work_order(patient, exclude_order=order) is None
    refused_cart(bank, patient)
    refused_order(bank, patient)
    # An ICE is refused too.
    with pytest.raises(cart.CartRefused):
        cart.add_tps(bank, patient, maker='undecided', size='ice')


def test_an_order_made_before_the_transfer_is_refused_at_payment(bank, patient):
    earlier = payments.create_order(bank, [resolver.OrderLine(resolver.product(
        BRANDED['medium']), patient_code=patient.code)], actor=actor(bank))
    transfer_order(bank, patient)
    with pytest.raises(payments.PatientBusy):
        payments.start_payment(earlier, 'fake', expected_version=earlier.version,
                               actor=actor(bank))
    assert Order.objects.get(pk=earlier.pk).state == 'cancelled'
    assert stages_of(patient) == ['pending_payment']


def test_choosing_the_transfer_again_adds_nothing(bank, patient):
    order = transfer_order(bank, patient)
    order = by_transfer(Order.objects.get(pk=order.pk), bank)  # its own row never blocks it
    assert order.state == 'pending_payment'
    service.hold_work_orders_for_transfer(order)
    hooks.fire('transfer_approved', order)
    assert WorkOrder.objects.count() == 1 and len(events()) == 1


def test_staff_recording_the_money_moves_the_same_row_to_pending_assignment(bank, patient):
    order = transfer_order(bank, patient)
    held = WorkOrder.objects.get()
    assert staff_paid(order).state == 'paid'
    row = WorkOrder.objects.get()
    assert (row.pk, row.stage, row.version) == (held.pk, 'pending_assignment', 2)
    assert [(event.type, event.aggregate_version, event.payload['stage'])
            for event in events()] == [('work_order.created', 1, 'pending_payment'),
                                       ('work_order.stage_changed', 2, 'pending_assignment')]
    # Replayed: nothing more.
    service.create_work_orders_for_order(Order.objects.get(pk=order.pk))
    assert WorkOrder.objects.count() == 1 and len(events()) == 2


def test_switching_to_card_and_paying_moves_the_same_row(bank, patient):
    order = transfer_order(bank, patient)
    held = WorkOrder.objects.get()
    order = pay(Order.objects.get(pk=order.pk), bank)  # its own row never blocks it
    assert order.state == 'paid'
    row = WorkOrder.objects.get()
    assert (row.pk, row.stage, row.version) == (held.pk, 'pending_assignment', 2)


def test_the_row_is_found_by_its_order_when_the_line_ids_changed(bank, patient):
    order = transfer_order(bank, patient)
    held = WorkOrder.objects.get()
    WorkOrder.objects.filter(pk=held.pk).update(order_line_id=uuid.uuid4())
    service.hold_work_orders_for_transfer(order)  # still found: nothing added
    staff_paid(order)
    assert WorkOrder.objects.get().pk == held.pk
    assert WorkOrder.objects.get().stage == 'pending_assignment'


def test_cancelling_the_order_cancels_its_pending_work_orders(bank, patient):
    tps(bank, patient)
    twin(bank, bank.second)
    order = by_transfer(finish(bank), bank)
    assert stages_of(patient) == stages_of(bank.second) == ['pending_payment']
    order = payments.cancel(order, expected_version=order.version, actor=actor(bank))
    assert order.state == 'cancelled'
    assert stages_of(patient) == stages_of(bank.second) == ['cancelled']
    assert sorted((event.type, event.payload['stage']) for event in events()) == [
        ('work_order.created', 'pending_payment'), ('work_order.created', 'pending_payment'),
        ('work_order.stage_changed', 'cancelled'), ('work_order.stage_changed', 'cancelled')]
    # Replayed (cancelled, then expired): nothing more.
    service.cancel_work_orders_for_order(order)
    hooks.fire('expired', order)
    assert len(events()) == 4
    # The patients are free again.
    assert service.blocking_work_order(patient) is None
    tps(bank, patient)


def test_staff_cancelling_on_the_admin_site_cancels_them_too(bank, patient):
    """The staff path: the pending transfer resource's remote write (9.18)."""
    from integration import transfers
    from mytp_core.contracts.remote import TRANSFER_CANCELLED
    tps(bank, patient)
    twin(bank, bank.second)
    order = by_transfer(finish(bank), bank)
    done = transfers.update(order.pk, order.version, {'state': TRANSFER_CANCELLED},
                            actor=payments.admin_actor(uuid.uuid4()))
    assert done.state == 'cancelled'
    assert list(WorkOrder.objects.filter(order_id=order.pk).values_list(
        'stage', flat=True)) == ['cancelled', 'cancelled']
    assert stages_of(patient) == stages_of(bank.second) == ['cancelled']


def test_an_expired_order_cancels_its_pending_work_orders(bank, patient):
    order = transfer_order(bank, patient)
    service.cancel_work_orders_for_order(order)  # the `expired` receiver
    assert stages_of(patient) == ['cancelled']


# --- Planning after an ICE --------------------------------------------------------------------

def test_a_paid_planning_closes_the_live_ice_and_creates_its_own(ready, patient):
    ice = paid_ice(ready, patient)
    tps(ready, patient)  # a live ICE lets a planning through
    order = pay(finish(ready), ready)
    ice.refresh_from_db()
    assert (ice.stage, ice.version) == ('closed', 2)
    planning = service.live_work_order(patient)
    assert (planning.stage, planning.order_id) == ('pending_assignment', order.pk)
    assert [(event.type, event.aggregate_id, event.payload['stage'])
            for event in events()] == [
        ('work_order.created', ice.pk, 'pending_assignment'),
        ('work_order.stage_changed', ice.pk, 'closed'),
        ('work_order.created', planning.pk, 'pending_assignment')]
    assert events()[1].payload['product_category'] == 'initial_case_evaluation'


def test_a_closed_ice_neither_blocks_nor_counts_as_a_finished_treatment(ready, patient):
    paid_ice(ready, patient)
    tps(ready, patient)
    pay(finish(ready), ready)
    planning = service.live_work_order(patient)
    service.move_work_order(planning, 'cancelled', planning.version, SYSTEM)
    assert stages_of(patient) == ['cancelled', 'closed']
    assert service.blocking_work_order(patient) is None
    assert not service.has_finished_treatment(patient)
    tps(ready, patient)  # free again
    assert CartItem.objects.count() == 1


def test_a_closing_that_meets_another_live_case_rolls_the_payment_back(ready, patient):
    ice = paid_ice(ready, patient)
    line = resolver.OrderLine(resolver.product(BRANDED['medium']), patient_code=patient.code)
    first = payments.create_order(ready, [line], actor=actor(ready))
    second = payments.create_order(ready, [line], actor=actor(ready))  # the ICE lets both in
    second = payments.start_payment(second, 'fake', expected_version=second.version,
                                    actor=actor(ready))
    pay(first, ready)
    before = len(events())
    with pytest.raises(payments.HookFailed):
        payments.mark_paid(second, expected_version=second.version, actor=SYSTEM)
    assert Order.objects.get(pk=second.pk).state == 'pending_payment'
    ice.refresh_from_db()
    assert ice.stage == 'closed' and stages_of(patient) == ['closed', 'pending_assignment']
    assert len(events()) == before


def test_a_planning_by_transfer_leaves_the_ice_live_until_paid(bank, patient):
    ice = paid_ice(bank, patient)
    order = transfer_order(bank, patient)
    ice.refresh_from_db()
    assert ice.stage == 'pending_assignment'
    assert stages_of(patient) == ['pending_assignment', 'pending_payment']
    # The pending planning blocks another planning although the ICE is live.
    refused_cart(bank, patient)
    refused_order(bank, patient)
    staff_paid(order)
    ice.refresh_from_db()
    assert ice.stage == 'closed'
    assert stages_of(patient) == ['closed', 'pending_assignment']
    assert [event.payload['stage'] for event in events()] == [
        'pending_assignment', 'pending_payment', 'closed', 'pending_assignment']


def test_an_ice_is_never_closed_by_another_ice(ready, patient):
    ice = paid_ice(ready, patient)
    with pytest.raises(cart.CartRefused):
        cart.add_tps(ready, patient, maker='undecided', size='ice')
    ice.refresh_from_db()
    assert ice.stage == 'pending_assignment'


# --- Manual pending-payment work orders (9.9) ------------------------------------------------

def test_a_manual_pending_payment_work_order_never_blocks(ready, patient):
    WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(),
                             stage='pending_payment', actor_type='system')
    assert service.blocking_work_order(patient) is None
    tps(ready, patient)
    order = pay(finish(ready), ready)
    assert order.state == 'paid'
    assert stages_of(patient) == ['pending_assignment', 'pending_payment']


# --- The stage-change event of move_work_order -------------------------------------------------

def test_every_stage_move_records_its_event(ready, patient):
    tps(ready, patient)
    pay(finish(ready), ready)
    row = WorkOrder.objects.get()
    moved = service.move_work_order(row, 'assigned', 1, SYSTEM)
    last = events()[-1]
    assert (last.type, last.aggregate_id, last.aggregate_version, last.payload['stage']) == (
        'work_order.stage_changed', row.pk, 2, 'assigned')
    with pytest.raises(service.InvalidStageTransition):
        service.move_work_order(moved, 'approved', 2, SYSTEM)
    assert len(events()) == 2


def test_a_work_order_without_a_catalogue_product_sends_an_empty_category(patient):
    row = WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(),
                                   stage='pending_assignment', actor_type='system')
    service.move_work_order(row, 'assigned', 1, SYSTEM)
    assert events()[-1].payload['product_category'] == ''
