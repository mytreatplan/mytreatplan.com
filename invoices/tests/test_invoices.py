"""Invoices (epic-payments entry 6): one test (or more) per row of the plan's matrix."""
import threading
from datetime import datetime, timezone as utc
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection, connections
from django.utils import timezone

from accounts import fiscal
from accounts.erasure import erase_user
from accounts.models import FiscalEntity, User
from integration.tests.snapshot_rows import EXEMPT, REVERSE, TPS_AED
from invoices import service as invoices
from invoices.models import AppendOnly, Invoice, InvoiceLine, InvoiceSeries
from payments import service
from payments.models import Order, OrderLine
from payments.tests.conftest import *  # noqa: F401,F403
from payments.tests.test_service import actor, line, new, pay
from subscriptions.tests.helpers import doctor as make_doctor

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fake_gateway(fake_on):
    return fake_on


def only_invoice(order):
    return Invoice.objects.get(order_id=order.pk)


# --- Numbering and the copy ----------------------------------------------------------------

def test_three_paid_orders_get_consecutive_numbers_and_the_orders_values(doctor, entity):
    orders = [pay(new(doctor), doctor) for _ in range(3)]
    found = list(Invoice.objects.order_by('number'))
    year = datetime.now(ZoneInfo('Europe/Madrid')).year
    assert {i.year for i in found} == {year}
    assert [i.number for i in found] == [f'ES-EU-{year}-00000{n}' for n in (1, 2, 3)]
    for order in orders:
        invoice = only_invoice(order)
        assert invoice.kind == 'order' and invoice.einvoice_status == 'none'
        assert (invoice.net_total, invoice.vat_total, invoice.total, invoice.currency) == (
            order.net_total, order.vat_total, order.total, order.currency)
        assert invoice.vat_groups == order.vat_groups
        assert invoice.bill_legal_name == 'Clínica Espuny S.L.' == order.bill_legal_name
        assert invoice.bill_tax_id == order.bill_tax_id and invoice.bill_country == 'ES'
        assert invoice.bill_entity_id == order.bill_entity_id
        assert invoice.issued_at == order.paid_at and invoice.time_zone == 'Europe/Madrid'
        assert (invoice.seller_legal_name, invoice.seller_vat_number,
                invoice.seller_country) == ('MyTPDSO Spain S.L.', 'B93858371', 'ES')
        assert 'TRES CANTOS' in invoice.seller_address
    assert InvoiceSeries.objects.get().last_number == 3


def test_the_es_exemption_text_and_the_line_values_are_copied(doctor, entity):
    order = pay(new(doctor), doctor)
    invoice = only_invoice(order)
    row, source = invoice.lines.get(), order.lines.get()
    assert (row.vat_percent, row.vat_text, row.tax_category) == (
        Decimal('0.00'), EXEMPT, 'E')
    assert (row.code, row.name, row.quantity, row.list_amount, row.net) == (
        source.code, source.name, source.quantity, source.list_amount, source.net)
    assert row.included_case is False and row.patient_code == ''
    assert invoice.vat_groups == [{'percent': '0.00', 'text': EXEMPT, 'base': '100.00',
                                   'vat': '0.00'}]


def test_the_year_is_the_companys_local_year(doctor, entity, monkeypatch):
    order = new(doctor)
    order = service.start_payment(order, 'fake', expected_version=1, actor=actor(doctor))
    moment = datetime(2026, 12, 31, 23, 30, tzinfo=utc.utc)
    monkeypatch.setattr(timezone, 'now', lambda: moment)
    order = service.mark_paid(order, expected_version=2, actor=service.SYSTEM_ACTOR)
    invoice = only_invoice(order)
    assert invoice.number == 'ES-EU-2027-000001'
    assert str(invoice.issue_date) == '2027-01-01' and invoice.issued_at == moment


def test_a_new_year_restarts_the_series(doctor, entity, monkeypatch):
    numbers = []
    for moment in (datetime(2026, 6, 1, 10, 0, tzinfo=utc.utc),
                   datetime(2026, 6, 2, 10, 0, tzinfo=utc.utc),
                   datetime(2027, 2, 1, 10, 0, tzinfo=utc.utc)):
        order = service.start_payment(new(doctor), 'fake', expected_version=1,
                                      actor=actor(doctor))
        monkeypatch.setattr(timezone, 'now', lambda moment=moment: moment)
        numbers.append(only_invoice(service.mark_paid(
            order, expected_version=2, actor=service.SYSTEM_ACTOR)).number)
        monkeypatch.undo()
    assert numbers == ['ES-EU-2026-000001', 'ES-EU-2026-000002', 'ES-EU-2027-000001']


def test_reverse_charge_is_copied_with_its_text(catalogue_loaded):
    german = make_doctor('hans@example.com', country='DE')
    fiscal.create(german, {**COMPANY, 'country': 'DE', 'tax_id': 'DE136695976',
                           'legal_name': 'Erste GmbH'})
    second = fiscal.create(german, {**COMPANY, 'country': 'DE', 'tax_id': 'DE136695976',
                                    'legal_name': 'Zweite GmbH'})
    order = pay(new(german, entity=FiscalEntity.objects.get(pk=second.pk)), german)
    invoice = only_invoice(order)
    row = invoice.lines.get()
    assert invoice.number.startswith('IE-EU-') and invoice.time_zone == 'Europe/Dublin'
    assert (row.vat_percent, row.vat_text, row.tax_category) == (
        Decimal('0.00'), REVERSE, 'AE')
    assert invoice.vat_groups[0]['text'] == REVERSE and invoice.vat_total == Decimal('0.00')
    assert invoice.seller_company_number == 'Company number 815526 (Ireland)'


def test_a_free_order_and_an_included_case_still_get_their_invoice(doctor, entity):
    from patients import service as patients
    # Since 9.8 a paid line that names a patient gets a work order: a real patient.
    code = patients.create_patient(doctor, reference='P-1', actor=patients.Actor(
        'doctor', doctor.uid)).code
    order = new(doctor)
    OrderLine.objects.filter(order=order).update(
        included_case=True, voucher_share=Decimal('100.00'), net=Decimal('0.00'),
        patient_code=code)
    Order.objects.filter(pk=order.pk).update(
        net_total=0, voucher_taken=Decimal('100.00'), total=0, vat_total=0,
        vat_groups=[{'percent': '0.00', 'text': EXEMPT, 'base': '0.00', 'vat': '0.00'}])
    Order.objects.filter(pk=order.pk).update(state='pending_payment')
    order = service.mark_paid(Order.objects.get(pk=order.pk), expected_version=1,
                              actor=service.SYSTEM_ACTOR)
    invoice = only_invoice(order)
    row = invoice.lines.get()
    assert invoice.total == Decimal('0.00') and invoice.voucher_taken == Decimal('100.00')
    assert (row.list_amount, row.net, row.included_case, row.patient_code) == (
        Decimal('100.00'), Decimal('0.00'), True, code)


def test_a_repeated_hook_returns_the_same_invoice(doctor, entity):
    order = pay(new(doctor), doctor)
    first = only_invoice(order)
    assert invoices.issue_for_order(Order.objects.get(pk=order.pk)).pk == first.pk
    assert invoices.issue_for_order(order).pk == first.pk
    assert Invoice.objects.count() == 1 and InvoiceSeries.objects.get().last_number == 1


def test_a_missing_company_rolls_the_payment_back(doctor, entity):
    order = new(doctor)
    Order.objects.filter(pk=order.pk).update(company_code='XX')
    order = Order.objects.get(pk=order.pk)
    order = service.start_payment(order, 'fake', expected_version=1, actor=actor(doctor))
    with pytest.raises(service.HookFailed) as failed:
        service.mark_paid(order, expected_version=2, actor=service.SYSTEM_ACTOR)
    assert isinstance(failed.value.__cause__, invoices.CompanyMissing)
    stored = Order.objects.get(pk=order.pk)
    assert stored.state == 'pending_payment' and stored.paid_at is None
    assert not Invoice.objects.exists() and not InvoiceSeries.objects.exists()
    Order.objects.filter(pk=order.pk).update(company_code='ES')  # the retry works
    paid = service.mark_paid(Order.objects.get(pk=order.pk), expected_version=2,
                             actor=service.SYSTEM_ACTOR)
    assert only_invoice(paid).number.endswith('-000001')


def test_an_ae_invoice_on_the_ae_instance(catalogue_loaded, ae_portal, fake_on):
    user = make_doctor('uae@example.com', country='AE')
    fiscal.create(user, {**COMPANY, 'legal_name': 'Gulf Dental LLC', 'tax_id': '1',
                         'country': 'AE', 'city': 'Dubai'})
    order = service.create_order(user, [line(TPS_AED)], actor=actor(user))
    order = pay(order, user)
    invoice = only_invoice(order)
    assert invoice.number == f'AE-AE-{invoice.year}-000001'
    assert (invoice.currency, invoice.instance, invoice.time_zone) == (
        'AED', 'AE', 'Asia/Dubai')
    assert invoice.seller_vat_number == 'VAT-100' and invoice.total == order.total
    assert invoice.lines.get().vat_percent == Decimal('5.00')


def test_an_exempt_line_for_a_vies_valid_foreign_buyer_stays_exempt(catalogue_loaded):
    portuguese = make_doctor('rui@example.com', country='PT')
    fiscal.create(portuguese, {**COMPANY, 'country': 'PT', 'tax_id': 'PT501964843',
                               'legal_name': 'Primeira Lda'})
    second = fiscal.create(portuguese, {**COMPANY, 'country': 'PT', 'tax_id': 'PT501964843',
                                        'legal_name': 'Segunda Lda'})
    order = new(portuguese, entity=FiscalEntity.objects.get(pk=second.pk))
    assert order.company_code == 'ES' and order.bill_vies_status == 'valid'
    invoice = only_invoice(pay(order, portuguese))
    row = invoice.lines.get()
    assert (row.vat_text, row.tax_category) == (EXEMPT, 'E')


def test_the_seller_name_is_the_orders_not_the_current_companys(doctor, entity):
    order = new(doctor)
    Order.objects.filter(pk=order.pk).update(company_legal_name='Old Name S.L.')
    order = pay(Order.objects.get(pk=order.pk), doctor)
    invoice = only_invoice(order)
    assert invoice.seller_legal_name == 'Old Name S.L.'
    assert invoice.seller_vat_number == 'B93858371'  # the identity is the company's


# --- Every paid path -----------------------------------------------------------------------

def test_a_full_cover_voucher_order_gets_its_invoice(doctor, entity, admin):
    from catalogue import resolver
    from integration.tests.snapshot_rows import GENERAL, pull, voucher
    from subscriptions.tests.helpers import catalogue
    assert pull(admin, 2, catalogue(), vouchers=[voucher(
        GENERAL['id'], 'K7QM2XH9TR', amount='100.00')]) == ('applied', 2)
    order = new(doctor, voucher=resolver.voucher_by_code('K7QM2XH9TR'))
    assert order.state == 'paid' and order.total == Decimal('0.00')
    invoice = only_invoice(order)
    assert invoice.total == Decimal('0.00') and invoice.voucher_taken == Decimal('100.00')
    assert invoice.number.endswith('-000001')
    assert invoice.lines.get().list_amount == Decimal('100.00')


def test_a_confirmed_gateway_payment_gets_its_invoice(doctor, entity):
    from payments import gateways
    order = service.start_payment(new(doctor), 'fake', expected_version=1,
                                  actor=actor(doctor))
    payment = service.open_payment(order)
    result = service.confirm_payment(payment, gateways.GatewayPayment(
        'fake-1', gateways.PAID, order.total, order.currency))
    assert result == service.CONFIRMED
    assert only_invoice(order).number.endswith('-000001')


def test_a_transfer_marked_paid_gets_its_invoice(doctor, entity):
    from catalogue.models import BillingCompany
    from integration.tests.snapshot_rows import snapshot_writes
    with snapshot_writes():
        BillingCompany.objects.filter(code='ES').update(
            bank_account_holder='MyTPDSO Spain S.L.', iban='ES9121000418450200051332',
            bic='CAIXESBBXXX', bank_name='CaixaBank')
    order = new(doctor)
    order = service.start_payment(order, 'bank_transfer', expected_version=order.version,
                                  actor=actor(doctor))
    paid = service.mark_transfer_paid(order, Decimal('100.00'), expected_version=order.version,
                                      actor=service.SYSTEM_ACTOR)
    assert only_invoice(paid).number.endswith('-000001')


# --- Immutability --------------------------------------------------------------------------

def test_an_issued_invoice_refuses_every_change(doctor, entity):
    invoice = only_invoice(pay(new(doctor), doctor))
    line_row = invoice.lines.get()
    invoice.total = Decimal('1.00')
    with pytest.raises(AppendOnly):
        invoice.save()
    with pytest.raises(AppendOnly):
        invoice.save(update_fields=['total'])
    with pytest.raises(AppendOnly):
        invoice.save(update_fields=['total', 'einvoice_status'])
    with pytest.raises(AppendOnly):
        invoice.save(update_fields=[])
    with pytest.raises(AppendOnly):
        invoice.delete()
    with pytest.raises(AppendOnly):
        Invoice.objects.filter(pk=invoice.pk).update(total=1)
    with pytest.raises(AppendOnly):
        Invoice.objects.filter(pk=invoice.pk).delete()
    with pytest.raises(AppendOnly):
        Invoice.objects.bulk_update([invoice], ['total'])
    line_row.net = Decimal('1.00')
    with pytest.raises(AppendOnly):
        line_row.save()
    with pytest.raises(AppendOnly):
        line_row.delete()
    with pytest.raises(AppendOnly):
        InvoiceLine.objects.filter(pk=line_row.pk).update(net=1)
    with pytest.raises(AppendOnly):
        invoice.lines.all().delete()
    stored = Invoice.objects.get(pk=invoice.pk)
    assert stored.total == Decimal('100.00') and stored.lines.get().net == Decimal('100.00')


def test_only_the_einvoice_fields_change(doctor, entity):
    invoice = only_invoice(pay(new(doctor), doctor))
    done = invoices.set_einvoice_status(invoice, 'sent', 'ASP-123')
    assert (done.einvoice_status, done.einvoice_reference) == ('sent', 'ASP-123')
    stored = Invoice.objects.get(pk=invoice.pk)
    assert (stored.einvoice_status, stored.einvoice_reference) == ('sent', 'ASP-123')
    assert stored.einvoice_updated_at is not None and stored.total == Decimal('100.00')
    invoices.set_einvoice_status(stored, 'accepted')
    assert Invoice.objects.get(pk=invoice.pk).einvoice_status == 'accepted'
    with pytest.raises(invoices.BadStatus):
        invoices.set_einvoice_status(stored, 'lost')
    assert Invoice.objects.get(pk=invoice.pk).einvoice_status == 'accepted'


def test_the_order_is_unique_per_invoice(doctor, entity):
    from django.db import IntegrityError, transaction
    invoice = only_invoice(pay(new(doctor), doctor))
    with pytest.raises(IntegrityError), transaction.atomic():
        Invoice.objects.create(
            number='X-1', kind='order', order=invoice.order, company_code='ES',
            instance='EU', year=2026, seller_legal_name='x', currency='EUR', net_total=0,
            discount_total=0, ice_total=0, voucher_taken=0, vat_total=0, total=0,
            issue_date=invoice.issue_date, time_zone='Europe/Madrid', actor_type='system')


# --- A manual sale -------------------------------------------------------------------------

BUYER = {'kind': 'company', 'legal_name': 'Dental Walk-in S.L.', 'tax_id': 'B12345674',
         'country': 'ES', 'address_line1': 'Calle Sol 2', 'city': 'Sevilla',
         'postal_code': '41001', 'billing_email': 'cash@example.com'}


def test_a_manual_invoice_for_a_buyer_with_no_account(catalogue_loaded):
    lines = [{'name': 'Setup', 'net': '100.00', 'vat_percent': '21.00'},
             {'name': 'Extra', 'net': '0.10', 'vat_percent': '21.00', 'quantity': 3},
             {'name': 'Course', 'net': '50.00', 'vat_percent': '0', 'vat_text': EXEMPT}]
    invoice = invoices.issue_manual('ES', BUYER, lines, 'EUR', service.SYSTEM_ACTOR)
    assert invoice.kind == 'manual' and invoice.order is None
    assert invoice.number.endswith('-000001') and invoice.number.startswith('ES-EU-')
    assert invoice.vat_groups == [
        {'percent': '21.00', 'text': '', 'base': '100.10', 'vat': '21.02'},
        {'percent': '0.00', 'text': EXEMPT, 'base': '50.00', 'vat': '0.00'}]
    assert (invoice.net_total, invoice.vat_total, invoice.total) == (
        Decimal('150.10'), Decimal('21.02'), Decimal('171.12'))
    assert [row.tax_category for row in invoice.lines.all()] == ['S', 'S', 'E']
    assert invoice.bill_legal_name == 'Dental Walk-in S.L.' and invoice.bill_country == 'ES'
    assert invoice.bill_entity_id is None and invoice.actor_type == 'system'
    again = invoices.issue_manual('ES', BUYER, lines[:1], 'EUR', service.SYSTEM_ACTOR)
    assert again.number.endswith('-000002')


@pytest.mark.parametrize('buyer, lines, error', [
    ({**BUYER, 'user': 'x'}, [{'name': 'a', 'net': '1', 'vat_percent': '21'}],
     invoices.BadBuyer),
    ({**BUYER, 'kind': 'robot'}, [{'name': 'a', 'net': '1', 'vat_percent': '21'}],
     invoices.BadBuyer),
    ({**BUYER, 'legal_name': ''}, [{'name': 'a', 'net': '1', 'vat_percent': '21'}],
     invoices.BadBuyer),
    (BUYER, [], invoices.NoLines),
    (BUYER, [{'name': 'a', 'net': '1', 'vat_percent': '0'}], invoices.BadLine),
    (BUYER, [{'name': 'a', 'net': '1', 'vat_percent': '101'}], invoices.BadLine),
    (BUYER, [{'name': 'a', 'net': '-1', 'vat_percent': '21'}], invoices.BadLine),
    (BUYER, [{'name': 'a', 'net': 1.5, 'vat_percent': '21'}], invoices.BadLine),
    (BUYER, [{'name': 'a', 'net': '1.005', 'vat_percent': '21'}], invoices.BadLine),
    (BUYER, [{'name': 'a', 'net': '1', 'vat_percent': '21', 'code': 'c' * 65}],
     invoices.BadLine),
    (BUYER, [{'name': 'a', 'net': '1', 'vat_percent': '21', 'patient_code': 'p' * 33}],
     invoices.BadLine),
])
def test_a_bad_manual_invoice_issues_nothing(catalogue_loaded, buyer, lines, error):
    with pytest.raises(error):
        invoices.issue_manual('ES', buyer, lines, 'EUR', service.SYSTEM_ACTOR)
    assert not Invoice.objects.exists() and not InvoiceSeries.objects.exists()


def test_a_manual_invoice_needs_a_known_company(catalogue_loaded):
    with pytest.raises(invoices.CompanyMissing):
        invoices.issue_manual('XX', BUYER, [{'name': 'a', 'net': '1', 'vat_percent': '21'}],
                              'EUR', service.SYSTEM_ACTOR)


# --- Erasure -------------------------------------------------------------------------------

def test_the_invoice_survives_the_erasure_of_the_buyer(doctor, entity, settings):
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 30
    staff = User.objects.create_superuser('staff@example.net', 'Sup3r-secret-pass-42!')
    invoice = only_invoice(pay(new(doctor), doctor))
    assert erase_user(doctor, by=staff)
    # Erasure only hides (9.12); the purge archives, then deletes the entity.
    from datetime import timedelta

    from django.utils import timezone

    from accounts.erasure import purge_erased
    assert purge_erased(now=timezone.now() + timedelta(days=31)) == 1
    assert not FiscalEntity.objects.filter(user=doctor).exists()
    stored = Invoice.objects.get(pk=invoice.pk)
    assert stored.bill_legal_name == 'Clínica Espuny S.L.' and stored.bill_tax_id
    assert stored.lines.count() == 1 and stored.order_id is not None


# --- Concurrency (PostgreSQL only) -----------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_concurrent_payments_get_numbers_with_no_gap(doctor, entity):
    if connection.vendor != 'postgresql':
        pytest.skip('row locks need PostgreSQL')
    orders = [service.start_payment(new(doctor), 'fake', expected_version=1,
                                    actor=actor(doctor)) for _ in range(3)]
    barrier, errors = threading.Barrier(3), []

    def run(order):
        try:
            barrier.wait()
            service.mark_paid(order, expected_version=2, actor=service.SYSTEM_ACTOR)
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
    numbers = sorted(Invoice.objects.values_list('number', flat=True))
    year = Invoice.objects.first().year
    assert numbers == [f'ES-EU-{year}-00000{n}' for n in (1, 2, 3)]
