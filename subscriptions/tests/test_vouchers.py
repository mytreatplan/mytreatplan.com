"""Voucher redemption (epic-subscriptions entry 4): one test (or more) per row of the plan's
matrix. A redemption follows its order through the `payments.hooks` receivers of
`subscriptions.vouchers`: reserved at creation, consumed at payment, released at a cancel
or an expiry; the use is checked before an order is created; the outbox holds
`voucher.redeemed` (v1 reserved, v2 consumed) and `voucher.released` (v2). The admin site
is the fake of integration/tests/snapshot_rows.py (no network)."""
import threading
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, connections, transaction

from accounts import fiscal
from catalogue import pricing, resolver
from integration.models import OutboxEvent
from integration.tests.snapshot_rows import TPS, pull, voucher
from payments import hooks, service
from payments.models import Order
from payments.tests.test_bank_transfer import backdate, bank  # noqa: F401
from payments.tests.conftest import (COMPANY, catalogue_loaded, country_map, doctor,  # noqa: F401
                                     entity, events, fake_on)
from subscriptions import vouchers
from subscriptions.models import VoucherRedemption
from subscriptions.tests.helpers import ADVANCED, catalogue, doctor as make_doctor

pytestmark = pytest.mark.django_db

TRY_ME = 'f0000001-0000-4000-8000-000000000001'
SUBSCRIPTION = 'f0000002-0000-4000-8000-000000000002'
OTHER_TRY_ME = 'f0000003-0000-4000-8000-000000000003'
CODES = {TRY_ME: 'TRYME23456', SUBSCRIPTION: 'SUBSC23456', OTHER_TRY_ME: 'TRYM223456'}


@pytest.fixture
def loaded(doctor, entity, fake_on, admin):
    """Catalogue and vouchers pulled: a try-me (50.00), a second try-me, a subscription
    voucher (100.00)."""
    assert pull(admin, 2, catalogue(), vouchers=[
        voucher(TRY_ME, CODES[TRY_ME]),
        voucher(OTHER_TRY_ME, CODES[OTHER_TRY_ME]),
        voucher(SUBSCRIPTION, CODES[SUBSCRIPTION], type_='subscription', amount='100.00'),
    ]) == ('applied', 2)
    return admin


def actor(user):
    return service.doctor_actor(user)


def code(voucher_id):
    return resolver.voucher_by_code(CODES[voucher_id])


def new(user, voucher_id=TRY_ME, **kwargs):
    return service.create_order(
        user, [resolver.OrderLine(resolver.product(TPS))], actor=actor(user),
        voucher=code(voucher_id) if voucher_id else None, **kwargs)


def new_subscription(user, voucher_id=SUBSCRIPTION):
    return service.create_subscription_order(
        user, resolver.subscription_by_id(ADVANCED), 'upfront', actor=actor(user),
        voucher=code(voucher_id))


def pay(order, user, gateway='fake'):
    order = service.start_payment(order, gateway, expected_version=order.version,
                                  actor=actor(user))
    return service.mark_paid(order, expected_version=order.version,
                             actor=service.SYSTEM_ACTOR)


def cancel(order, user):
    return service.cancel(order, expected_version=Order.objects.get(pk=order.pk).version,
                          actor=actor(user))


def redemption(order):
    return VoucherRedemption.objects.get(order_id=order.pk)


def sent():
    """(type, aggregate version, state, redemption id) of every voucher event, in order."""
    rows = OutboxEvent.objects.filter(type__startswith='voucher.').order_by('id')
    return [(row.type, row.aggregate_version, row.payload['state'],
             row.payload['redemption_id']) for row in rows]


@pytest.fixture
def other(loaded):
    """A second doctor with a fiscal entity of their own."""
    user = make_doctor('other@example.com')
    fiscal.create(user, dict(COMPANY, tax_id='B28015865', legal_name='Otra Clínica S.L.'))
    return user


# --- Reserve, consume, release -------------------------------------------------------------

def test_a_try_me_voucher_is_reserved_when_the_order_is_created(loaded, doctor, events):
    order = new(doctor)
    held = redemption(order)
    assert (held.state, held.version, held.voucher_id, held.voucher_type) == (
        'reserved', 1, code(TRY_ME).pk, 'try_me')
    assert (held.doctor_uid, held.order_id, held.amount, held.currency) == (
        doctor.uid, order.pk, Decimal('50.00'), 'EUR')
    assert order.state == 'checkout' and order.voucher_taken == Decimal('50.00')
    assert [(t, v, s) for t, v, s, _ in sent()] == [('voucher.redeemed', 1, 'reserved')]


def test_a_paid_order_consumes_its_redemption(loaded, doctor):
    order = pay(new(doctor), doctor)
    held = redemption(order)
    assert (held.state, held.version) == ('consumed', 2)
    assert VoucherRedemption.objects.count() == 1
    assert [(t, v, s) for t, v, s, _ in sent()] == [
        ('voucher.redeemed', 1, 'reserved'), ('voucher.redeemed', 2, 'consumed')]
    assert {uid for *_, uid in sent()} == {str(held.pk)}


def test_the_events_carry_the_contract_payload_and_no_patient_data(loaded, doctor):
    order = pay(new(doctor), doctor)
    payload = OutboxEvent.objects.filter(type='voucher.redeemed').latest('id').payload
    assert set(payload) == {'redemption_id', 'voucher_id', 'voucher_type', 'client_uid',
                            'order_id', 'order_reference', 'state', 'amount', 'at'}
    assert payload['order_reference'] == order.reference
    assert payload['client_uid'] == str(doctor.uid)
    assert payload['amount'] == {'amount': '50.00', 'currency': 'EUR'}
    assert doctor.email not in str(payload)
    event = OutboxEvent.objects.filter(type='voucher.redeemed').latest('id')
    assert str(event.aggregate_id) == payload['redemption_id']


@pytest.mark.parametrize('how', ['cancel', 'expire'])
def test_a_cancelled_or_expired_order_releases_the_voucher(loaded, doctor, other, how):
    first = new(doctor)
    if how == 'cancel':
        cancel(first, doctor)
    else:
        service.expire(first, expected_version=first.version)
    assert (redemption(first).state, redemption(first).version) == ('released', 2)
    assert [(t, v, s) for t, v, s, _ in sent()] == [
        ('voucher.redeemed', 1, 'reserved'), ('voucher.released', 2, 'released')]
    # The same doctor can use the voucher again; the released row stays as history.
    again = new(doctor)
    assert redemption(again).state == 'reserved'
    assert sorted(VoucherRedemption.objects.values_list('state', flat=True)) == [
        'released', 'reserved']


def test_a_bank_transfer_order_releases_its_voucher_when_the_14_days_end(
        loaded, doctor, entity, bank):
    order = new(doctor)
    order = service.start_payment(order, 'bank_transfer', expected_version=order.version,
                                  actor=actor(doctor))
    backdate(order, 13)
    assert service.expire_stale() == 0
    assert redemption(order).state == 'reserved'  # held as long as its order
    backdate(order, 14)
    assert service.expire_stale() == 1
    assert Order.objects.get(pk=order.pk).state == 'expired'
    assert redemption(order).state == 'released'
    assert sent()[-1][:3] == ('voucher.released', 2, 'released')
    assert new(doctor)  # the voucher can be used again


def test_a_covered_order_is_reserved_and_consumed_in_one_transaction(
        loaded, doctor, entity, admin):
    pull(admin, 3, catalogue(), vouchers=[voucher(TRY_ME, CODES[TRY_ME], amount='100.00')])
    order = new(doctor)
    assert (order.state, order.total) == ('paid', Decimal('0.00'))
    assert redemption(order).state == 'consumed'
    assert [(t, v, s) for t, v, s, _ in sent()] == [
        ('voucher.redeemed', 1, 'reserved'), ('voucher.redeemed', 2, 'consumed')]


def test_a_consumed_redemption_is_never_released(loaded, doctor):
    order = pay(new(doctor), doctor)
    vouchers.release(Order.objects.get(pk=order.pk))
    assert redemption(order).state == 'consumed'
    assert len(sent()) == 2


def test_the_hooks_are_idempotent_per_order(loaded, doctor):
    order = new(doctor)
    vouchers.reserve(order)
    assert VoucherRedemption.objects.count() == 1 and len(sent()) == 1
    paid = pay(order, doctor)
    vouchers.consume(paid)  # `paid` fires again
    hooks.fire('paid', paid)
    assert redemption(order).state == 'consumed' and len(sent()) == 2
    vouchers.release(paid)
    hooks.fire('cancelled', paid)
    assert redemption(order).state == 'consumed' and len(sent()) == 2


def test_an_order_without_a_voucher_has_no_redemption(loaded, doctor):
    order = pay(new(doctor, voucher_id=None), doctor)
    assert order.voucher_id is None
    assert not VoucherRedemption.objects.exists() and not sent()
    for step in (vouchers.reserve, vouchers.consume, vouchers.release):
        assert step(order) is None


# --- The limits ----------------------------------------------------------------------------

@pytest.mark.parametrize('settle', ['reserved', 'consumed'])
def test_a_try_me_voucher_is_used_once_per_doctor(loaded, doctor, settle):
    first = new(doctor)
    if settle == 'consumed':
        pay(first, doctor)
    with pytest.raises(pricing.OrderRefused) as refused:
        new(doctor)
    assert refused.value.reason == pricing.VOUCHER_USED == 'voucher_used'
    assert Order.objects.count() == 1 and VoucherRedemption.objects.count() == 1


def test_a_try_me_voucher_is_open_to_another_doctor(loaded, doctor, other):
    new(doctor)
    second = new(other)
    assert redemption(second).doctor_uid == other.uid
    assert VoucherRedemption.objects.count() == 2


def test_another_try_me_voucher_is_a_separate_use(loaded, doctor):
    new(doctor)
    second = new(doctor, OTHER_TRY_ME)
    assert redemption(second).voucher_id == code(OTHER_TRY_ME).pk


def test_a_subscription_voucher_is_used_once_in_total(loaded, doctor, other):
    first = new_subscription(doctor)
    held = redemption(first)
    assert (held.state, held.voucher_type, held.amount) == (
        'reserved', 'subscription', Decimal('100.00'))
    with pytest.raises(pricing.OrderRefused) as refused:
        new_subscription(other)
    assert refused.value.reason == pricing.VOUCHER_USED
    assert Order.objects.count() == 1
    # Once the first order is cancelled, the other doctor can use it.
    cancel(first, doctor)
    second = new_subscription(other)
    assert redemption(second).state == 'reserved' and redemption(first).state == 'released'


def test_the_refusal_is_given_before_anything_is_written(loaded, doctor):
    new(doctor)
    events_before = OutboxEvent.objects.count()
    with pytest.raises(pricing.OrderRefused):
        new(doctor)
    assert OutboxEvent.objects.count() == events_before


def test_a_code_that_is_not_in_the_snapshot_is_refused(loaded, doctor, admin):
    gone = code(TRY_ME)
    pull(admin, 3, catalogue(), vouchers=[voucher(SUBSCRIPTION, CODES[SUBSCRIPTION],
                                                  type_='subscription', amount='100.00')])
    with pytest.raises(pricing.OrderRefused) as refused:
        new_with(doctor, gone)
    assert refused.value.reason == pricing.VOUCHER_UNKNOWN == 'voucher_unknown'
    assert not Order.objects.exists() and not VoucherRedemption.objects.exists()


def new_with(user, voucher_row):
    return service.create_order(user, [resolver.OrderLine(resolver.product(TPS))],
                                actor=actor(user), voucher=voucher_row)


def test_the_current_snapshot_row_is_checked_not_the_one_passed(loaded, doctor, admin):
    stale = code(TRY_ME)
    pull(admin, 3, catalogue(), vouchers=[voucher(TRY_ME, CODES[TRY_ME], active=False)])
    with pytest.raises(pricing.OrderRefused) as refused:
        new_with(doctor, stale)
    assert refused.value.reason == pricing.VOUCHER_NOT_USABLE


def test_the_pricing_checks_still_refuse_wrong_currency_or_doctor(loaded, doctor, admin):
    pull(admin, 3, catalogue(), vouchers=[
        voucher(TRY_ME, CODES[TRY_ME], currency='AED'),
        voucher(OTHER_TRY_ME, CODES[OTHER_TRY_ME],
                client='99999999-9999-4999-8999-999999999999')])
    with pytest.raises(pricing.OrderRefused) as refused:
        new(doctor)
    assert refused.value.reason == pricing.VOUCHER_CURRENCY
    with pytest.raises(pricing.OrderRefused) as refused:
        new(doctor, OTHER_TRY_ME)
    assert refused.value.reason == pricing.VOUCHER_DOCTOR
    assert not VoucherRedemption.objects.exists()


def test_the_orders_own_reservation_does_not_refuse_its_repricing(loaded, doctor, entity):
    order = new(doctor)
    chosen = service.choose_entity(order, entity, expected_version=order.version,
                                   actor=actor(doctor))
    assert chosen.state == 'checkout' and chosen.voucher_taken == Decimal('50.00')
    assert VoucherRedemption.objects.count() == 1 and len(sent()) == 1


# --- The constraints are the last word -------------------------------------------------------

def make(voucher_id, order_id, doctor_uid, state, kind='try_me'):
    import uuid
    return VoucherRedemption.objects.create(
        voucher_id=uuid.UUID(voucher_id), voucher_type=kind, doctor_uid=doctor_uid,
        order_id=order_id, state=state, amount=Decimal('1.00'), currency='EUR', version=1)


def test_the_database_refuses_a_second_live_use(loaded, doctor, other):
    import uuid
    make(TRY_ME, uuid.uuid4(), doctor.uid, 'reserved')
    with pytest.raises(IntegrityError), transaction.atomic():
        make(TRY_ME, uuid.uuid4(), doctor.uid, 'consumed')
    make(TRY_ME, uuid.uuid4(), other.uid, 'reserved')  # another doctor
    make(TRY_ME, uuid.uuid4(), doctor.uid, 'released')  # history never counts
    make(SUBSCRIPTION, uuid.uuid4(), doctor.uid, 'consumed', 'subscription')
    with pytest.raises(IntegrityError), transaction.atomic():
        make(SUBSCRIPTION, uuid.uuid4(), other.uid, 'reserved', 'subscription')
    order_id = uuid.uuid4()
    make(OTHER_TRY_ME, order_id, doctor.uid, 'reserved')
    with pytest.raises(IntegrityError), transaction.atomic():
        make(OTHER_TRY_ME, order_id, other.uid, 'released')  # one row per order


def test_a_racing_reservation_fails_as_a_hook_failure_and_creates_no_order(
        loaded, doctor, monkeypatch):
    """Both orders passed the use check; the second one's reservation hits the constraint."""
    first = new(doctor)
    monkeypatch.setattr(vouchers, 'check_use', lambda voucher, doctor_uid: None)
    with pytest.raises(service.HookFailed):
        new(doctor)
    assert Order.objects.count() == 1 and VoucherRedemption.objects.count() == 1
    assert redemption(first).state == 'reserved' and len(sent()) == 1


@pytest.mark.django_db(transaction=True)
def test_two_orders_reserving_at_once_one_wins(loaded, doctor, entity, fake_on):
    if connection.vendor != 'postgresql':
        pytest.skip('a real race needs PostgreSQL')
    outcomes, barrier = [], threading.Barrier(2)

    def run():
        try:
            barrier.wait()
            new(doctor)
            outcomes.append('order')
        except (service.HookFailed, pricing.OrderRefused) as failure:
            outcomes.append(type(failure).__name__)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count('order') == 1
    assert Order.objects.count() == 1 and VoucherRedemption.objects.count() == 1


# --- A payment that must not fail ----------------------------------------------------------

def test_an_order_created_before_redemptions_is_consumed_when_it_is_paid(loaded, doctor):
    order = new(doctor)
    VoucherRedemption.objects.all().delete()  # as if created before this release
    OutboxEvent.objects.filter(type__startswith='voucher.').delete()
    paid = pay(order, doctor)
    assert paid.state == 'paid' and redemption(order).state == 'consumed'
    assert [(t, v, s) for t, v, s, _ in sent()] == [
        ('voucher.redeemed', 1, 'reserved'), ('voucher.redeemed', 2, 'consumed')]


def test_a_payment_still_goes_through_when_its_voucher_is_held_by_another_order(
        loaded, doctor, other, caplog):
    order = new(doctor)
    VoucherRedemption.objects.all().delete()
    new(other, OTHER_TRY_ME)
    VoucherRedemption.objects.filter(voucher_id=code(OTHER_TRY_ME).pk).update(
        voucher_id=code(TRY_ME).pk, doctor_uid=doctor.uid)  # held by another order
    paid = pay(order, doctor)
    assert paid.state == 'paid'
    assert VoucherRedemption.objects.filter(order_id=order.pk).count() == 0
    assert 'could not be redeemed' in caplog.text


# --- A code typed by a person ----------------------------------------------------------------

@pytest.mark.parametrize('typed', ['', None, '  ', 'NOPE23456X', 'not a code'])
def test_an_unknown_or_blank_code_is_refused_as_unknown(loaded, typed):
    with pytest.raises(pricing.OrderRefused) as refused:
        vouchers.voucher_for_code(typed)
    assert refused.value.reason == pricing.VOUCHER_UNKNOWN


def test_another_regions_code_is_not_in_this_snapshot(loaded, admin):
    # The admin site sends this region's vouchers only: a code of region ae never arrives.
    assert resolver.voucher_by_code('AEONLY2345') is None
    with pytest.raises(pricing.OrderRefused) as refused:
        vouchers.voucher_for_code('aeonl-y2345')
    assert refused.value.reason == pricing.VOUCHER_UNKNOWN


def test_a_known_code_gives_the_snapshot_voucher_in_any_form(loaded):
    expected = code(TRY_ME)
    assert vouchers.voucher_for_code('tryme-23456') == expected
    assert vouchers.voucher_for_code(CODES[TRY_ME]) == expected


def test_the_released_event_carries_the_stored_amount(loaded, doctor):
    order = new(doctor)
    cancel(order, doctor)
    released = OutboxEvent.objects.get(type='voucher.released').payload
    assert released['amount'] == {'amount': str(redemption(order).amount),
                                  'currency': redemption(order).currency}
