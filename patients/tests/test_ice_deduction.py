"""The ICE deduction (epic-ordering 9.7): one test (or more) per row of the plan's matrix, on
`patients.ice`, the cart and the order hooks. The ICE costs 80.00 EUR here (the Advanced
doctor pays the TPS branded medium 100.00 less 6 %: 94.00); Iberia is billed by the Spanish
company, exempt. The admin site is the fake of integration/tests/snapshot_rows.py."""
import logging
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from cart import service as cart
from cart.models import CartItem
from cart.tests.test_checkout import (CODE, actor, browser, by_transfer, finish, load, pay,
                                      ready, third_patient, tps)  # noqa: F401 (fixture)
from catalogue import pricing
from integration.tests.snapshot_rows import IRELAND
from mytp_core.money import Money
from patients import ice
from patients.models import IceDeduction, WorkOrder
from patients.tests.conftest import ICE
from payments import service as payments
from payments.models import Order, OrderLine

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]
FINISH = '/en/cart/finish/'


def eighty(section):
    for row in section['products']:
        if row['id'] == ICE:
            row['net_price']['amount'] = '80.00'


@pytest.fixture
def doc(ready, admin):
    """The Advanced doctor of the cart tests (two patients, an address, the vouchers), the
    ICE at 80.00 EUR."""
    load(admin, eighty, version=4)
    return ready


def paid_ice(doctor, patient):
    """A paid ICE order of the patient (through the cart); its ICE line."""
    cart.add_tps(doctor, patient, maker='undecided', size='ice')
    order = pay(finish(doctor), doctor)
    assert order.state == 'paid'
    return order.lines.get()


def planning(order):
    return order.lines.get()


def free(patient):
    """Cancel the patient's live work order (since 9.8 a paid planning has one, and the
    ICE's is closed), so another planning may be ordered."""
    from patients import service as patients_service
    live = patients_service.live_work_order(patient)
    patients_service.move_work_order(live, 'cancelled', live.version,
                                     patients_service.SYSTEM_ACTOR)


def deduction(order):
    return IceDeduction.objects.get(order_id=order.pk)


# --- The matrix ---------------------------------------------------------------------------

def test_happy_a_paid_ice_comes_off_the_patient_s_tps(doc, patient):
    ice_line = paid_ice(doc, patient)
    assert ice_line.net == Decimal('80.00')
    tps(doc, patient)
    order = finish(doc)
    line = planning(order)
    assert (line.ice, line.ice_asked, line.net) == (
        Decimal('80.00'), Decimal('80.00'), Decimal('14.00'))
    assert order.ice_total == Decimal('80.00')
    assert order.total == order.net_total + order.vat_total == Decimal('14.00')
    found = deduction(order)
    assert (found.state, found.ice_line_id, found.order_line_id, found.amount,
            found.currency, found.patient_code, found.doctor_uid) == (
        'reserved', ice_line.pk, line.pk, Decimal('80.00'), 'EUR', patient.code, doc.uid)


def test_acceptance_paid_with_the_fake_gateway_and_no_second_credit(doc, patient):
    paid_ice(doc, patient)
    tps(doc, patient)
    order = pay(finish(doc), doc)
    assert order.state == 'paid' and order.total == Decimal('14.00')
    assert deduction(order).state == 'consumed'
    assert not CartItem.objects.exists()
    # 9.8: the ICE's work order is closed and the planning's is live.
    assert sorted(WorkOrder.objects.values_list('stage', flat=True)) == [
        'closed', 'pending_assignment']
    free(patient)
    tps(doc, patient)  # a later TPS for the patient
    later = finish(doc)
    assert (planning(later).ice, planning(later).ice_asked) == (Decimal('0.00'), None)
    assert later.total == Decimal('94.00')
    assert not IceDeduction.objects.filter(order_id=later.pk).exists()


def test_two_pending_orders_the_database_refuses_the_second_deduction(doc, patient,
                                                                      monkeypatch):
    ice_line = paid_ice(doc, patient)
    tps(doc, patient)
    first = finish(doc)
    # A second order for the same ICE, as two racing requests would see it: the held ICE
    # still looks free, so only the constraint stops it.
    monkeypatch.setattr(cart, '_cancel_earlier', lambda doctor: None)
    monkeypatch.setattr(ice, '_ice_lines', lambda *args: [ice_line])
    with pytest.raises(cart.CartRefused) as caught:
        finish(doc)
    assert caught.value.code == cart.ICE_TAKEN
    assert Order.objects.filter(kind='service').exclude(pk=ice_line.order_id).get() == first
    assert deduction(first).state == 'reserved'
    with pytest.raises(IntegrityError), transaction.atomic():
        IceDeduction.objects.create(
            ice_line_id=ice_line.pk, order_id=uuid.uuid4(), order_line_id=uuid.uuid4(),
            doctor_uid=doc.uid, patient_code=patient.code, state='reserved',
            amount=Decimal('80.00'), currency='EUR')


def test_two_pending_orders_through_payments_fail_as_a_hook(doc, patient):
    ice_line = paid_ice(doc, patient)
    tps(doc, patient)
    first = finish(doc)
    lines = cart._order_lines(cart.items_of(doc))
    credited = [pricing.OrderLine(line.product, line.quantity, line.add_ons,
                                  Money(ice_line.net, 'EUR'), line.patient_code,
                                  line.maker, line.other_name, line.shipping)
                for line in lines]
    with pytest.raises(payments.HookFailed) as caught:
        payments.create_order(doc, credited, actor=actor(doc))
    assert isinstance(caught.value.__cause__, ice.IceTaken)
    assert Order.objects.filter(kind='service').count() == 2  # the ICE's and the first
    assert deduction(first).state == 'reserved'


def test_the_cart_shows_a_message_when_the_ice_is_taken(doc, patient, monkeypatch):
    ice_line = paid_ice(doc, patient)
    tps(doc, patient)
    finish(doc)
    monkeypatch.setattr(cart, '_cancel_earlier', lambda doctor: None)
    monkeypatch.setattr(ice, '_ice_lines', lambda *args: [ice_line])
    html = browser(doc).post(FINISH, {}).content.decode()
    assert 'held by another of your orders' in html and 'data-error' in html
    assert patient.code not in html.split('data-error', 1)[1].split('</', 1)[0]


@pytest.mark.parametrize('how', ['cancel', 'expire'])
def test_a_cancelled_or_expired_order_releases_the_ice(doc, patient, how):
    paid_ice(doc, patient)
    tps(doc, patient)
    first = finish(doc)
    if how == 'cancel':
        payments.cancel(first, expected_version=first.version, actor=actor(doc))
    else:
        payments.expire(first, expected_version=first.version)
    assert deduction(first).state == 'released'
    second = finish(doc)
    assert planning(second).ice == Decimal('80.00')
    assert deduction(second).state == 'reserved'


def test_finishing_again_moves_the_deduction_to_the_new_order(doc, patient):
    paid_ice(doc, patient)
    tps(doc, patient)
    first = finish(doc)
    second = finish(doc)
    first.refresh_from_db()
    assert first.state == 'cancelled'
    assert deduction(first).state == 'released'
    assert deduction(second).state == 'reserved'
    assert planning(second).ice == Decimal('80.00')


def test_a_refunded_ice_gives_no_credit(doc, patient):
    ice_line = paid_ice(doc, patient)
    Order.objects.filter(pk=ice_line.order_id).update(state=Order.REFUNDED)
    tps(doc, patient)
    order = finish(doc)
    assert planning(order).ice == Decimal('0.00') and order.total == Decimal('94.00')
    assert not IceDeduction.objects.exists()


def test_an_ice_refunded_while_held_changes_nothing(doc, patient):
    ice_line = paid_ice(doc, patient)
    tps(doc, patient)
    order = finish(doc)
    Order.objects.filter(pk=ice_line.order_id).update(state=Order.REFUNDED)
    assert pay(order, doc).state == 'paid'
    assert deduction(order).state == 'consumed'


@pytest.mark.parametrize('change', [{'company_code': 'OTHER'}, {'currency': 'USD'}])
def test_an_ice_of_another_company_or_currency_gives_no_credit(doc, patient, change):
    ice_line = paid_ice(doc, patient)
    Order.objects.filter(pk=ice_line.order_id).update(**change)
    tps(doc, patient)
    order = finish(doc)
    assert order.state == 'checkout' and planning(order).ice == Decimal('0.00')
    assert not IceDeduction.objects.exists()


def test_an_ice_billed_by_another_company_than_the_planning_gives_no_credit(doc, patient,
                                                                         admin):
    paid_ice(doc, patient)

    def irish(section):
        eighty(section)
        for row in section['billing_rules']:
            if row['category'] == 'tps_branded' and row['company'] != IRELAND \
                    and row['vat_percent'] == '0.00':
                row['company'] = IRELAND
    load(admin, irish, version=5)
    tps(doc, patient)
    order = finish(doc)
    assert order.company_code != Order.objects.get(
        pk=OrderLine.objects.get(code='ICE-1').order_id).company_code
    assert planning(order).ice == Decimal('0.00')


def test_an_ice_bigger_than_the_line_is_capped(doc, patient):
    paid_ice(doc, patient)
    tps(doc, patient, size='short')  # 80.00 less 6 %: 75.20
    order = finish(doc)
    line = planning(order)
    assert (line.ice_asked, line.ice, line.net) == (
        Decimal('80.00'), Decimal('75.20'), Decimal('0.00'))
    assert deduction(order).amount == Decimal('75.20')
    assert order.state == 'paid'  # 0.00: paid at once
    assert deduction(order).state == 'consumed'


def test_an_ice_already_followed_by_a_paid_planning_gives_no_credit(doc, patient,
                                                                    monkeypatch):
    # An ICE paid before 9.7, its first later planning paid in full (no deduction then).
    paid_ice(doc, patient)
    with monkeypatch.context() as patched:
        patched.setattr(ice, 'credited_lines', lambda doctor, lines, country: lines)
        tps(doc, patient)
        earlier = pay(finish(doc), doc)
    assert planning(earlier).ice == Decimal('0.00') and not IceDeduction.objects.exists()
    free(patient)
    tps(doc, patient)
    order = finish(doc)
    assert planning(order).ice == Decimal('0.00') and order.total == Decimal('94.00')
    assert not IceDeduction.objects.exists()
    # Refunded, that planning still used the ICE.
    payments.cancel(order, expected_version=order.version, actor=actor(doc))
    Order.objects.filter(pk=earlier.pk).update(state=Order.REFUNDED)
    assert planning(finish(doc)).ice == Decimal('0.00')


def test_the_credit_comes_off_before_vat_under_a_vat_charging_rule(doc, patient, admin):
    def vat(section):
        eighty(section)
        for row in section['billing_rules']:
            if row['category'] == 'tps_branded' and row['vat_percent'] == '0.00' \
                    and row['company'] != IRELAND:
                # As Iberia's subscription rule: the Spanish company, 21 %.
                row['vat_percent'] = '21.00'
                row.pop('exemption_text', None)
    paid_ice(doc, patient)
    load(admin, vat, version=5)
    tps(doc, patient)
    order = finish(doc)
    line = planning(order)
    assert (line.ice, line.net, line.vat_percent) == (
        Decimal('80.00'), Decimal('14.00'), Decimal('21.00'))
    assert (order.vat_total, order.total) == (Decimal('2.94'), Decimal('16.94'))
    # Without the credit: 94.00 + 19.74 VAT = 113.74, so it drops by 80.00 plus 16.80 VAT.
    assert Decimal('113.74') - order.total == Decimal('80.00') * Decimal('1.21')


def test_two_patients_each_take_their_own_ice(doc, patient):
    paid_ice(doc, patient)
    paid_ice(doc, doc.second)
    tps(doc, patient)
    tps(doc, doc.second, size='long')  # 120.00 less 6 %: 112.80
    order = finish(doc)
    assert [(line.patient_code, line.ice, line.net)
            for line in order.lines.order_by('position')] == [
        (patient.code, Decimal('80.00'), Decimal('14.00')),
        (doc.second.code, Decimal('80.00'), Decimal('32.80'))]
    assert IceDeduction.objects.filter(order_id=order.pk, state='reserved').count() == 2


def test_another_patient_s_ice_is_never_taken(doc, patient):
    paid_ice(doc, patient)
    tps(doc, doc.second)
    order = finish(doc)
    assert planning(order).ice == Decimal('0.00')


def test_the_oldest_ice_comes_first(doc, patient):
    first = paid_ice(doc, patient)
    WorkOrder.objects.all().delete()  # a second ICE needs no live work order
    second = paid_ice(doc, patient)
    Order.objects.filter(pk=second.order_id).update(paid_at=timezone.now() - timedelta(days=9))
    tps(doc, patient)
    order = finish(doc)
    assert deduction(order).ice_line_id == second.pk
    payments.cancel(order, expected_version=order.version, actor=actor(doc))
    Order.objects.filter(pk=second.order_id).update(state=Order.REFUNDED)
    assert deduction(finish(doc)).ice_line_id == first.pk


def test_a_try_me_voucher_is_spread_on_what_the_ice_leaves(doc, patient):
    paid_ice(doc, patient)
    tps(doc, patient, size='long')  # 112.80 - 80.00 = 32.80
    tps(doc, doc.second)  # 94.00
    order = finish(doc, CODE)
    first, second = order.lines.order_by('position')
    assert first.ice == Decimal('80.00') and second.ice == Decimal('0.00')
    assert first.voucher_share + second.voucher_share == Decimal('50.00')
    assert order.net_total == Decimal('32.80') + Decimal('94.00') - Decimal('50.00')


def test_a_voucher_on_an_order_the_ice_makes_free_is_refused(doc, patient):
    paid_ice(doc, patient)
    tps(doc, patient, size='short')  # the ICE covers it all
    with pytest.raises(pricing.OrderRefused) as caught:
        finish(doc, CODE)
    assert caught.value.reason == pricing.VOUCHER_NOTHING_TO_COVER
    assert not IceDeduction.objects.exists()


def test_choosing_the_entity_again_keeps_the_credit(doc, patient):
    paid_ice(doc, patient)
    tps(doc, patient)
    order = finish(doc)
    order = payments.choose_entity(order, order.fiscal_entity,
                                   expected_version=order.version, actor=actor(doc))
    line = planning(order)
    assert (line.ice, line.net) == (Decimal('80.00'), Decimal('14.00'))
    assert deduction(order).state == 'reserved'
    order = pay(order, doc)
    found = deduction(order)
    assert found.state == 'consumed' and found.order_line_id == line.pk


def test_a_transfer_approval_keeps_the_deduction_reserved(doc, patient):
    from catalogue.models import BillingCompany
    from cart.tests.test_checkout import BANK
    from integration.tests.snapshot_rows import snapshot_writes
    with snapshot_writes():
        BillingCompany.objects.update(**BANK)
    paid_ice(doc, patient)
    tps(doc, patient)
    order = by_transfer(finish(doc), doc)
    assert order.state == 'pending_payment'
    assert deduction(order).state == 'reserved'
    # 9.8: the item left the cart, and the patient's pending-payment work order keeps them
    # out of a new one while the ICE stays live.
    with pytest.raises(cart.CartRefused) as caught:
        tps(doc, patient)
    assert caught.value.code == cart.LIVE_WORK_ORDER
    assert sorted(WorkOrder.objects.values_list('stage', flat=True)) == [
        'pending_assignment', 'pending_payment']


def test_a_refunded_planning_order_keeps_its_deduction_consumed(doc, patient):
    paid_ice(doc, patient)
    tps(doc, patient)
    order = pay(finish(doc), doc)
    Order.objects.filter(pk=order.pk).update(state=Order.REFUNDED)
    assert deduction(order).state == 'consumed'
    free(patient)
    tps(doc, patient)
    assert planning(finish(doc)).ice == Decimal('0.00')


# --- The receivers ------------------------------------------------------------------------

def test_the_receivers_are_idempotent(doc, patient):
    paid_ice(doc, patient)
    tps(doc, patient)
    order = finish(doc)
    held = deduction(order)
    assert ice.reserve(order) == [held] and IceDeduction.objects.count() == 1
    order = pay(order, doc)
    ice.consume(order)
    ice.release(order)  # a consumed deduction is never released
    assert deduction(order).state == 'consumed' and IceDeduction.objects.count() == 1


def test_releasing_twice_changes_nothing(doc, patient):
    paid_ice(doc, patient)
    tps(doc, patient)
    order = finish(doc)
    payments.cancel(order, expected_version=order.version, actor=actor(doc))
    order.refresh_from_db()
    before = deduction(order).updated_at
    assert ice.release(order) == []
    assert deduction(order).updated_at == before


def test_an_order_without_a_credit_or_a_subscription_order_gets_no_deduction(doc, patient):
    tps(doc, patient)
    order = finish(doc)
    assert ice.reserve(order) == [] and ice.consume(order) == []
    Order.objects.filter(pk=order.pk).update(kind=Order.SUBSCRIPTION)
    order.refresh_from_db()
    OrderLine.objects.filter(order=order).update(ice_asked=Decimal('80.00'))
    assert ice.reserve(order) == []
    assert not IceDeduction.objects.exists()


def test_an_included_case_line_takes_no_deduction(doc, patient):
    paid_ice(doc, patient)
    tps(doc, patient)
    order = finish(doc)
    IceDeduction.objects.all().delete()
    OrderLine.objects.filter(order=order).update(included_case=True)
    assert ice.reserve(order) == []
    assert ice.available_credit(doc, patient.code, order.company_code, 'EUR') is not None


def test_a_paid_order_whose_deduction_is_missing_reserves_and_consumes_it(doc, patient):
    paid_ice(doc, patient)
    tps(doc, patient)
    order = finish(doc)
    IceDeduction.objects.all().delete()
    assert pay(order, doc).state == 'paid'
    assert deduction(order).state == 'consumed'


def test_a_paid_order_whose_ice_is_gone_is_still_paid_and_logged(doc, patient, caplog):
    ice_line = paid_ice(doc, patient)
    tps(doc, patient)
    order = finish(doc)
    IceDeduction.objects.all().delete()
    Order.objects.filter(pk=ice_line.order_id).update(state=Order.REFUNDED)
    with caplog.at_level(logging.ERROR, logger='patients.ice'):
        assert pay(order, doc).state == 'paid'
    assert not IceDeduction.objects.exists()
    assert str(order.pk) in caplog.text
    for name in ('Ana', 'Pérez'):
        assert name not in caplog.text


def test_an_ice_taken_by_another_order_fails_the_hook_and_logs_no_name(doc, patient, caplog):
    ice_line = paid_ice(doc, patient)
    tps(doc, patient)
    finish(doc)
    lines = [pricing.OrderLine(line.product, line.quantity, line.add_ons,
                               Money(ice_line.net, 'EUR'), line.patient_code,
                               line.maker, line.other_name, line.shipping)
             for line in cart._order_lines(cart.items_of(doc))]
    with caplog.at_level(logging.ERROR), pytest.raises(payments.HookFailed):
        payments.create_order(doc, lines, actor=actor(doc))
    for name in ('Ana', 'Pérez'):
        assert name not in caplog.text


def test_available_credit_needs_a_paid_ice_of_the_doctor(doc, patient, other_doctor):
    assert ice.available_credit(doc, patient.code, 'ANY', 'EUR') is None
    ice_line = paid_ice(doc, patient)
    company = Order.objects.get(pk=ice_line.order_id).company_code
    found, credit = ice.available_credit(doc, patient.code, company, 'EUR')
    assert found.pk == ice_line.pk and credit == Money(Decimal('80.00'), 'EUR')
    assert ice.available_credit(other_doctor, patient.code, company, 'EUR') is None
    assert ice.available_credit(doc, '', company, 'EUR') is None
