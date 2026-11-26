"""Order emails (epic-ordering 9.20): one test per row of the plan's matrix. The receivers
queue `cart.tasks.send_order_email` after commit; the tests run the task by hand. The invoice
PDF is mocked where WeasyPrint is not needed."""
import logging

import pytest
from django.core import mail
from django.core.mail import EmailMessage
from django_tasks_db.models import DBTaskResult

from accounts.models import Profile
from cart import emails
from cart.tasks import send_order_email
from cart.tests.test_checkout import (BANK, actor, bank, by_transfer, finish, pay,  # noqa: F401
                                      ready, tps, two_items)
from invoices import pdf
from invoices.models import Invoice
from mytp_core.models import TaskEffect
from payments import service as payments
from payments.models import Order

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]


@pytest.fixture(autouse=True)
def fake_pdf(monkeypatch):
    calls = []

    def render(invoice, user, build=pdf.context):
        calls.append(build)
        return b'%PDF-fake'
    monkeypatch.setattr(pdf, 'render', render)
    return calls


@pytest.fixture
def capture(django_capture_on_commit_callbacks):
    def run(fn):
        with django_capture_on_commit_callbacks(execute=True):
            return fn()
    return run


def queued():
    return sorted((row.args_kwargs['args'][0] for row in DBTaskResult.objects.all()))


def send(kind, order):
    return send_order_email.call(kind, str(order.pk))


def transfer_order(user, patient, capture):
    tps(user, patient)
    order = finish(user)
    return capture(lambda: by_transfer(order, user))


def test_transfer_approved_sends_one_confirmation(bank, patient, capture):
    order = transfer_order(bank, patient, capture)
    assert queued() == ['order_confirmation']
    assert send('order_confirmation', order) is True
    message = mail.outbox[0]
    assert len(mail.outbox) == 1 and message.to == [bank.email]
    assert order.reference in message.subject and order.reference in message.body
    assert patient.code in message.body and f'{order.total} {order.currency}' in message.body
    assert BANK['iban'][:4] in message.body and 'ES91 2100' in message.body
    assert f'/en/checkout/{order.pk}/transfer/' in message.body


def test_choosing_the_transfer_again_sends_no_second_confirmation(bank, patient, capture):
    order = transfer_order(bank, patient, capture)
    send('order_confirmation', order)
    order.refresh_from_db()
    order = capture(lambda: payments.start_payment(
        order, 'fake', expected_version=order.version, actor=actor(bank)))
    order = capture(lambda: by_transfer(order, bank))
    assert queued() == ['order_confirmation', 'order_confirmation']
    assert send('order_confirmation', order) is False
    assert len(mail.outbox) == 1


def test_a_company_without_bank_details_still_gets_the_confirmation(bank, patient, capture):
    order = transfer_order(bank, patient, capture)
    from catalogue.models import BillingCompany
    from integration.tests.snapshot_rows import snapshot_writes
    with snapshot_writes():
        BillingCompany.objects.update(iban='')
    send('order_confirmation', order)
    body = mail.outbox[0].body
    assert order.reference in body and 'IBAN' not in body and patient.code in body


def test_a_paid_transfer_sends_one_receipt_with_the_invoice(bank, patient, capture, fake_pdf):
    order = transfer_order(bank, patient, capture)
    order = capture(lambda: payments.mark_transfer_paid(
        order, order.total, expected_version=order.version, actor=payments.SYSTEM_ACTOR))
    assert queued() == ['order_confirmation', 'payment_receipt']
    assert send('payment_receipt', order) is True
    invoice = Invoice.objects.get(order=order)
    message = mail.outbox[0]
    assert len(message.attachments) == 1 and message.attachments[0][0] == f'{invoice.number}.pdf'
    assert message.attachments[0][2] == 'application/pdf'
    assert fake_pdf == [pdf.codes_only_context]
    assert order.reference in message.body and patient.code in message.body
    assert send('payment_receipt', order) is False and len(mail.outbox) == 1


def test_a_card_order_gets_the_receipt_only(ready, patient, capture):
    tps(ready, patient)
    order = capture(lambda: pay(finish(ready), ready))
    assert queued() == ['payment_receipt']
    assert send('payment_receipt', order) is True
    assert len(mail.outbox) == 1 and len(mail.outbox[0].attachments) == 1
    assert mail.outbox[0].attachments[0][2] == 'application/pdf'


def test_a_free_order_gets_the_receipt_only(ready, patient, capture):
    two_items(ready, patient)
    from cart.tests.test_checkout import BIG_CODE
    order = capture(lambda: finish(ready, BIG_CODE))
    assert order.total == 0 and queued() == ['payment_receipt']
    assert send('payment_receipt', order) is True


def test_other_orders_send_nothing(ready, patient, capture):
    tps(ready, patient)
    order = finish(ready)
    assert emails.should_email(order)
    Order.objects.filter(pk=order.pk).update(kind=Order.SUBSCRIPTION)
    order.refresh_from_db()
    assert not emails.should_email(order)
    Order.objects.filter(pk=order.pk).update(kind=Order.SERVICE)
    order.lines.update(patient_code='')
    assert not emails.should_email(order)
    order.lines.update(patient_code=patient.code)
    Order.objects.filter(pk=order.pk).update(user=None)
    order.refresh_from_db()
    assert not emails.should_email(order)
    capture(lambda: emails.on_paid(order))
    assert queued() == [] and send('payment_receipt', order) is False and not mail.outbox


def test_a_failed_send_changes_nothing_and_is_logged_by_uuid(bank, patient, capture,
                                                             monkeypatch, caplog):
    order = transfer_order(bank, patient, capture)

    def broken(self, *args, **kwargs):
        raise OSError(f'refused {self.to[0]} {BANK["iban"]}')
    monkeypatch.setattr(EmailMessage, 'send', broken)
    with caplog.at_level(logging.DEBUG):
        assert send('order_confirmation', order) is None
    log = caplog.text
    assert f'order email not sent: order_confirmation order {order.pk} (OSError)' in log
    assert bank.email not in log and BANK['iban'] not in log
    assert not TaskEffect.objects.filter(effect='order_confirmation').exists()
    before = Order.objects.get(pk=order.pk)
    assert before.version == order.version
    monkeypatch.undo()
    assert send('order_confirmation', order) is True


def test_a_rollback_queues_nothing(bank, patient, django_capture_on_commit_callbacks):
    tps(bank, patient)
    order = finish(bank)
    hook = payments.hooks
    hook.register('transfer_approved', 'zz_raises', lambda o: 1 / 0)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            with pytest.raises(payments.HookFailed):
                by_transfer(order, bank)
    finally:
        hook._registry['transfer_approved'].pop('zz_raises')
    assert queued() == []


def test_a_spanish_doctor_gets_spanish_emails(bank, patient, capture):
    Profile.objects.create(user=bank, language='es')
    order = transfer_order(bank, patient, capture)
    send('order_confirmation', order)
    message = mail.outbox[0]
    assert 'pago por transferencia bancaria' in message.subject
    assert 'Titular de la cuenta' in message.body and 'Pedido:' in message.body
    order = capture(lambda: payments.mark_transfer_paid(
        order, order.total, expected_version=order.version, actor=payments.SYSTEM_ACTOR))
    send('payment_receipt', order)
    assert 'Pago recibido' in mail.outbox[1].subject and 'Adjuntamos su factura' in mail.outbox[1].body


def test_no_patient_name_in_the_messages_or_the_codes_only_pdf(bank, patient, capture):
    order = transfer_order(bank, patient, capture)
    send('order_confirmation', order)
    order = capture(lambda: payments.mark_transfer_paid(
        order, order.total, expected_version=order.version, actor=payments.SYSTEM_ACTOR))
    send('payment_receipt', order)
    for message in mail.outbox:
        assert 'Ana' not in message.body and 'Pérez' not in message.body
        assert 'Ana' not in message.subject
    invoice = Invoice.objects.get(order=order)
    named = pdf.render_html(invoice, bank)
    codes = pdf.render_html(invoice, bank, build=pdf.codes_only_context)
    assert 'Ana Pérez' in named and patient.code in named
    assert 'Ana' not in codes and 'Pérez' not in codes and patient.code in codes


def test_a_confirmation_after_switching_to_card_is_not_sent_nor_marked(bank, patient, capture):
    order = transfer_order(bank, patient, capture)
    order.refresh_from_db()
    order = capture(lambda: payments.start_payment(
        order, 'fake', expected_version=order.version, actor=actor(bank)))
    assert send('order_confirmation', order) is False
    assert not mail.outbox and not TaskEffect.objects.filter(effect='order_confirmation').exists()
    order = capture(lambda: by_transfer(order, bank))
    assert send('order_confirmation', order) is True and len(mail.outbox) == 1


def test_a_failed_pdf_render_changes_nothing_and_is_logged(bank, patient, capture,
                                                           monkeypatch, caplog):
    order = transfer_order(bank, patient, capture)
    order = capture(lambda: payments.mark_transfer_paid(
        order, order.total, expected_version=order.version, actor=payments.SYSTEM_ACTOR))

    def broken(invoice, user, build=pdf.context):
        raise RuntimeError('render failed')
    monkeypatch.setattr(pdf, 'render', broken)
    with caplog.at_level(logging.DEBUG):
        assert send('payment_receipt', order) is None
    assert f'order email not sent: payment_receipt order {order.pk} (RuntimeError)' in caplog.text
    assert not mail.outbox and not TaskEffect.objects.filter(effect='payment_receipt').exists()
    assert Order.objects.get(pk=order.pk).version == order.version
