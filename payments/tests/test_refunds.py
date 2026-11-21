"""Refunds, credit notes and staff cancellation (epic-payments entry 7): one test (or more)
per row of the plan's matrix. The admin site is the fake of integration/tests/snapshot_rows.py
and Redsys is the fake `post_json` of test_redsys (no network)."""
import urllib.error
from datetime import datetime, timezone as utc
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from invoices.models import Invoice
from payments import fake, gateways, hooks, refunds, service, webhooks
from payments.models import Order, OrderChange, Payment, Refund
from payments.tests.test_redsys import FakeRedsys, paid as paid_redsys, redsys_on, sentry  # noqa: F401
from payments.tests.test_service import actor, new
from payments.tests.test_webhooks import attempt, last_log

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures('clear_cache', 'portal_urls')]

STAFF = service.admin_actor('00000000-0000-0000-0000-000000000042')


@pytest.fixture(autouse=True)
def fake_gateway(fake_on):
    return fake_on


def paid_fake(user):
    """A paid order whose fake payment is confirmed (as the webhook leaves it)."""
    order, payment = attempt(user, 'fake')
    fake.pay(payment)
    webhooks.process(last_log().pk)
    payment.refresh_from_db()
    assert payment.state == 'confirmed'
    return Order.objects.get(pk=order.pk)


def paid_transfer(user):
    from catalogue.models import BillingCompany
    from integration.tests.snapshot_rows import snapshot_writes
    with snapshot_writes():
        BillingCompany.objects.filter(code='ES').update(
            bank_account_holder='MyTPDSO Spain S.L.', iban='ES9121000418450200051332',
            bic='CAIXESBBXXX', bank_name='CaixaBank')
    order = new(user)
    order = service.start_payment(order, 'bank_transfer', expected_version=order.version,
                                  actor=actor(user))
    return service.mark_transfer_paid(order, Decimal('100.00'),
                                      expected_version=order.version, actor=STAFF)


def do_refund(order, actor_=STAFF, **kwargs):
    kwargs.setdefault('reason', 'cancellation')
    return refunds.refund(order, expected_version=Order.objects.get(pk=order.pk).version,
                          actor=actor_, **kwargs)


def invoice_of(order):
    return Invoice.objects.get(order_id=order.pk)


# --- A refund through a gateway ----------------------------------------------------------------

def test_a_paid_order_is_refunded_through_the_fake_gateway(doctor, entity, events):
    order = paid_fake(doctor)
    invoice = invoice_of(order)
    done = do_refund(order, reason='goodwill')
    note = done.credit_note
    year = datetime.now(ZoneInfo('Europe/Madrid')).year
    assert (done.state, done.manual, done.reason) == ('done', False, 'goodwill')
    assert done.gateway_refund_id.startswith('fake_refund_') and done.amount == order.total
    assert note.number == f'ES-EU-{year}-R000001' and note.kind == 'credit_note'
    assert (note.original_id, note.order_id, note.reason) == (invoice.pk, None, 'goodwill')
    assert note.net_total == -invoice.net_total and note.total == -invoice.total
    assert note.vat_total == -invoice.vat_total and note.currency == invoice.currency
    assert note.extensions == {'rectification_code': ''}
    fresh = Order.objects.get(pk=order.pk)
    assert (fresh.state, fresh.refunded_at is not None) == ('refunded', True)
    assert fresh.version == order.version + 1 and fresh.actor_type == 'admin'
    assert [e for e in events if e[0] == 'refunded'] == [('refunded', order.pk, 'refunded')]
    change = OrderChange.objects.filter(order=order).latest('version')
    assert (change.event, change.from_state, change.to_state) == ('refunded', 'paid', 'refunded')


def test_the_original_invoice_is_unchanged(doctor, entity):
    order = paid_fake(doctor)
    before = Invoice.objects.filter(pk=invoice_of(order).pk).values().get()
    do_refund(order)
    assert Invoice.objects.filter(pk=before['id']).values().get() == before
    assert Invoice.objects.filter(kind='order').count() == 1


def test_the_pending_row_is_committed_before_the_gateway_is_called(
        doctor, entity, monkeypatch):
    order = paid_fake(doctor)
    seen = []

    def spy(payment, amount):
        row = Refund.objects.get(order=order)
        seen.append((row.state, row.amount, row.reason, row.payment_id))
        return 'fake_refund_x'

    adapter = gateways.adapter('fake')
    monkeypatch.setitem(gateways._registry, 'fake', gateways.Adapter(
        **{**adapter.__dict__, 'refund': spy}))
    do_refund(order)
    assert seen[0][0] == 'pending' and seen[0][1:3] == (order.total, 'cancellation')


def test_a_redsys_refund_issues_the_credit_note(doctor, entity, redsys_on, monkeypatch):
    order, payment = paid_redsys(doctor)
    redsys_fake = FakeRedsys()
    monkeypatch.setattr('accounts.http.post_json', redsys_fake)
    done = do_refund(order)
    assert done.state == 'done' and done.payment_id == payment.pk
    assert done.gateway_refund_id == f'{payment.gateway_payment_id}:654321'
    assert done.credit_note.number.endswith('-R000001')
    assert Order.objects.get(pk=order.pk).state == 'refunded'
    assert len(redsys_fake.calls) == 1


# --- By hand -----------------------------------------------------------------------------------

def test_a_bank_transfer_order_is_refunded_by_hand_with_a_note(doctor, entity):
    order = paid_transfer(doctor)
    with pytest.raises(refunds.BadRefund):  # no gateway can refund a transfer
        do_refund(order)
    with pytest.raises(refunds.BadRefund, match='note'):
        do_refund(order, manual=True)
    assert not Refund.objects.exists()
    done = do_refund(order, manual=True, note='  Returned to the buyer\'s IBAN on 21 Nov ')
    assert (done.state, done.manual, done.payment_id, done.gateway_refund_id) == (
        'done', True, None, '')
    assert done.note == 'Returned to the buyer\'s IBAN on 21 Nov'
    assert done.credit_note.number.endswith('-R000001')
    assert Order.objects.get(pk=order.pk).state == 'refunded'


def test_a_free_order_gets_a_free_credit_note(doctor, entity, admin):
    from catalogue import resolver
    from integration.tests.snapshot_rows import GENERAL, pull, voucher
    from subscriptions.tests.helpers import catalogue
    assert pull(admin, 2, catalogue(), vouchers=[voucher(
        GENERAL['id'], 'K7QM2XH9TR', amount='100.00')]) == ('applied', 2)
    order = new(doctor, voucher=resolver.voucher_by_code('K7QM2XH9TR'))
    assert order.state == 'paid' and order.total == Decimal('0.00')
    done = do_refund(order, manual=True, note='Voucher order cancelled')
    note = done.credit_note
    assert note.total == Decimal('0.00') and str(note.total) == '0.00'
    assert note.voucher_taken == -Decimal('100.00')
    assert note.lines.get().list_amount == -Decimal('100.00')
    assert Order.objects.get(pk=order.pk).state == 'refunded'


# --- When it does not work ---------------------------------------------------------------------

def test_a_gateway_that_refuses_fails_the_refund(doctor, entity, redsys_on, monkeypatch):
    order, _payment = paid_redsys(doctor)
    monkeypatch.setattr('accounts.http.post_json', FakeRedsys(error='SIS0057'))
    with pytest.raises(refunds.RefundFailed):
        do_refund(order)
    row = Refund.objects.get()
    assert (row.state, row.credit_note_id) == ('failed', None)
    assert Order.objects.get(pk=order.pk).state == 'paid'
    assert Invoice.objects.filter(kind='credit_note').count() == 0
    monkeypatch.setattr('accounts.http.post_json', FakeRedsys())
    assert do_refund(order).state == 'done'  # staff may try again after a refusal


@pytest.mark.parametrize('answer', [
    FakeRedsys(fault=urllib.error.URLError('timed out')), FakeRedsys(fault=ValueError('x')),
    FakeRedsys(order='0000OTHER000'), FakeRedsys(Ds_Amount='100'),
])
def test_a_timeout_or_an_unknown_answer_needs_attention_and_blocks_a_second_refund(
        doctor, entity, redsys_on, monkeypatch, sentry, answer):
    order, _payment = paid_redsys(doctor)
    monkeypatch.setattr('accounts.http.post_json', answer)
    done = do_refund(order)
    assert done.state == 'needs_attention' and done.credit_note_id is None
    assert Order.objects.get(pk=order.pk).state == 'paid'
    assert not Invoice.objects.filter(kind='credit_note').exists()
    assert sentry == [f'payment refund has no usable gateway answer: order {order.pk}']
    monkeypatch.setattr('accounts.http.post_json', FakeRedsys())
    with pytest.raises(refunds.RefundPending):
        do_refund(order)
    with pytest.raises(refunds.RefundPending):
        do_refund(order, manual=True, note='done in the console')
    assert Refund.objects.count() == 1


def test_an_unexpected_fault_of_the_adapter_needs_attention(
        doctor, entity, monkeypatch, sentry):
    order = paid_fake(doctor)
    adapter = gateways.adapter('fake')

    def boom(payment, amount):
        raise RuntimeError('boom')

    monkeypatch.setitem(gateways._registry, 'fake', gateways.Adapter(
        **{**adapter.__dict__, 'refund': boom}))
    assert do_refund(order).state == 'needs_attention' and len(sentry) == 1


def test_a_settlement_that_fails_after_the_gateway_refunded_needs_attention(
        doctor, entity, sentry):
    order = paid_fake(doctor)

    def failing(_order):
        raise RuntimeError('receiver bug')

    hooks.register('refunded', 'test_failing', failing)
    try:
        with pytest.raises(service.HookFailed):
            do_refund(order)
    finally:
        hooks.unregister('refunded', 'test_failing')
    row = Refund.objects.get()
    assert row.state == 'needs_attention' and row.gateway_refund_id and len(sentry) == 1
    assert Order.objects.get(pk=order.pk).state == 'paid'  # rolled back: no credit note
    assert not Invoice.objects.filter(kind='credit_note').exists()


def test_a_manual_settlement_that_fails_is_failed_and_can_be_repeated(doctor, entity):
    order = paid_transfer(doctor)
    hooks.register('refunded', 'test_failing', lambda _order: 1 / 0)
    try:
        with pytest.raises(service.HookFailed):
            do_refund(order, manual=True, note='by hand')
    finally:
        hooks.unregister('refunded', 'test_failing')
    assert Refund.objects.get().state == 'failed'
    assert do_refund(order, manual=True, note='by hand').state == 'done'


# --- Repeats and refusals ----------------------------------------------------------------------

def test_a_second_refund_is_refused_while_one_is_pending_or_after_it_is_done(
        doctor, entity):
    order = paid_fake(doctor)
    Refund.objects.create(order=order, amount=order.total, currency=order.currency,
                          reason='error', actor_type='system')
    with pytest.raises(refunds.RefundPending):
        do_refund(order)
    Refund.objects.all().update(state='failed')
    assert do_refund(order).state == 'done'
    with pytest.raises(service.InvalidTransition):
        do_refund(order)
    assert Refund.objects.filter(state='done').count() == 1
    assert Invoice.objects.filter(kind='credit_note').count() == 1


def test_the_database_allows_one_pending_refund_per_order(doctor, entity):
    from django.db import IntegrityError, transaction
    order = paid_fake(doctor)
    fields = dict(order=order, amount=order.total, currency=order.currency, reason='error',
                  actor_type='system')
    Refund.objects.create(**fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        Refund.objects.create(**fields)
    Refund.objects.create(**{**fields, 'state': 'failed'})  # only pending is limited


def test_an_unpaid_order_cannot_be_refunded(doctor, entity):
    order = new(doctor)
    with pytest.raises(service.InvalidTransition):
        do_refund(order)
    assert not Refund.objects.exists()


def test_a_stale_version_a_bad_reason_and_a_long_note_change_nothing(doctor, entity):
    order = paid_fake(doctor)
    with pytest.raises(service.StaleVersion):
        refunds.refund(order, expected_version=order.version - 1, actor=STAFF,
                       reason='error')
    with pytest.raises(refunds.BadRefund):
        do_refund(order, reason='because')
    with pytest.raises(refunds.BadRefund):
        do_refund(order, manual=True, note='x' * 501)
    assert not Refund.objects.exists() and Order.objects.get(pk=order.pk).state == 'paid'


def test_a_doctor_cannot_refund_or_staff_cancel(doctor, entity):
    order = paid_fake(doctor)
    with pytest.raises(service.InvalidActor):
        do_refund(order, actor(doctor))
    unpaid = new(doctor)
    with pytest.raises(service.InvalidActor):
        refunds.staff_cancel(unpaid, expected_version=unpaid.version, actor=actor(doctor))
    with pytest.raises(service.InvalidActor):
        refunds.staff_cancel(unpaid, expected_version=unpaid.version,
                             actor=service.Actor('admin', None))
    assert not Refund.objects.exists() and Order.objects.get(pk=unpaid.pk).state == 'checkout'


def test_the_system_may_refund(doctor, entity):
    assert do_refund(paid_fake(doctor), service.SYSTEM_ACTOR).state == 'done'


def test_a_refund_inside_a_transaction_is_refused_before_anything_is_written(
        doctor, entity):
    from django.db import transaction
    order = paid_fake(doctor)
    with transaction.atomic(), pytest.raises(refunds.InsideTransaction):
        do_refund(order)
    assert not Refund.objects.exists() and Order.objects.get(pk=order.pk).state == 'paid'


def test_a_doctor_sees_a_refunded_order_on_the_return_and_checkout_pages(doctor, entity):
    from payments.tests.test_checkout import browser, html
    order = paid_fake(doctor)
    do_refund(order)
    client = browser(doctor)
    page = html(client.get(f'/en/checkout/{order.pk}/return/'))
    assert 'data-order-state="refunded"' in page and 'This order was refunded.' in page
    assert 'expired' not in page.lower().split('data-order-state')[1][:80]
    response = client.get(f'/en/checkout/{order.pk}/')
    assert response.status_code == 302 and response['Location'] == (
        f'/en/checkout/{order.pk}/return/')
    es = html(client.get(f'/es/checkout/{order.pk}/return/'))
    assert 'data-order-state="refunded"' in es


# --- Concurrency (PostgreSQL only) -------------------------------------------------------------

def test_three_refunds_at_once_number_their_credit_notes_with_no_gap(doctor, entity):
    import threading

    from django.db import connection, connections
    if connection.vendor != 'postgresql':
        pytest.skip('row locks need PostgreSQL')
    orders = [paid_fake(doctor) for _ in range(3)]
    barrier, errors = threading.Barrier(3), []

    def run(order):
        try:
            barrier.wait()
            do_refund(order)
        except Exception as failure:  # noqa: BLE001
            errors.append(failure)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=run, args=(order,)) for order in orders]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    numbers = sorted(Invoice.objects.filter(kind='credit_note').values_list('number', flat=True))
    year = Invoice.objects.filter(kind='credit_note').first().year
    assert numbers == [f'ES-EU-{year}-R00000{n}' for n in (1, 2, 3)]
    assert Order.objects.filter(state='refunded').count() == 3


# --- Staff cancel ------------------------------------------------------------------------------

def test_staff_cancel_an_unpaid_order(doctor, entity, events):
    order = new(doctor)
    done = refunds.staff_cancel(order, expected_version=order.version, actor=STAFF)
    assert (done.state, done.actor_type) == ('cancelled', 'admin')
    assert [e[0] for e in events if e[0] == 'cancelled'] == ['cancelled']


def test_staff_cancel_waits_for_a_live_card_session(doctor, entity):
    order, _payment = attempt(doctor, 'fake')
    order.refresh_from_db()  # the card session is live: staff wait for it
    with pytest.raises(service.AttemptOpen):
        refunds.staff_cancel(order, expected_version=order.version, actor=STAFF)
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'


def test_a_paid_order_cannot_be_cancelled(doctor, entity):
    order = paid_fake(doctor)
    with pytest.raises(service.InvalidTransition):
        refunds.staff_cancel(order, expected_version=order.version, actor=STAFF)
    with pytest.raises(service.InvalidTransition):
        service.cancel(order, expected_version=order.version, actor=STAFF)
    with pytest.raises(service.InvalidTransition):
        service.expire(order, expected_version=order.version)


# --- The year boundary -------------------------------------------------------------------------

def test_a_refund_after_midnight_in_madrid_is_numbered_in_the_new_year(
        doctor, entity, monkeypatch):
    order = paid_fake(doctor)
    moment = datetime(2026, 12, 31, 23, 30, tzinfo=utc.utc)  # 00:30 on 1 Jan in Madrid
    monkeypatch.setattr('invoices.service.timezone', SimpleNamespace(now=lambda: moment))
    note = do_refund(order).credit_note
    assert note.number == 'ES-EU-2027-R000001' and note.year == 2027
    assert note.issue_date.isoformat() == '2027-01-01' and note.issued_at == moment


def test_credit_notes_number_across_orders_in_their_own_series(doctor, entity):
    first, second = paid_fake(doctor), paid_fake(doctor)
    one, two = do_refund(first).credit_note, do_refund(second).credit_note
    assert [one.number[-7:], two.number[-7:]] == ['R000001', 'R000002']
    assert Invoice.objects.filter(kind='order').count() == 2  # invoices keep their own count
    assert sorted(Invoice.objects.filter(kind='order').values_list('number', flat=True)
                  )[-1].endswith('-000002')


# --- The Redsys refund notification ------------------------------------------------------------

def test_a_refund_notification_is_marked_done_without_a_retry(
        doctor, entity, redsys_on, monkeypatch):
    from payments.tests.test_redsys import notify
    order, payment = paid_redsys(doctor)
    notify(payment, response='0900', transaction_type='3')
    row = last_log()
    assert row.result == 'accepted' and row.status == ''
    assert webhooks.process(row.pk) == webhooks.REFUND_NOTICE
    row.refresh_from_db()
    assert row.processed_at is not None and row.attempts == 0 and row.next_try_at is None
    assert webhooks.process(row.pk) is None  # done: not looked at again
    assert Order.objects.get(pk=order.pk).state == 'paid'


# --- The commands ------------------------------------------------------------------------------

def run(*args):
    out = StringIO()
    call_command(*args, stdout=out, stderr=out)
    return out.getvalue()


def test_refund_order_refunds_and_prints_the_credit_note(doctor, entity):
    order = paid_fake(doctor)
    printed = run('refund_order', order.reference, '--reason', 'error')
    note = Invoice.objects.get(kind='credit_note')
    assert printed.strip() == f'refund done for order {order.reference}: credit note {note.number}'
    refund = Refund.objects.get()
    assert (refund.actor_type, refund.actor_uuid) == ('system', None)
    with pytest.raises(CommandError, match='InvalidTransition'):
        run('refund_order', order.reference, '--reason', 'error')


def test_refund_order_by_hand_needs_a_note(doctor, entity):
    order = paid_transfer(doctor)
    with pytest.raises(CommandError, match='note'):
        run('refund_order', order.reference, '--reason', 'goodwill', '--manual')
    assert 'refund done' in run('refund_order', order.reference, '--reason', 'goodwill',
                                '--manual', '--note', 'by hand')
    with pytest.raises(CommandError):
        run('refund_order', order.reference, '--reason', 'whim')
    with pytest.raises(CommandError, match='no such order'):
        run('refund_order', 'KM-000000', '--reason', 'error')


def test_refund_order_reports_a_refusal_and_a_missing_answer(
        doctor, entity, redsys_on, monkeypatch, sentry):
    order, _payment = paid_redsys(doctor)
    monkeypatch.setattr('accounts.http.post_json', FakeRedsys(error='SIS0057'))
    with pytest.raises(CommandError, match='refused'):
        run('refund_order', order.reference, '--reason', 'error')
    monkeypatch.setattr('accounts.http.post_json',
                        FakeRedsys(fault=urllib.error.URLError('down')))
    printed = run('refund_order', order.reference, '--reason', 'error')
    assert 'refund needs_attention' in printed and 'credit note -' in printed
    assert 'blocked for refunds' in printed


def test_cancel_order_cancels_an_unpaid_order_and_refuses_a_paid_one(doctor, entity):
    order = new(doctor)
    assert run('cancel_order', order.reference).strip() == f'order {order.reference}: cancelled'
    paid = paid_fake(doctor)
    with pytest.raises(CommandError, match='InvalidTransition'):
        run('cancel_order', paid.reference)
    with pytest.raises(CommandError, match='no such order'):
        run('cancel_order', 'KM-000000')
