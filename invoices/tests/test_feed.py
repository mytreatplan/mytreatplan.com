"""The accounting feed (epic-payments entry 8): one test per portal row of the plan's matrix,
asserting on the OutboxEvent rows and their payloads."""
from decimal import Decimal

import pytest
from mytp_core.contracts import (INVOICE_ISSUED, PAYMENT_CONFIRMED, REFUND_COMPLETED,
                                 InvalidPayload, build_payload)

from integration.models import OutboxEvent
from invoices import service as invoices
from invoices.models import Invoice
from payments import service
from payments.models import OrderLine
from payments.tests.conftest import *  # noqa: F401,F403
from payments.tests.test_refunds import do_refund, paid_fake, paid_transfer
from payments.tests.test_service import actor, new, pay

pytestmark = [pytest.mark.django_db(transaction=True),
              pytest.mark.usefixtures('clear_cache', 'portal_urls')]

FEED = (INVOICE_ISSUED, PAYMENT_CONFIRMED, REFUND_COMPLETED)
BUYER = {'kind': 'company', 'legal_name': 'Dental Walk-in S.L.', 'tax_id': 'B12345674',
         'country': 'ES', 'address_line1': 'Calle Sol 2', 'city': 'Sevilla',
         'postal_code': '41001', 'billing_email': 'cash@example.com'}


@pytest.fixture(autouse=True)
def fake_gateway(fake_on):
    return fake_on


def feed():
    return list(OutboxEvent.objects.filter(type__in=FEED).order_by('id'))


def of_type(event_type):
    return [event for event in feed() if event.type == event_type]


def test_a_paid_gateway_order_sends_the_invoice_and_the_payment(doctor, entity):
    order = paid_fake(doctor)
    invoice = Invoice.objects.get(order_id=order.pk)
    (sent,) = of_type(INVOICE_ISSUED)
    assert (sent.aggregate_id, sent.aggregate_version, sent.version) == (invoice.pk, 1, 1)
    payload = sent.payload
    assert payload['kind'] == 'order' and payload['number'] == invoice.number
    assert payload['client_uid'] == str(doctor.uid) and payload['order_id'] == str(order.pk)
    assert 'original_id' not in payload and 'reason' not in payload
    assert payload['seller']['legal_name'] == invoice.seller_legal_name
    assert payload['buyer']['legal_name'] == invoice.bill_legal_name
    assert payload['buyer']['tax_id'] == invoice.bill_tax_id
    assert payload['buyer']['country'] == 'ES'
    assert payload['total'] == {'amount': str(invoice.total), 'currency': 'EUR'}
    assert payload['vat_groups'] and payload['lines'][0]['net']['currency'] == 'EUR'
    (paid,) = of_type(PAYMENT_CONFIRMED)
    payment = order.payments.get()
    assert paid.aggregate_id == payment.pk and paid.aggregate_version == 1
    assert paid.payload['invoice_id'] == str(invoice.pk)
    assert paid.payload['client_uid'] == str(doctor.uid)
    assert paid.payload['amount'] == {'amount': str(payment.amount), 'currency': 'EUR'}
    assert paid.payload['gateway'] == 'fake'


def test_a_transfer_marked_paid_sends_the_invoice_and_the_payment(doctor, entity):
    paid_transfer(doctor)
    assert [event.type for event in feed()] == [INVOICE_ISSUED, PAYMENT_CONFIRMED]
    assert of_type(PAYMENT_CONFIRMED)[0].payload['gateway'] == 'bank_transfer'


def test_a_repeated_issue_sends_nothing(doctor, entity):
    order = pay(new(doctor), doctor)
    assert len(feed()) == 1
    invoices.issue_for_order(order)
    note = invoices.issue_credit_note(Invoice.objects.get(order_id=order.pk), reason='error',
                                      actor=service.SYSTEM_ACTOR)
    assert len(feed()) == 2
    assert invoices.issue_credit_note(Invoice.objects.get(order_id=order.pk), reason='error',
                                      actor=service.SYSTEM_ACTOR).pk == note.pk
    assert len(feed()) == 2


def test_a_free_order_sends_the_invoice_only(doctor, entity, admin):
    from catalogue import resolver
    from integration.tests.snapshot_rows import GENERAL, pull, voucher
    from subscriptions.tests.helpers import catalogue
    assert pull(admin, 2, catalogue(), vouchers=[voucher(
        GENERAL['id'], 'K7QM2XH9TR', amount='100.00')]) == ('applied', 2)
    order = new(doctor, voucher=resolver.voucher_by_code('K7QM2XH9TR'))
    assert order.total == Decimal('0.00')
    assert [event.type for event in feed()] == [INVOICE_ISSUED]
    assert feed()[0].payload['total']['amount'] == '0.00'


def test_a_refund_sends_the_credit_note_and_the_refund_without_the_note(doctor, entity):
    order = paid_fake(doctor)
    done = do_refund(order, reason='goodwill')
    invoice = Invoice.objects.get(order_id=order.pk)
    note_event = [e for e in of_type(INVOICE_ISSUED) if e.payload['kind'] == 'credit_note'][0]
    payload = note_event.payload
    assert note_event.aggregate_id == done.credit_note.pk
    assert (payload['original_id'], payload['reason']) == (str(invoice.pk), 'goodwill')
    assert payload['total']['amount'] == f'-{invoice.total}'
    assert payload['client_uid'] == str(doctor.uid)
    (refund,) = of_type(REFUND_COMPLETED)
    assert refund.aggregate_id == done.pk and refund.aggregate_version == 1
    assert refund.payload['credit_note_id'] == str(done.credit_note.pk)
    assert refund.payload['invoice_id'] == str(invoice.pk)
    assert refund.payload['payment_id'] == str(order.payments.get().pk)
    assert refund.payload['manual'] is False and 'note' not in refund.payload


def test_a_manual_refund_note_is_never_sent(doctor, entity):
    order = paid_transfer(doctor)
    do_refund(order, manual=True, note='Returned to the IBAN ending 1332')
    (refund,) = of_type(REFUND_COMPLETED)
    assert refund.payload['manual'] is True and 'payment_id' not in refund.payload
    assert 'IBAN' not in str([e.payload for e in feed()])


def test_a_manual_sale_has_no_client_and_a_line_without_a_code_has_no_key(catalogue_loaded):
    lines = [{'name': 'Setup', 'net': '100.00', 'vat_percent': '21.00'},
             {'name': 'Case', 'net': '10.00', 'vat_percent': '21.00',
              'patient_code': 'EU-7K3QX9MD'}]
    invoice = invoices.issue_manual('ES', BUYER, lines, 'EUR', service.SYSTEM_ACTOR)
    (sent,) = feed()
    payload = sent.payload
    assert sent.aggregate_id == invoice.pk and payload['kind'] == 'manual'
    assert not {'client_uid', 'order_id', 'original_id'} & set(payload)
    assert 'patient_code' not in payload['lines'][0]
    assert payload['lines'][1]['patient_code'] == 'EU-7K3QX9MD'


def test_an_order_line_patient_code_travels(doctor, entity):
    order = new(doctor)
    OrderLine.objects.filter(order=order).update(patient_code='EU-7K3QX9MD')
    pay(order, doctor)
    assert of_type(INVOICE_ISSUED)[0].payload['lines'][0]['patient_code'] == 'EU-7K3QX9MD'


@pytest.mark.parametrize('code', ['Ana', 'eu-7k3qx9md', 'EU-7K3QX9M', 'EU7K3QX9MD1'])
def test_a_bad_patient_code_is_refused_and_nothing_is_written(catalogue_loaded, code):
    lines = [{'name': 'Case', 'net': '10.00', 'vat_percent': '21.00', 'patient_code': code}]
    with pytest.raises(invoices.BadLine):
        invoices.issue_manual('ES', BUYER, lines, 'EUR', service.SYSTEM_ACTOR)
    assert not Invoice.objects.exists() and not feed()


def test_a_payload_with_a_patient_name_is_refused(doctor, entity):
    pay(new(doctor), doctor)
    payload = of_type(INVOICE_ISSUED)[0].payload
    payload['lines'][0]['patient_name'] = 'Ana'
    with pytest.raises(InvalidPayload):
        build_payload(INVOICE_ISSUED, payload)
