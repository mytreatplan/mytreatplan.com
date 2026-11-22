"""Restore-safe invoice numbering (epic-payments, AD-7): the hold, `resume_after_restore` and
the `resume_invoice_numbering` command against a fake admin site (`FakeAdmin`, serving the
`invoice-series-highest` lookup). One test per row of the plan's matrix."""
import io
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from integration.tests.test_hub import FakeAdmin
from invoices import service as invoices
from invoices.models import Invoice, InvoiceSeries, NumberingHold
from invoices.tests.test_invoices import BUYER
from mytp_core import interface
from mytp_core.contracts import InvoiceSeriesHighestAnswer, invoice_series_highest
from mytp_core.contracts.hub import INVOICE_SERIES_HIGHEST_PATH
from payments import service
from payments.models import Order
from payments.tests.conftest import *  # noqa: F401,F403
from payments.tests.test_service import actor, new, pay

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
YEAR = datetime.now(ZoneInfo('Europe/Madrid')).year
LINE = {'name': 'Setup', 'net': '100.00', 'vat_percent': '21.00'}


@pytest.fixture(autouse=True)
def fake_gateway(fake_on):
    return fake_on


@pytest.fixture
def admin_series(interface_settings, monkeypatch):
    """The fake admin site answering the series lookup with `admin.series`, a list of
    (company, instance, year, kind, highest). A test that also needs the catalogue lists
    `catalogue_loaded` first: that fixture's own fake admin is replaced by this one."""
    interface_settings.HUB_LOOKUP_TIMEOUT = 3
    admin = FakeAdmin()
    monkeypatch.setattr(interface, '_open', admin)
    admin.path = INVOICE_SERIES_HIGHEST_PATH
    admin.series = []
    admin.answer_for = lambda region: invoice_series_highest(region, NOW, admin.series)
    return admin


def run(*args):
    out = io.StringIO()
    call_command('resume_invoice_numbering', *args, stdout=out)
    return out.getvalue()


def counter(kind='invoice', year=None):
    return InvoiceSeries.objects.get(company_code='ES', instance='EU', kind=kind,
                                     year=year or YEAR).last_number


def seed(last_number, kind='invoice', year=None):
    InvoiceSeries.objects.update_or_create(
        company_code='ES', instance='EU', year=year or YEAR, kind=kind,
        defaults={'last_number': last_number})


def test_a_restore_that_lost_two_invoices_raises_the_counter(catalogue_loaded, admin_series):
    seed(5)
    admin_series.series = [('ES', 'EU', YEAR, 'invoice', 7)]
    output = run()
    assert counter() == 7
    assert not invoices.numbering_on_hold()
    assert f'ES {YEAR} invoice: raised from 5 to 7' in output
    # The first real invoice continues above the admin's highest.
    invoice = invoices.issue_manual('ES', BUYER, [LINE], 'EUR', service.SYSTEM_ACTOR)
    assert invoice.number == f'ES-EU-{YEAR}-000008'


@pytest.mark.parametrize('admin_highest', [7, 3])
def test_nothing_lost_leaves_the_counter_and_lifts_the_hold(admin_series, admin_highest):
    seed(7)
    admin_series.series = [('ES', 'EU', YEAR, 'invoice', admin_highest)]
    output = run()
    assert counter() == 7 and not invoices.numbering_on_hold()
    assert 'unchanged at 7' in output


def test_a_series_only_the_admin_knows_is_created(admin_series):
    seed(7)
    admin_series.series = [('ES', 'EU', YEAR, 'credit_note', 2),
                           ('ES', 'EU', YEAR, 'invoice', 7)]
    output = run()
    assert counter('credit_note') == 2 and counter() == 7
    assert 'credit_note: created at 2' in output
    assert invoices._next_number('ES', 'EU', YEAR, InvoiceSeries.CREDIT_NOTE) == (
        f'ES-EU-{YEAR}-R000003')


def test_another_instances_series_are_ignored(admin_series):
    admin_series.series = [('ES', 'AE', YEAR, 'invoice', 4)]
    run()
    assert not InvoiceSeries.objects.exists() and not invoices.numbering_on_hold()


def test_the_admin_down_keeps_the_hold_and_exits_non_zero(catalogue_loaded, admin_series):
    seed(5)
    admin_series.down = True
    with pytest.raises(CommandError) as failed:
        run()
    assert 'stays on hold' in str(failed.value)
    assert 'unavailable (network)' in str(failed.value)
    assert invoices.numbering_on_hold() and counter() == 5
    # Issuing is refused while the hold is on.
    with pytest.raises(invoices.NumberingOnHold):
        invoices.issue_manual('ES', BUYER, [LINE], 'EUR', service.SYSTEM_ACTOR)
    assert not Invoice.objects.exists()
    admin_series.down = False
    admin_series.series = [('ES', 'EU', YEAR, 'invoice', 9)]
    run()
    assert not invoices.numbering_on_hold() and counter() == 9


def test_a_payment_during_the_hold_rolls_back_and_is_confirmed_after_it(doctor, entity,
                                                                         admin_series):
    order = new(doctor)
    order = service.start_payment(order, 'fake', expected_version=1, actor=actor(doctor))
    run('--hold-only')
    assert invoices.numbering_on_hold()
    with pytest.raises(service.HookFailed) as failed:
        service.mark_paid(order, expected_version=2, actor=service.SYSTEM_ACTOR)
    assert isinstance(failed.value.__cause__, invoices.NumberingOnHold)
    stored = Order.objects.get(pk=order.pk)
    assert stored.state == 'pending_payment' and stored.paid_at is None
    assert not Invoice.objects.exists() and not InvoiceSeries.objects.exists()
    # The hold lifts (the admin holds four invoices): the retry works.
    admin_series.series = [('ES', 'EU', YEAR, 'invoice', 4)]
    run()
    paid = service.mark_paid(Order.objects.get(pk=order.pk), expected_version=2,
                             actor=service.SYSTEM_ACTOR)
    assert Invoice.objects.get(order_id=paid.pk).number == f'ES-EU-{YEAR}-000005'


def test_issuing_during_the_hold_raises_numbering_on_hold(doctor, entity):
    paid = pay(new(doctor), doctor)
    invoice = Invoice.objects.get(order_id=paid.pk)
    invoices.hold_numbering()
    with pytest.raises(invoices.NumberingOnHold):
        invoices.issue_manual('ES', BUYER, [LINE], 'EUR', service.SYSTEM_ACTOR)
    with pytest.raises(invoices.NumberingOnHold):
        invoices.issue_credit_note(invoice, reason='goodwill', actor=service.SYSTEM_ACTOR)
    assert issubclass(invoices.NumberingOnHold, invoices.InvoiceError)
    # What is issued already is returned, not renumbered.
    assert invoices.issue_for_order(paid).pk == invoice.pk
    assert Invoice.objects.count() == 1 and counter() == 1


def test_hold_only_sets_the_hold_without_calling_the_admin(admin_series):
    output = run('--hold-only')
    assert invoices.numbering_on_hold() and admin_series.calls == []
    hold = NumberingHold.objects.get()
    assert hold.on and hold.set_at is not None and hold.reason
    assert 'on hold' in output


def test_the_hold_keeps_its_first_time_when_set_again():
    first = invoices.hold_numbering()
    again = invoices.hold_numbering('again')
    assert again.set_at == first.set_at and again.reason == 'again'
    assert NumberingHold.objects.count() == 1


def test_running_it_twice_changes_nothing_more(admin_series):
    seed(5)
    admin_series.series = [('ES', 'EU', YEAR, 'invoice', 7)]
    run()
    second = run()
    assert counter() == 7 and 'unchanged at 7' in second
    assert InvoiceSeries.objects.count() == 1 and not invoices.numbering_on_hold()


def test_a_failure_while_raising_leaves_the_hold_on_and_the_counters(monkeypatch):
    seed(5)
    invoices.hold_numbering()
    answer = InvoiceSeriesHighestAnswer.model_validate(
        invoice_series_highest('eu', NOW, [('ES', 'EU', YEAR, 'invoice', 7),
                                           ('ES', 'EU', YEAR, 'credit_note', 1)]))

    def boom(self, *args, **kwargs):
        raise RuntimeError('database went away')
    monkeypatch.setattr(NumberingHold, 'save', boom)
    with pytest.raises(RuntimeError):
        invoices.resume_after_restore(answer)
    monkeypatch.undo()
    assert counter() == 5 and invoices.numbering_on_hold()
    assert not InvoiceSeries.objects.filter(kind='credit_note').exists()


# --- After the hold: pending payments, expiry and refunds -----------------------------------

def hold_and_exhaust_webhook_retries(payment):
    """The gateway confirmed while numbering was on hold: every webhook retry was refused,
    and the last one gave up (the row is `failed`, no retry left)."""
    from payments import webhooks
    from payments.models import WebhookLog
    run('--hold-only')
    row = WebhookLog.objects.order_by('-id').first()
    results = [webhooks.process(row.pk) for _ in range(webhooks.MAX_ATTEMPTS)]
    assert results[-1] == 'failed'
    row.refresh_from_db()
    assert row.next_try_at is None and row.processed_at is None


@pytest.mark.usefixtures('clear_cache', 'portal_urls')
def test_a_payment_refused_during_the_hold_is_confirmed_by_the_command(doctor, entity,
                                                                         admin_series):
    from payments import fake
    from payments.tests.test_webhooks import attempt, send
    order, payment = attempt(doctor)
    fake.pay(payment)
    send(payment)
    hold_and_exhaust_webhook_retries(payment)
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    admin_series.series = [('ES', 'EU', YEAR, 'invoice', 4)]
    output = run()
    assert f'order {order.reference}: payment confirmed' in output
    assert '1 pending order(s) confirmed' in output
    paid = Order.objects.get(pk=order.pk)
    assert paid.state == 'paid'
    assert Invoice.objects.get(order_id=order.pk).number == f'ES-EU-{YEAR}-000005'
    assert not invoices.numbering_on_hold()
    # Idempotent: nothing is left pending.
    assert '0 pending order(s) confirmed' in run()


@pytest.mark.usefixtures('clear_cache', 'portal_urls')
def test_an_unpaid_pending_order_is_left_and_a_failing_one_does_not_stop_the_rest(
        doctor, entity, admin_series, monkeypatch):
    from payments import fake, webhooks
    from payments.tests.test_service import new
    from payments.tests.test_webhooks import attempt
    unpaid, _ = attempt(doctor)
    broken, broken_payment = attempt(doctor, order=new(doctor))
    paid_order, paid_payment = attempt(doctor, order=new(doctor))
    fake.pay(paid_payment)
    real = webhooks.reread_and_confirm

    def flaky(payment):
        if payment.pk == broken_payment.pk:
            raise RuntimeError('gateway down')
        return real(payment)
    monkeypatch.setattr(webhooks, 'reread_and_confirm', flaky)
    out, err = io.StringIO(), io.StringIO()
    call_command('resume_invoice_numbering', stdout=out, stderr=err)
    assert f'order {paid_order.reference}: payment confirmed' in out.getvalue()
    assert '1 pending order(s) confirmed' in out.getvalue()
    assert f'order {broken.reference}: not re-checked (RuntimeError)' in err.getvalue()
    assert unpaid.reference not in out.getvalue()
    assert Order.objects.get(pk=paid_order.pk).state == 'paid'
    assert Order.objects.get(pk=unpaid.pk).state == 'pending_payment'
    assert not invoices.numbering_on_hold()


@pytest.mark.usefixtures('clear_cache', 'portal_urls')
def test_the_expiry_job_expires_nothing_while_the_hold_is_on(doctor, entity):
    from datetime import timedelta
    from payments.tests.test_webhooks import attempt
    from payments.models import Payment
    order, payment = attempt(doctor)
    now = timezone.now()
    Order.objects.filter(pk=order.pk).update(created_at=now - timedelta(hours=30),
                                             pending_at=now - timedelta(hours=25))
    Payment.objects.filter(pk=payment.pk).update(session_expires_at=now - timedelta(minutes=1))
    invoices.hold_numbering()
    assert service.expire_stale(now) == 0
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    invoices.resume_after_restore(InvoiceSeriesHighestAnswer.model_validate(
        invoice_series_highest('eu', NOW, [])))
    assert service.expire_stale(now) == 1
    assert Order.objects.get(pk=order.pk).state == 'expired'


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures('clear_cache', 'portal_urls')
def test_a_refund_during_the_hold_is_refused_before_anything_is_written(doctor, entity):
    from payments import refunds
    from payments.models import Refund
    from payments.tests.test_refunds import STAFF, paid_fake
    order = paid_fake(doctor)
    invoices.hold_numbering()
    with pytest.raises(invoices.NumberingOnHold):
        refunds.refund(order, expected_version=order.version, actor=STAFF,
                       reason='goodwill')
    assert not Refund.objects.exists()
    assert Order.objects.get(pk=order.pk).state == 'paid'
    # The staff command shows it as a refusal, not a traceback.
    with pytest.raises(CommandError, match='NumberingOnHold'):
        call_command('refund_order', order.reference, '--reason', 'goodwill')
    assert not Refund.objects.exists()


@pytest.mark.usefixtures('clear_cache', 'portal_urls')
def test_a_payment_whose_session_expired_during_the_hold_is_still_confirmed(
        doctor, entity, admin_series):
    from datetime import timedelta
    from payments import fake
    from payments.models import Payment
    from payments.tests.test_webhooks import attempt
    order, payment = attempt(doctor)
    fake.pay(payment)
    invoices.hold_numbering()
    Payment.objects.filter(pk=payment.pk).update(
        session_expires_at=timezone.now() - timedelta(hours=2))
    output = run()
    assert f'order {order.reference}: payment confirmed' in output
    assert Order.objects.get(pk=order.pk).state == 'paid'
