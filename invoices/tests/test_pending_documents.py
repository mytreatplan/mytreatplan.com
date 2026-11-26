"""Pending-payment documents in the doctor's invoices list (epic-ordering 9.19): one test (or
more) per row of the plan's matrix, the language and the patient-name rule. The PDF's text
is asserted on the HTML before conversion (as test_pdf.py); the conversion by the `%PDF`
header and the response."""
import re
import uuid
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from catalogue.models import BillingCompany
from integration import transfers
from integration.tests.snapshot_rows import snapshot_writes
from invoices import pdf
from invoices.models import Invoice
from invoices.tests.test_pdf import CODE, NAME, browser, knows, no_lookup, spanish
from invoices.views import pending_documents_of
from payments import service
from payments.models import Order, OrderLine
from payments.tests.conftest import *
from payments.tests.test_bank_transfer import bank, switch_to_card
from payments.tests.test_service import actor, new, pay
from subscriptions.tests.helpers import doctor as make_doctor

__all__ = ['bank', 'no_lookup']  # fixtures (no_lookup is autouse)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls', 'bank')]

IBAN = 'ES91 2100 0418 4502 0005 1332'
LIST = '/en/invoices/'


@pytest.fixture(autouse=True)
def fake_gateway(fake_on):
    return fake_on


def approved(doctor, code=CODE):
    """An order of `doctor`'s for a patient, approved for a bank transfer."""
    from patients.models import Patient
    Patient.objects.get_or_create(code=code, defaults={
        'doctor': doctor, 'actor_type': 'doctor', 'actor_uuid': doctor.uid})
    order = new(doctor)
    OrderLine.objects.filter(order=order).update(patient_code=code)
    return service.start_payment(order, 'bank_transfer', expected_version=order.version,
                                 actor=actor(doctor))


def url(order):
    return f'/en/invoices/pending/{order.pk}/pdf/'


def page(user):
    return browser(user).get(LIST).content.decode()


def staff_does(order, state):
    """Staff confirm or cancel at the admin site (integration.transfers.update)."""
    order.refresh_from_db()
    return transfers.update(order.pk, order.version, {'state': state},
                            actor=service.admin_actor(uuid.uuid4()))


# --- Transfer approved -----------------------------------------------------------------------

def test_an_approved_transfer_is_listed_as_pending_without_a_number(doctor, entity):
    order = approved(doctor)
    html = page(doctor)
    assert f'data-pending-document="{order.reference}"' in html
    assert 'data-pending-badge' in html and 'Pending payment' in html
    assert 'This is not an invoice' in html and 'data-invoice=' not in html
    assert f'/invoices/pending/{order.pk}/pdf/' in html and 'data-no-invoices' not in html
    assert not Invoice.objects.filter(order=order).exists()


def test_the_pdf_is_the_pending_document_with_the_reference_and_the_bank_details(
        doctor, entity):
    order = approved(doctor)
    html = pdf.render_pending_html(order, doctor)
    assert 'Pending payment document' in html and 'This is not an invoice' in html
    assert order.reference in html and IBAN in html
    assert 'CAIXESBBXXX' in html and 'MyTPDSO Spain S.L.' in html
    assert 'data-number' not in html and '>Invoice<' not in html
    assert not re.search(r'ES-\w+-\d{4}-\d{6}', html)  # no invoice number
    assert 'Calle Mayor 1' in html  # the buyer
    assert re.search(r'data-total>[\d.,]+ EUR<', html)
    response = browser(doctor).get(url(order))
    assert response.status_code == 200 and response.content.startswith(b'%PDF')
    assert response['Content-Type'] == 'application/pdf'
    assert (response['Content-Disposition']
            == f'attachment; filename="{order.reference}-pending.pdf"')
    control = response['Cache-Control']
    assert 'no-store' in control and 'private' in control


def test_the_seller_comes_from_the_live_billing_company(doctor, entity):
    order = approved(doctor)
    with snapshot_writes():
        BillingCompany.objects.filter(code='ES').update(address='Paseo Nuevo 9\n28001 Madrid')
    html = pdf.render_pending_html(order, doctor)
    assert 'Paseo Nuevo 9' in html and '28001 Madrid' in html
    assert order.company_legal_name in html


def test_seller_and_bank_details_come_from_the_active_company_of_the_code(doctor, entity):
    order = approved(doctor)
    active = BillingCompany.objects.get(code='ES', active=True)
    with snapshot_writes():
        BillingCompany.objects.create(
            id=uuid.UUID(int=0), code='ES', legal_name='Old Seller S.L.',
            address='Calle Vieja 1', country='ES', time_zone='Europe/Madrid',
            vat_number='B00000000', bank_account_holder='Old Seller S.L.',
            iban='ES7620770024003102575766', bic='CAHMESMMXXX', bank_name='Old Bank',
            active=False)
    assert BillingCompany.objects.filter(code='ES').order_by('id').first().active is False
    html = pdf.render_pending_html(order, doctor)
    assert active.address.splitlines()[0] in html and IBAN in html
    assert 'Calle Vieja 1' not in html and 'B00000000' not in html
    assert 'ES76 2077' not in html and 'Old Bank' not in html


def test_the_pending_document_is_dated_by_the_order_and_sorted_among_the_invoices(
        doctor, entity):
    earlier = Invoice.objects.get(order_id=pay(new(doctor), doctor).pk)
    order = approved(doctor)
    html = page(doctor)
    assert html.index(f'data-pending-document="{order.reference}"') < html.index(
        f'data-invoice="{earlier.number}"')
    order.refresh_from_db()
    madrid = order.pending_at.astimezone(ZoneInfo('Europe/Madrid')).date()
    assert pdf.pending_date(order) == madrid  # the company's time zone, as an invoice


# --- Paid ------------------------------------------------------------------------------------

def test_once_paid_the_invoice_takes_its_place(doctor, entity):
    order = approved(doctor)
    staff_does(order, 'paid')
    invoice = Invoice.objects.get(order=order)
    html = page(doctor)
    assert f'data-invoice="{invoice.number}"' in html
    assert 'data-pending-document' not in html
    assert browser(doctor).get(url(order)).status_code == 404


# --- Cancelled -------------------------------------------------------------------------------

def test_cancelled_by_the_doctor_it_leaves_the_list(doctor, entity):
    order = approved(doctor)
    service.cancel(order, expected_version=order.version, actor=actor(doctor))
    assert 'data-pending-document' not in page(doctor)
    assert browser(doctor).get(url(order)).status_code == 404


def test_cancelled_by_staff_it_leaves_the_list(doctor, entity):
    order = approved(doctor)
    staff_does(order, 'cancelled')
    assert Order.objects.get(pk=order.pk).state == Order.CANCELLED
    assert 'data-pending-document' not in page(doctor)
    assert browser(doctor).get(url(order)).status_code == 404


# --- Switched to card ------------------------------------------------------------------------

def test_switched_to_card_there_is_no_pending_row(doctor, entity):
    order = approved(doctor)
    switch_to_card(order)
    assert Order.objects.get(pk=order.pk).state == Order.PENDING_PAYMENT
    assert not pending_documents_of(doctor).exists()
    assert 'data-pending-document' not in page(doctor)
    assert browser(doctor).get(url(order)).status_code == 404


# --- Another doctor, staff, anonymous ---------------------------------------------------------

def test_another_doctor_gets_a_404_and_no_row(doctor, entity):
    order = approved(doctor)
    other = make_doctor('other@example.com')
    assert browser(other).get(url(order)).status_code == 404
    assert order.reference not in page(other)


def test_staff_gets_a_404_and_anonymous_goes_to_login(doctor, entity, django_user_model):
    order = approved(doctor)
    staff = django_user_model.objects.create_user('staff@example.com', 'x', is_staff=True)
    assert browser(staff).get(url(order)).status_code == 404
    assert browser(staff).get(LIST).status_code == 404
    response = Client().get(url(order))
    assert response.status_code == 302 and '/login' in response['Location']


def test_an_unknown_order_is_a_404(doctor, entity):
    assert browser(doctor).get(f'/en/invoices/pending/{uuid.uuid4()}/pdf/').status_code == 404


# --- No bank details -------------------------------------------------------------------------

def test_without_bank_details_the_pdf_says_to_contact_us(doctor, entity):
    order = approved(doctor)
    with snapshot_writes():
        BillingCompany.objects.filter(code='ES').update(
            bank_account_holder='', iban='', bic='', bank_name='')
    html = pdf.render_pending_html(order, doctor)
    assert 'The bank details are not available right now. Please contact us.' in html
    assert 'data-transfer-iban' not in html and order.reference in html
    assert browser(doctor).get(url(order)).status_code == 200


# --- Language and patient names --------------------------------------------------------------

def test_the_document_is_in_the_doctors_language(doctor, entity):
    spanish(doctor)
    order = approved(doctor)
    html = pdf.render_pending_html(order, doctor)
    assert 'lang="es"' in html
    assert 'Documento pendiente de pago' in html and 'Esto no es una factura' in html
    assert 'Referencia del pedido' in html and 'Titular de la cuenta' in html
    listing = browser(doctor).get('/es/invoices/').content.decode()
    assert 'Pendiente de pago' in listing and 'Esto no es una factura' in listing


def test_the_name_is_on_the_pdf_only(doctor, entity):
    knows((CODE, NAME))
    order = approved(doctor)
    assert f'{NAME} ({CODE})' in pdf.render_pending_html(order, doctor)
    html = page(doctor)
    assert NAME not in html and CODE not in html


def test_the_name_is_not_in_the_url(doctor, entity):
    knows((CODE, NAME))
    order = approved(doctor)
    response = browser(doctor).get(url(order))
    assert NAME not in response.request['PATH_INFO']
