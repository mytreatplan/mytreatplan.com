"""The invoice PDF and its download (epic-payments entry 10): one test (or more) per row of
the plan's matrix. The PDF's text is asserted on the HTML before conversion (WeasyPrint has
no text extraction); the conversion itself is checked by the `%PDF` header and the response."""
import logging
import re

import pytest
from django.test import Client

from accounts import fiscal
from accounts.models import Profile
from invoices import patients, pdf, service as invoices
from invoices.models import Invoice, InvoiceLine
from invoices.views import invoices_of
from payments import service
from payments.models import OrderLine
from payments.tests.conftest import *  # noqa: F401,F403
from payments.tests.conftest import COMPANY
from payments.tests.test_service import actor, new, pay
from subscriptions.tests.helpers import doctor as make_doctor

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

STAFF = service.SYSTEM_ACTOR
CODE = 'EU-7K3QX9MD'
NAME = 'Marta Quiroga Zubiri'


@pytest.fixture(autouse=True)
def fake_gateway(fake_on):
    return fake_on


@pytest.fixture(autouse=True)
def no_lookup():
    patients.unregister()
    yield
    patients.unregister()


def browser(user):
    client = Client()
    client.force_login(user)
    return client


def invoice_with_patient(doctor, code=CODE):
    order = new(doctor)
    OrderLine.objects.filter(order=order).update(patient_code=code)
    return Invoice.objects.get(order_id=pay(order, doctor).pk)


def manual_for(entity, code=CODE):
    return invoices.issue_manual(
        'ES', {**COMPANY, 'legal_name': 'Clínica Espuny S.L.'},
        [{'name': 'Setup', 'net': '100.00', 'vat_percent': '21.00', 'patient_code': code}],
        'EUR', STAFF, entity=entity)


def knows(*pairs):
    table = dict(pairs)
    seen = []

    def lookup(user, codes):
        seen.append((user, list(codes)))
        return {code: table[code] for code in codes if code in table}
    patients.register(lookup)
    return seen


def spanish(doctor):
    Profile.objects.update_or_create(user=doctor, defaults={'language': 'es'})


def test_an_es_invoice_in_spanish_with_the_name_next_to_the_code(doctor, entity, monkeypatch):
    spanish(doctor)
    knows((CODE, NAME))
    invoice = invoice_with_patient(doctor)
    html = pdf.render_html(invoice, doctor)
    assert 'lang="es"' in html and 'Factura' in html and 'Vendedor' in html
    assert invoice.seller_legal_name in html and invoice.seller_vat_number in html
    assert 'Clínica Espuny S.L.' in html and 'Calle Mayor 1' in html
    assert f'{NAME} ({CODE})' in html
    assert re.search(r'data-total>[\d.,]+ EUR<', html)
    assert '100,00 EUR' in html and invoice.number in html  # Spanish separators
    rendered = []
    real = pdf.render_html
    monkeypatch.setattr(pdf, 'render_html', lambda *args: rendered.append(real(*args)) or rendered[-1])
    response = browser(doctor).get(f'/en/invoices/{invoice.pk}/pdf/')  # an /en/ URL, es profile
    assert 'lang="es"' in rendered[0] and f'{NAME} ({CODE})' in rendered[0]
    assert response.status_code == 200 and response.content.startswith(b'%PDF')
    assert response['Content-Type'] == 'application/pdf'
    assert response['Content-Disposition'] == f'attachment; filename="{invoice.number}.pdf"'


def test_the_pdf_shows_the_vat_groups_and_the_exemption_text(doctor, entity):
    from integration.tests.snapshot_rows import EXEMPT
    invoice = invoices.issue_manual(
        'ES', {**COMPANY}, [{'name': 'Course', 'net': '50.00', 'vat_percent': '0',
                             'vat_text': EXEMPT},
                            {'name': 'Setup', 'net': '100.00', 'vat_percent': '21.00'}],
        'EUR', STAFF, entity=entity)
    html = pdf.render_html(invoice, doctor)
    assert EXEMPT and invoice.lines.get(position=1).vat_text == EXEMPT
    assert [group['text'] for group in invoice.vat_groups] == [EXEMPT, '']
    assert html.count('data-vat-group') == 2 + 1  # the table's attribute names, per group row
    assert html.count(EXEMPT) == 2  # under the line and in its VAT group
    assert '50,00 EUR' not in html and '50.00 EUR' in html  # English: the doctor has no profile language
    assert '21.00 EUR' in html and '21%' in html and '0%' in html


def test_an_erased_patient_shows_only_the_code(doctor, entity):
    knows(('EU-OTHER000', 'Someone Else'))
    html = pdf.render_html(invoice_with_patient(doctor), doctor)
    assert f'{CODE}<' in html and 'Someone Else' not in html and f'({CODE})' not in html


def test_no_hook_shows_only_the_code(doctor, entity):
    html = pdf.render_html(invoice_with_patient(doctor), doctor)
    assert f'{CODE}<' in html and f'({CODE})' not in html


def test_a_hook_that_raises_shows_only_the_code_and_logs_no_name(doctor, entity, caplog):
    def broken(user, codes):
        raise RuntimeError(f'cannot find {NAME}')
    patients.register(broken)
    with caplog.at_level(logging.DEBUG):
        html = pdf.render_html(invoice_with_patient(doctor), doctor)
    assert f'{CODE}<' in html
    assert caplog.records and NAME not in caplog.text and 'RuntimeError' in caplog.text


def test_names_for_is_empty_without_codes_and_returns_only_asked_codes(doctor):
    seen = knows((CODE, NAME), ('EU-ZZZZZZZZ', 'Not asked'))
    assert patients.names_for(doctor, ['', '']) == {} and seen == []
    assert patients.names_for(doctor, [CODE]) == {CODE: NAME}
    assert patients.names_for(doctor, ['EU-NOBODY00']) == {}
    with pytest.raises(ValueError):
        patients.register(lambda user, codes: {})


def test_a_credit_note_is_titled_negative_and_names_the_original(doctor, entity):
    spanish(doctor)
    invoice = invoice_with_patient(doctor)
    note = invoices.issue_credit_note(invoice, reason='error', actor=STAFF)
    html = pdf.render_html(note, doctor)
    assert 'Factura rectificativa' in html and note.number in html
    assert f'Rectifica la factura {invoice.number}' in html
    assert f'-{invoice.total}' in html.replace(',', '.').replace('\xa0', '')
    response = browser(doctor).get(f'/en/invoices/{note.pk}/pdf/')
    assert response.status_code == 200 and response.content.startswith(b'%PDF')
    assert f'{note.number}.pdf' in response['Content-Disposition']


def test_ae_gets_english_whatever_the_profile_language(ae_portal, catalogue_loaded):
    from integration.tests.snapshot_rows import TPS_AED
    from payments.tests.test_service import line
    ae_portal.LANGUAGES = ae_portal.REGION_PROFILE.language_choices  # as at start-up
    user = make_doctor('uae@example.com', country='AE')
    Profile.objects.create(user=user, language='es')
    fiscal.create(user, {**COMPANY, 'legal_name': 'Gulf Dental LLC', 'tax_id': '1',
                         'country': 'AE', 'city': 'Dubai'})
    order = service.create_order(user, [line(TPS_AED)], actor=actor(user))
    invoice = Invoice.objects.get(order_id=pay(order, user).pk)
    html = pdf.render_html(invoice, user)
    assert 'lang="en"' in html and 'Seller' in html and 'Vendedor' not in html
    assert 'AED' in html


def test_the_response_is_never_cached(doctor, entity):
    invoice = invoice_with_patient(doctor)
    response = browser(doctor).get(f'/en/invoices/{invoice.pk}/pdf/')
    control = response['Cache-Control']
    assert 'no-store' in control and 'private' in control


def test_no_patient_name_is_in_the_url_or_the_log(doctor, entity, caplog):
    knows((CODE, NAME))
    invoice = invoice_with_patient(doctor)
    with caplog.at_level(logging.DEBUG):
        response = browser(doctor).get(f'/en/invoices/{invoice.pk}/pdf/')
    assert NAME not in response.request['PATH_INFO'] and NAME not in caplog.text
    assert NAME not in browser(doctor).get('/en/invoices/').content.decode()


def test_another_doctor_gets_a_404(doctor, entity):
    invoice = invoice_with_patient(doctor)
    other = make_doctor('other@example.com')
    assert browser(other).get(f'/en/invoices/{invoice.pk}/pdf/').status_code == 404
    page = browser(other).get('/en/invoices/').content.decode()
    assert f'data-invoice="{invoice.number}"' not in page
    assert f'data-invoice="{invoice.number}"' in browser(doctor).get('/en/invoices/').content.decode()


def test_a_credit_note_belongs_to_its_originals_owner(doctor, entity):
    note = invoices.issue_credit_note(invoice_with_patient(doctor), reason='error', actor=STAFF)
    other = make_doctor('other@example.com')
    assert browser(other).get(f'/en/invoices/{note.pk}/pdf/').status_code == 404
    assert f'data-invoice="{note.number}"' in browser(doctor).get('/en/invoices/').content.decode()


def test_a_manual_invoice_with_the_doctors_entity_is_listed_and_downloadable(doctor, entity):
    invoice = manual_for(entity)
    assert invoice.bill_entity_id == entity.pk and invoice.bill_entity_version == entity.version
    page = browser(doctor).get('/en/invoices/').content.decode()
    assert invoice.number in page and f'/invoices/{invoice.pk}/pdf/' in page
    assert browser(doctor).get(f'/en/invoices/{invoice.pk}/pdf/').status_code == 200
    assert browser(make_doctor('other@example.com')).get(
        f'/en/invoices/{invoice.pk}/pdf/').status_code == 404


def test_a_manual_credit_note_follows_its_entity(doctor, entity):
    note = invoices.issue_credit_note(manual_for(entity), reason='goodwill', actor=STAFF)
    assert browser(doctor).get(f'/en/invoices/{note.pk}/pdf/').status_code == 200


def test_an_unlinked_or_orphan_invoice_is_a_404_for_everyone(doctor, entity):
    unlinked = manual_for(None)
    gone = manual_for(entity)
    orphan = invoice_with_patient(doctor)
    # An erased account takes its entities and orders' user link away; the invoices stay.
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('UPDATE payments_order SET user_id = NULL WHERE id = %s',
                       [str(orphan.order_id).replace('-', '')
                        if connection.vendor == 'sqlite' else orphan.order_id])
    entity.delete()
    for invoice in (unlinked, gone, orphan):
        assert browser(doctor).get(f'/en/invoices/{invoice.pk}/pdf/').status_code == 404
        assert invoice.pk not in [row.pk for row in invoices_of(doctor)]


def test_staff_gets_a_404_and_anonymous_goes_to_login(doctor, entity, django_user_model):
    invoice = invoice_with_patient(doctor)
    staff = django_user_model.objects.create_user('staff@example.com', 'x', is_staff=True)
    assert browser(staff).get(f'/en/invoices/{invoice.pk}/pdf/').status_code == 404
    assert browser(staff).get('/en/invoices/').status_code == 404
    for url in ('/en/invoices/', f'/en/invoices/{invoice.pk}/pdf/'):
        response = Client().get(url)
        assert response.status_code == 302 and '/login' in response['Location']


def test_the_list_is_newest_first_without_names(doctor, entity):
    knows((CODE, NAME))
    first = invoice_with_patient(doctor)
    note = invoices.issue_credit_note(first, reason='error', actor=STAFF)
    page = browser(doctor).get('/en/invoices/').content.decode()
    assert page.index(note.number) < page.index(first.number)
    assert NAME not in page and CODE not in page
    assert 'Credit note' in page and 'data-download' in page


def test_an_empty_list_says_so(doctor):
    assert 'data-no-invoices' in browser(doctor).get('/en/invoices/').content.decode()


def test_the_home_has_an_invoices_tile(doctor):
    page = browser(doctor).get('/en/home/').content.decode()
    assert 'data-tile="invoices"' in page and '/invoices/' in page


def test_the_models_are_untouched():
    assert not InvoiceLine._meta.get_field('patient_code').null


def test_the_renderer_loads_only_the_fonts(doctor, entity):
    fetcher = pdf._fonts_only()
    for url in ('file:///etc/hosts', 'data:text/plain,x', 'http://example.com/a.css'):
        with pytest.raises(ValueError):
            fetcher.fetch(url)
    assert pdf.font_urls() and all(url.endswith('.woff2') for url in pdf.font_urls())
    # And a real render still gets its fonts through it.
    assert pdf.render(invoice_with_patient(doctor), doctor).startswith(b'%PDF')


def test_a_manual_invoice_keeps_its_entity_when_the_doctor_removes_it(doctor, entity):
    from accounts import fiscal
    invoice = manual_for(entity)
    assert fiscal.remove(doctor, entity.pk) == fiscal.ARCHIVED
    page = browser(doctor).get('/en/invoices/').content.decode()
    assert f'data-invoice="{invoice.number}"' in page
    assert browser(doctor).get(f'/en/invoices/{invoice.pk}/pdf/').status_code == 200


def test_the_invoices_tile_is_a_doctors_only(doctor, django_user_model):
    staff = django_user_model.objects.create_user('staff@example.com', 'x', is_staff=True)
    assert 'data-tile="invoices"' in browser(doctor).get('/en/home/').content.decode()
    assert 'data-tile="invoices"' not in browser(staff).get('/en/home/').content.decode()
