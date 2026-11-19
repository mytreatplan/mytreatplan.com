"""The order model and its service (epic-payments entry 1): one test (or more) per row of
the plan's matrix. The admin site is the fake of integration/tests/snapshot_rows.py (no
network)."""
import logging
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts import fiscal
from accounts.models import FiscalEntity
from catalogue import pricing, resolver
from integration.tests.snapshot_rows import GENERAL, TPS, pull, voucher
from payments import gateways, hooks, reference, service
from payments.models import AppendOnly, Order, OrderChange, OrderLine, Payment
from subscriptions.tests.helpers import ADVANCED, catalogue, doctor as make_doctor
from payments.tests.conftest import COMPANY

pytestmark = pytest.mark.django_db


def actor(user):
    return service.doctor_actor(user)


def line(product=TPS, **kwargs):
    return resolver.OrderLine(resolver.product(product), **kwargs)


def new(user, **kwargs):
    return service.create_order(user, [line()], actor=actor(user), **kwargs)


def pay(order, user, gateway='fake'):
    order = service.start_payment(order, gateway, expected_version=order.version,
                                  actor=actor(user))
    return service.mark_paid(order, expected_version=order.version,
                             actor=service.SYSTEM_ACTOR)


# --- Creating ------------------------------------------------------------------------------

def test_an_order_copies_what_the_pricing_gave(doctor, entity, fake_on, events):
    order = new(doctor)
    priced = service.subscriptions_service.price_order_for(
        doctor, [line()], vies=fiscal.vies_status(entity))
    assert (order.state, order.version, order.kind) == ('checkout', 1, 'service')
    assert order.total == priced.total.amount == Decimal('100.00')
    assert (order.net_total, order.vat_total, order.currency) == (
        priced.net_total.amount, priced.vat_total.amount, 'EUR')
    assert order.company_code == 'ES' and order.company_legal_name == 'MyTPDSO Spain S.L.'
    assert order.gateways == ['redsys', 'bank_transfer']
    row = order.lines.get()
    assert (row.product_id, row.code, row.name, row.quantity) == (
        resolver.product(TPS).pk, 'TPS-BR', 'TPS branded', 1)
    assert (row.list_amount, row.discount_percent, row.net, row.vat_percent) == (
        Decimal('100.00'), Decimal('0.00'), Decimal('100.00'), Decimal('0.00'))
    assert row.vat_text == 'Exempt from VAT (art. 20 LIVA).'
    assert row.provider_cost == Decimal('0.00') and row.cost_complete is not None
    assert order.vat_groups[0]['text'] == row.vat_text
    assert events == [('created', order.pk, 'checkout')]
    change = order.changes.get()
    assert (change.event, change.from_state, change.to_state, change.version,
            change.actor_type, change.actor_uuid) == (
        'created', '', 'checkout', 1, 'doctor', doctor.uid)


def test_the_fiscal_entity_is_copied_and_encrypted_at_rest(doctor, entity, fake_on):
    from django.db import connection
    order = new(doctor)
    assert order.fiscal_entity_id == entity.pk and order.bill_entity_id == entity.pk
    assert (order.bill_kind, order.bill_legal_name, order.bill_tax_id, order.bill_country,
            order.bill_entity_version, order.bill_vies_status) == (
        'company', 'Clínica Espuny S.L.', 'B12345674', 'ES', entity.version,
        entity.vies_status)
    with connection.cursor() as cursor:
        cursor.execute('SELECT bill_legal_name, bill_tax_id, bill_address_line1 '
                       'FROM payments_order')
        raw = ' '.join(str(value) for value in cursor.fetchone())
    assert 'Espuny' not in raw and 'B12345674' not in raw and 'Calle Mayor' not in raw


def test_the_reference_is_two_letters_six_digits_and_unique(doctor, entity, fake_on, monkeypatch):
    first = new(doctor)
    assert reference.normalise(first.reference) == first.reference
    assert len(first.reference) == 9 and first.reference[2] == '-'
    assert not set(first.reference[:2]) & {'I', 'O'}
    # A collision is redrawn.
    draws = iter([first.reference, first.reference, 'KM-482915'])
    monkeypatch.setattr(reference, 'draw', lambda: next(draws))
    second = new(doctor)
    assert second.reference == 'KM-482915'
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=second.pk).update(reference=first.reference)


@pytest.mark.parametrize('typed', ['km-482915', 'KM482915', ' km 482915 ', 'Km-482915'])
def test_a_typed_reference_is_accepted_with_or_without_the_dash_in_any_case(typed):
    assert reference.normalise(typed) == 'KM-482915'


@pytest.mark.parametrize('typed', ['', None, 'IO-482915', 'KM-48291', 'K-4829151', 'KM_482915'])
def test_anything_else_is_not_a_reference(typed):
    assert reference.normalise(typed) is None


def test_an_order_is_unchanged_when_the_catalogue_price_changes(doctor, entity, fake_on, admin):
    order = new(doctor)
    section = catalogue()
    for row in section['products']:
        if row['id'] == TPS:
            row['net_price']['amount'] = '999.00'
    assert pull(admin, 2, section) == ('applied', 2)
    assert resolver.product(TPS).net_amount == Decimal('999.00')
    fresh = Order.objects.get(pk=order.pk)
    assert fresh.total == Decimal('100.00') and fresh.lines.get().net == Decimal('100.00')


def test_the_entity_is_marked_used_when_the_order_is_frozen(doctor, entity, fake_on):
    order = new(doctor)
    assert FiscalEntity.objects.get(pk=entity.pk).used_at is None
    service.start_payment(order, 'fake', expected_version=1, actor=actor(doctor))
    assert FiscalEntity.objects.get(pk=entity.pk).used_at is not None


def test_no_usable_entity_blocks_payment_and_the_order_stays_in_checkout(doctor, fake_on):
    order = new(doctor)
    assert order.fiscal_entity_id is None and order.bill_vies_status == ''
    with pytest.raises(service.NoEntity):
        service.start_payment(order, 'fake', expected_version=1, actor=actor(doctor))
    assert Order.objects.get(pk=order.pk).state == 'checkout'


def test_a_refused_country_entity_is_not_usable(doctor, fake_on):
    fiscal.create(doctor, {**COMPANY, 'country': 'PT', 'tax_id': 'PT501964843',
                           'postal_code': '1100-053'})
    from integration.tests.snapshot_rows import put_country
    put_country('PT', 'banned')
    order = new(doctor)
    assert order.fiscal_entity_id is None


def test_a_foreign_entity_is_refused(doctor, fake_on):
    other = make_doctor('other@example.com')
    foreign = fiscal.create(other, dict(COMPANY))
    with pytest.raises(service.EntityRefused):
        new(doctor, entity=foreign)
    assert not Order.objects.exists()


def test_the_default_entity_is_used_when_none_is_given(doctor, entity, fake_on):
    second = fiscal.create(doctor, {**COMPANY, 'legal_name': 'Otra S.L.'})
    assert new(doctor).fiscal_entity_id == entity.pk
    assert new(doctor, entity=second).fiscal_entity_id == second.pk


def test_a_vies_valid_german_company_pays_reverse_charge(doctor, catalogue_loaded, fake_on):
    german = make_doctor('hans@example.com', country='DE')
    first = fiscal.create(german, {**COMPANY, 'country': 'DE', 'tax_id': 'DE136695976',
                                   'legal_name': 'Erste GmbH'})
    FiscalEntity.objects.filter(pk=first.pk).update(vies_status='invalid')
    second = fiscal.create(german, {**COMPANY, 'country': 'DE', 'tax_id': 'DE136695976',
                                    'legal_name': 'Zweite GmbH'})
    assert FiscalEntity.objects.get(pk=second.pk).vies_status == 'valid'
    order = new(german, entity=FiscalEntity.objects.get(pk=first.pk))
    row = order.lines.get()
    assert (order.company_code, row.vat_percent) == ('IE', Decimal('23.00'))
    assert order.total == Decimal('123.00') and order.bill_vies_status == 'invalid'
    order = service.choose_entity(order, second, expected_version=1, actor=actor(german))
    row = order.lines.get()
    assert (order.version, order.bill_vies_status, order.bill_legal_name) == (
        2, 'valid', 'Zweite GmbH')
    assert row.vat_percent == Decimal('0.00')
    assert row.vat_text == 'Reverse charge (art. 196 Directive 2006/112/EC).'
    assert order.total == Decimal('100.00') and order.vat_total == Decimal('0.00')
    assert order.changes.latest('version').event == 'entity_chosen'


def test_the_entity_changes_only_in_checkout(doctor, entity, fake_on):
    order = service.start_payment(new(doctor), 'fake', expected_version=1,
                                  actor=actor(doctor))
    with pytest.raises(service.InvalidTransition):
        service.choose_entity(order, entity, expected_version=order.version,
                              actor=actor(doctor))


def test_an_entity_edited_after_it_was_chosen_must_be_chosen_again(doctor, entity, fake_on):
    order = new(doctor)
    fiscal.update(doctor, entity.pk, {'city': 'Getafe'}, version=entity.version)
    with pytest.raises(service.EntityChanged):
        service.start_payment(order, 'fake', expected_version=1, actor=actor(doctor))
    order = service.choose_entity(order, entity, expected_version=1, actor=actor(doctor))
    assert order.bill_city == 'Getafe'
    service.start_payment(order, 'fake', expected_version=order.version, actor=actor(doctor))


# --- Gateways and pricing refusals -------------------------------------------------------------

def test_a_gateway_that_is_not_offered_is_refused(doctor, entity, fake_on):
    order = new(doctor)
    for gateway in ('redsys', 'nonsense', ''):  # redsys is priced but has no adapter yet
        with pytest.raises(service.GatewayRefused):
            service.start_payment(order, gateway, expected_version=1, actor=actor(doctor))
    assert Order.objects.get(pk=order.pk).state == 'checkout'


def test_a_priced_gateway_with_an_adapter_is_offered(doctor, entity, settings):
    gateways.register('redsys', lambda order, request: '/x/')
    try:
        order = new(doctor)
        assert gateways.offered(order) == ['redsys']
        service.start_payment(order, 'redsys', expected_version=1, actor=actor(doctor))
    finally:
        gateways.unregister('redsys')


def test_without_a_payment_method_no_order_is_started(doctor, entity):
    with pytest.raises(service.NoPaymentMethod):  # fake gateway off, redsys has no adapter
        new(doctor)
    assert not Order.objects.exists()


def test_the_fake_gateway_is_not_offered_when_its_flag_is_off(doctor, entity, fake_on):
    order = new(doctor)
    assert gateways.offered(order) == ['fake']
    fake_on.PAYMENTS_FAKE_GATEWAY = False
    assert gateways.offered(order) == []


def test_an_ae_order_has_no_payment_method_without_the_fake_gateway(
        catalogue_loaded, ae_portal, settings):
    from integration.tests.snapshot_rows import TPS_AED
    user = make_doctor('uae@example.com', country='AE')
    with pytest.raises(service.NoPaymentMethod):
        service.create_order(user, [line(TPS_AED)], actor=actor(user))
    assert not Order.objects.exists()
    settings.PAYMENTS_FAKE_GATEWAY = True
    settings.ROBOTS_NOINDEX = True  # a staging site
    assert service.create_order(user, [line(TPS_AED)], actor=actor(user)).gateways == []


def test_a_pricing_refusal_creates_no_order(doctor, entity, fake_on):
    from integration.tests.snapshot_rows import TPS_USD
    with pytest.raises(pricing.OrderRefused) as refused:
        service.create_order(doctor, [line(TPS), line(TPS_USD)], actor=actor(doctor))
    assert refused.value.reason == pricing.PRODUCT_NOT_OFFERED
    assert not Order.objects.exists()


# --- Moving ------------------------------------------------------------------------------------

def test_pay_then_paid_fires_the_hooks_once_each(doctor, entity, fake_on, events):
    order = new(doctor)
    order = service.start_payment(order, 'fake', expected_version=1, actor=actor(doctor))
    assert (order.state, order.gateway, order.version) == ('pending_payment', 'fake', 2)
    paid = service.mark_paid(order, expected_version=2, actor=service.SYSTEM_ACTOR)
    assert (paid.state, paid.version, paid.paid_at is not None) == ('paid', 3, True)
    assert [event for event, *_ in events] == ['created', 'paid']
    assert [(c.event, c.from_state, c.to_state, c.version, c.actor_type)
            for c in order.changes.all()] == [
        ('created', '', 'checkout', 1, 'doctor'),
        ('payment_started', 'checkout', 'pending_payment', 2, 'doctor'),
        ('paid', 'pending_payment', 'paid', 3, 'system')]


def test_a_replayed_paid_changes_and_fires_nothing(doctor, entity, fake_on, events):
    order = pay(new(doctor), doctor)
    again = service.mark_paid(order, expected_version=1, actor=service.SYSTEM_ACTOR)
    assert again.version == order.version == 3 and again.state == 'paid'
    assert [event for event, *_ in events] == ['created', 'paid']
    assert order.changes.count() == 3
    # A second Pay on a paid order is refused.
    with pytest.raises(service.InvalidTransition):
        service.start_payment(order, 'fake', expected_version=3, actor=actor(doctor))


def test_a_stale_version_changes_nothing_and_fires_nothing(doctor, entity, fake_on, events):
    order = new(doctor)
    service.start_payment(order, 'fake', expected_version=1, actor=actor(doctor))
    with pytest.raises(service.StaleVersion) as stale:
        service.mark_paid(order, expected_version=1, actor=service.SYSTEM_ACTOR)
    assert stale.value.current == 2
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    assert [event for event, *_ in events] == ['created']


def test_a_paid_hook_that_raises_rolls_the_move_back(doctor, entity, fake_on, caplog):
    order = service.start_payment(new(doctor), 'fake', expected_version=1,
                                  actor=actor(doctor))

    def boom(order):
        raise RuntimeError('receiver bug')

    hooks.register('paid', 'test_boom', boom)
    try:
        with caplog.at_level(logging.ERROR), pytest.raises(service.HookFailed):
            service.mark_paid(order, expected_version=2, actor=service.SYSTEM_ACTOR)
    finally:
        hooks.unregister('paid', 'test_boom')
    fresh = Order.objects.get(pk=order.pk)
    assert (fresh.state, fresh.version, fresh.paid_at) == ('pending_payment', 2, None)
    assert fresh.changes.count() == 2
    assert str(order.pk) in caplog.text and 'paid hook failed' in caplog.text


@pytest.mark.parametrize('first', ['checkout', 'pending_payment'])
def test_cancel_and_expire_an_unpaid_order(doctor, entity, fake_on, events, first):
    order = new(doctor)
    if first == 'pending_payment':
        order = service.start_payment(order, 'fake', expected_version=1, actor=actor(doctor))
        service.fail_payment(service.open_payment(order))  # no attempt open: may cancel
    cancelled = service.cancel(order, expected_version=order.version, actor=actor(doctor))
    assert cancelled.state == 'cancelled' and cancelled.cancelled_at is not None
    assert service.cancel(order, expected_version=0, actor=actor(doctor)).state == 'cancelled'
    assert [e for e, *_ in events].count('cancelled') == 1
    with pytest.raises(service.InvalidTransition):  # not from cancelled to expired
        service.expire(order, expected_version=cancelled.version)
    other = service.expire(new(doctor), expected_version=1)
    assert other.state == 'expired' and other.actor_type == 'system'
    assert [e for e, *_ in events].count('expired') == 1


def test_a_paid_order_cannot_be_cancelled_or_expired(doctor, entity, fake_on):
    order = pay(new(doctor), doctor)
    with pytest.raises(service.InvalidTransition):
        service.cancel(order, expected_version=order.version, actor=actor(doctor))
    with pytest.raises(service.InvalidTransition):
        service.expire(order, expected_version=order.version)
    assert Order.objects.get(pk=order.pk).state == 'paid'


def test_the_expiry_job_expires_only_the_orders_older_than_24_hours(
        doctor, entity, fake_on, events):
    old = service.start_payment(new(doctor), 'fake', expected_version=1, actor=actor(doctor))
    recent = new(doctor)
    done = pay(new(doctor), doctor)
    checkout_old = new(doctor)
    now = timezone.now()
    Order.objects.filter(pk__in=[old.pk, done.pk, checkout_old.pk]).update(
        created_at=now - timedelta(hours=25))
    Order.objects.filter(pk=old.pk).update(pending_at=now - timedelta(hours=25))
    Payment.objects.filter(order=old).update(session_expires_at=now - timedelta(hours=24))
    # Created 30 h ago but frozen 1 h ago: the doctor may be on the gateway page.
    fresh_pending = service.start_payment(new(doctor), 'fake', expected_version=1,
                                          actor=actor(doctor))
    Order.objects.filter(pk=fresh_pending.pk).update(
        created_at=now - timedelta(hours=30), pending_at=now - timedelta(hours=1))
    Order.objects.filter(pk=recent.pk).update(created_at=now - timedelta(hours=23))
    assert service.expire_stale(now) == 2
    states = dict(Order.objects.values_list('pk', 'state'))
    assert states[old.pk] == states[checkout_old.pk] == 'expired'
    assert states[recent.pk] == 'checkout' and states[done.pk] == 'paid'
    assert states[fresh_pending.pk] == 'pending_payment'
    assert [e for e, *_ in events].count('expired') == 2
    assert service.expire_stale(now) == 0


def test_the_expire_orders_command_prints_a_count_only(doctor, entity, fake_on, capsys):
    from django.core.management import call_command
    order = new(doctor)
    Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=30))
    call_command('expire_orders')
    out = capsys.readouterr().out
    assert 'expire_orders: 1 order(s) expired' in out and order.reference not in out


def test_the_system_has_no_uuid_and_a_doctor_has_one(doctor, entity, fake_on):
    order = new(doctor)
    with pytest.raises(service.InvalidActor):
        service.cancel(order, expected_version=1, actor=service.Actor('system', doctor.uid))
    with pytest.raises(service.InvalidActor):
        service.cancel(order, expected_version=1, actor=service.Actor('doctor'))
    with pytest.raises(service.InvalidActor):
        service.cancel(order, expected_version=1, actor='me')


def test_the_change_rows_are_append_only(doctor, entity, fake_on):
    change = new(doctor).changes.get()
    change.event = 'x'
    with pytest.raises(AppendOnly):
        change.save()
    with pytest.raises(AppendOnly):
        change.delete()
    with pytest.raises(AppendOnly):
        OrderChange.objects.update(event='x')


# --- A 0.00 order ------------------------------------------------------------------------------

def test_an_order_covered_by_a_voucher_is_paid_at_creation(
        doctor, entity, admin, events):
    section = catalogue()
    assert pull(admin, 2, section, vouchers=[voucher(GENERAL['id'], 'K7QM2XH9TR',
                                                     amount='100.00')]) == ('applied', 2)
    order = new(doctor, voucher=resolver.voucher_by_code('K7QM2XH9TR'))
    assert (order.state, order.total, order.gateway, order.voucher_id) == (
        'paid', Decimal('0.00'), '', resolver.voucher_by_code('K7QM2XH9TR').pk)
    assert order.voucher_taken == Decimal('100.00')
    assert [e for e, *_ in events] == ['created', 'paid']
    assert FiscalEntity.objects.get(pk=entity.pk).used_at is not None
    assert [c.event for c in order.changes.all()] == ['created', 'paid']


# --- A subscription purchase -------------------------------------------------------------------

def test_a_subscription_purchase_is_one_line_with_its_period(doctor, entity, fake_on):
    subscription = resolver.subscription_by_id(ADVANCED)
    order = service.create_subscription_order(doctor, subscription, 'upfront',
                                              actor=actor(doctor))
    row = order.lines.get()
    assert order.kind == 'subscription' and order.lines.count() == 1
    assert (row.subscription_id, row.period, row.product_id, row.code) == (
        subscription.pk, 'upfront', None, 'ADV-IB')
    assert order.company_code == 'ES' and row.net == Decimal('600.00')
    assert (row.vat_percent, order.total) == (Decimal('21.00'), Decimal('726.00'))
    assert order.gateways == ['redsys']
    same = service.choose_entity(order, entity, expected_version=1, actor=actor(doctor))
    assert same.lines.get().period == 'upfront' and same.total == Decimal('726.00')


# --- Review fixes ----------------------------------------------------------------------------

def test_a_retry_from_pending_payment_uses_the_frozen_snapshot(doctor, entity, fake_on):
    order = service.start_payment(new(doctor), 'fake', expected_version=1,
                                  actor=actor(doctor))
    FiscalEntity.objects.filter(pk=entity.pk).update(archived_at=timezone.now())
    again = service.start_payment(order, 'fake', expected_version=order.version,
                                  actor=actor(doctor))
    assert again.state == 'pending_payment' and again.version == 3
    FiscalEntity.objects.filter(pk=entity.pk).delete()  # erased: the link goes to NULL
    final = service.start_payment(again, 'fake', expected_version=3, actor=actor(doctor))
    assert final.fiscal_entity_id is None and final.bill_legal_name == 'Clínica Espuny S.L.'


def test_a_withdrawn_add_on_or_voucher_refuses_the_re_pricing(doctor, entity, fake_on):
    order = new(doctor)
    OrderLine.objects.filter(order=order).update(
        add_ons=[{'id': '11111111-1111-4111-8111-111111111111', 'code': 'X', 'name': 'X',
                  'percent': None, 'amount': None}])
    with pytest.raises(pricing.OrderRefused) as refused:
        service.choose_entity(order, entity, expected_version=1, actor=actor(doctor))
    assert refused.value.reason == pricing.ADD_ON_REFUSED
    OrderLine.objects.filter(order=order).update(add_ons=[])
    Order.objects.filter(pk=order.pk).update(
        voucher_id='22222222-2222-4222-8222-222222222222')
    with pytest.raises(pricing.OrderRefused) as refused:
        service.choose_entity(order, entity, expected_version=1, actor=actor(doctor))
    assert refused.value.reason == pricing.VOUCHER_NOT_USABLE
    assert Order.objects.get(pk=order.pk).version == 1


def test_choosing_an_entity_for_an_order_of_an_erased_account_is_refused(
        doctor, entity, fake_on):
    order = new(doctor)
    Order.objects.filter(pk=order.pk).update(user=None)
    with pytest.raises(service.InvalidTransition):
        service.choose_entity(order, entity, expected_version=1, actor=actor(doctor))


def test_an_order_with_lines_and_changes_cannot_be_deleted(doctor, entity, fake_on):
    from django.db.models import ProtectedError
    order = new(doctor)
    with pytest.raises(ProtectedError), transaction.atomic():
        order.delete()
    with pytest.raises(AppendOnly):
        OrderChange.objects.filter(order=order).delete()
    assert order.lines.exists() and order.changes.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_mark_paid_fires_the_hook_once(doctor, entity, fake_on, events):
    import threading
    from django.db import connection, connections
    if connection.vendor != 'postgresql':
        pytest.skip('row locks need PostgreSQL')
    order = service.start_payment(new(doctor), 'fake', expected_version=1,
                                  actor=actor(doctor))
    outcomes, barrier = [], threading.Barrier(2)

    def run():
        try:
            barrier.wait()
            outcomes.append(service.mark_paid(order, expected_version=2,
                                              actor=service.SYSTEM_ACTOR).state)
        except service.StaleVersion:
            outcomes.append('stale')
        finally:
            connections.close_all()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [e for e, *_ in events].count('paid') == 1
    assert sorted(outcomes) in (['paid', 'paid'], ['paid', 'stale'])
    assert Order.objects.get(pk=order.pk).version == 3
