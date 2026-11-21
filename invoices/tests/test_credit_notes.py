"""Credit notes (epic-payments entry 7): the invoice side. The refund flow is tested in
payments/tests/test_refunds.py."""
import threading
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, connections, transaction

from invoices import service as invoices
from invoices.models import AppendOnly, Invoice, InvoiceLine, InvoiceSeries
from payments import service
from payments.tests.conftest import *  # noqa: F401,F403
from payments.tests.test_service import actor, new, pay

pytestmark = pytest.mark.django_db

STAFF = service.SYSTEM_ACTOR


@pytest.fixture(autouse=True)
def fake_gateway(fake_on):
    return fake_on


def invoice_for(doctor):
    return Invoice.objects.get(order_id=pay(new(doctor), doctor).pk)


def test_a_credit_note_copies_the_invoice_negated(doctor, entity):
    invoice = invoice_for(doctor)
    note = invoices.issue_credit_note(invoice, reason='error', actor=STAFF)
    assert note.number == f'ES-EU-{note.year}-R000001' and note.kind == 'credit_note'
    assert (note.original_id, note.order_id, note.reason) == (invoice.pk, None, 'error')
    assert note.original.number == invoice.number
    for name in ('net_total', 'discount_total', 'ice_total', 'voucher_taken', 'vat_total',
                 'total'):
        assert getattr(note, name) == -getattr(invoice, name) or (
            getattr(invoice, name) == 0 and getattr(note, name) == 0)
    assert note.vat_groups == [
        {**group, 'base': f'-{group["base"]}', 'vat': '0.00' if group['vat'] == '0.00'
         else f'-{group["vat"]}'} for group in invoice.vat_groups]
    for name in ('seller_legal_name', 'seller_address', 'seller_vat_number', 'currency',
                 'bill_legal_name', 'bill_tax_id', 'bill_country', 'bill_entity_id',
                 'bill_vies_status', 'bill_address_line1'):
        assert getattr(note, name) == getattr(invoice, name)
    assert (note.einvoice_status, note.extensions) == ('none', {'rectification_code': ''})
    original, copy = invoice.lines.get(), note.lines.get()
    assert (copy.code, copy.name, copy.quantity, copy.vat_percent, copy.vat_text,
            copy.tax_category) == (original.code, original.name, original.quantity,
                                   original.vat_percent, original.vat_text,
                                   original.tax_category)
    assert copy.net == -original.net and copy.list_amount == -original.list_amount


def test_a_vat_credit_note_negates_the_vat_groups(catalogue_loaded, ae_portal, fake_on):
    from accounts import fiscal
    from integration.tests.snapshot_rows import TPS_AED
    from payments.tests.conftest import COMPANY
    from payments.tests.test_service import line
    from subscriptions.tests.helpers import doctor as make_doctor
    user = make_doctor('uae@example.com', country='AE')
    fiscal.create(user, {**COMPANY, 'legal_name': 'Gulf Dental LLC', 'tax_id': '1',
                         'country': 'AE', 'city': 'Dubai'})
    order = service.create_order(user, [line(TPS_AED)], actor=actor(user))
    invoice = Invoice.objects.get(order_id=pay(order, user).pk)
    note = invoices.issue_credit_note(invoice, reason='cancellation', actor=STAFF)
    assert invoice.vat_total > 0 and note.vat_total == -invoice.vat_total
    group, original = note.vat_groups[0], invoice.vat_groups[0]
    assert Decimal(group['base']) == -Decimal(original['base'])
    assert Decimal(group['vat']) == -Decimal(original['vat'])
    assert note.number == f'AE-AE-{note.year}-R000001' and note.currency == 'AED'
    assert note.lines.get().vat_percent == Decimal('5.00')


def test_a_second_call_returns_the_same_note_and_takes_no_number(doctor, entity):
    invoice = invoice_for(doctor)
    one = invoices.issue_credit_note(invoice, reason='error', actor=STAFF)
    two = invoices.issue_credit_note(invoice, reason='goodwill', actor=STAFF)
    assert one.pk == two.pk and two.reason == 'error'
    assert InvoiceSeries.objects.get(kind='credit_note').last_number == 1


def test_the_series_are_separate(doctor, entity):
    first, second = invoice_for(doctor), invoice_for(doctor)
    invoices.issue_credit_note(second, reason='error', actor=STAFF)
    assert invoices._next_number('ES', 'EU', first.year) == f'ES-EU-{first.year}-000003'
    assert invoices._next_number('ES', 'EU', first.year, 'credit_note') == (
        f'ES-EU-{first.year}-R000002')
    assert InvoiceSeries.objects.count() == 2


def test_a_credit_note_has_no_credit_note_and_the_reason_is_checked(doctor, entity):
    invoice = invoice_for(doctor)
    with pytest.raises(invoices.InvoiceError):
        invoices.issue_credit_note(invoice, reason='whim', actor=STAFF)
    note = invoices.issue_credit_note(invoice, reason='error', actor=STAFF)
    with pytest.raises(invoices.InvoiceError):
        invoices.issue_credit_note(note, reason='error', actor=STAFF)
    with pytest.raises(service.InvalidActor):
        invoices.issue_credit_note(invoice, reason='error', actor=service.Actor('admin', None))
    with pytest.raises(service.InvalidActor):
        invoices.issue_credit_note(invoice, reason='error', actor=actor(doctor))
    assert Invoice.objects.filter(kind='credit_note').count() == 1  # only the first


def test_a_credit_note_is_append_only_like_an_invoice(doctor, entity):
    note = invoices.issue_credit_note(invoice_for(doctor), reason='error', actor=STAFF)
    line = note.lines.get()
    note.total = Decimal('-1.00')
    with pytest.raises(AppendOnly):
        note.save()
    with pytest.raises(AppendOnly):
        note.save(update_fields=['total'])
    with pytest.raises(AppendOnly):
        note.save(update_fields=['reason'])
    with pytest.raises(AppendOnly):
        note.delete()
    with pytest.raises(AppendOnly):
        Invoice.objects.filter(pk=note.pk).update(total=1)
    with pytest.raises(AppendOnly):
        Invoice.objects.filter(pk=note.pk).delete()
    line.net = Decimal('-1.00')
    with pytest.raises(AppendOnly):
        line.save()
    with pytest.raises(AppendOnly):
        line.delete()
    with pytest.raises(AppendOnly):
        InvoiceLine.objects.filter(pk=line.pk).update(net=1)
    stored = Invoice.objects.get(pk=note.pk)
    assert stored.total == -Decimal('100.00') and stored.reason == 'error'


def test_a_credit_note_takes_the_einvoice_status_like_an_invoice(doctor, entity):
    note = invoices.issue_credit_note(invoice_for(doctor), reason='error', actor=STAFF)
    done = invoices.set_einvoice_status(note, 'sent', 'ASP-9')
    assert Invoice.objects.get(pk=note.pk).einvoice_reference == 'ASP-9' == done.einvoice_reference


def test_the_database_allows_one_credit_note_per_invoice(doctor, entity):
    invoice = invoice_for(doctor)
    note = invoices.issue_credit_note(invoice, reason='error', actor=STAFF)
    with pytest.raises(IntegrityError), transaction.atomic():
        Invoice.objects.create(
            number='X-R1', kind='credit_note', original=invoice, company_code='ES',
            instance='EU', year=2026, seller_legal_name='x', currency='EUR', net_total=0,
            discount_total=0, ice_total=0, voucher_taken=0, vat_total=0, total=0,
            issue_date=note.issue_date, time_zone='Europe/Madrid', actor_type='system')


def test_an_original_with_a_credit_note_cannot_be_deleted(doctor, entity):
    invoice = invoice_for(doctor)
    invoices.issue_credit_note(invoice, reason='error', actor=STAFF)
    with pytest.raises(AppendOnly):
        invoice.delete()


# --- Concurrency (PostgreSQL only) -------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_concurrent_credit_notes_get_numbers_with_no_gap(doctor, entity):
    if connection.vendor != 'postgresql':
        pytest.skip('row locks need PostgreSQL')
    originals = [invoice_for(doctor) for _ in range(3)]
    barrier, errors = threading.Barrier(3), []

    def run(original):
        try:
            barrier.wait()
            invoices.issue_credit_note(original, reason='error', actor=STAFF)
        except Exception as failure:  # noqa: BLE001
            errors.append(failure)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=run, args=(original,)) for original in originals]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    year = originals[0].year
    assert sorted(Invoice.objects.filter(kind='credit_note').values_list('number', flat=True)
                  ) == [f'ES-EU-{year}-R00000{n}' for n in (1, 2, 3)]
