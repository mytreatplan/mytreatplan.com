"""The case wizard from step 2 on (epic-ordering 9.16): one test per row of the plan's matrix,
the `?next=` returns to the step, what the session holds, and the old URL. The service's rules
are tested in test_service.py and test_virtuaortho.py; here only what the steps do with them."""
import json
import re
import uuid
from urllib.parse import quote

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from accounts import makers, shipping
from cart import service
from cart.models import CartItem
from cart.tests.test_virtuaortho import ADDRESS
from patients import service as patients_service
from patients.models import WorkOrder
from patients.tests.conftest import BRANDED, ICE, SPARK_ONE, VO_ONE, VO_TWIN

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]
CART = '/en/cart/'
CREDENTIALS = {'username': 'dr-user', 'password': 'dr-password'}


def browser(user):
    client = Client()
    client.force_login(user)
    return client


def page(response):
    return response.content.decode()


def url(patient, step, language='en'):
    return f'/{language}/start-a-case/{patient.code}/{step}/'


def go(client, patient, step, current=None, language='en', **data):
    """POST one step: `current` is the wizard's own name of the step (options_tps or
    options_vo for the URL step "options")."""
    return client.post(url(patient, step, language),
                       {f'case_{patient.code}-current_step': current or step, **data})


def tps(client, patient, maker='invisalign', size='medium', add_on=(), kind='brand', **more):
    """The three TPS steps; the last response."""
    go(client, patient, 'product', product='tps')
    go(client, patient, 'maker', kind=kind, maker=maker, **more)
    return go(client, patient, 'options', 'options_tps', size=size, add_on=list(add_on))


def vo(client, doctor, patient, variant='digitaltwin', item='case:medium', add_on=(), **more):
    go(client, patient, 'product', product='vo')
    go(client, patient, 'vo_type', variant=variant)
    return go(client, patient, 'options', 'options_vo', item=item, add_on=list(add_on),
              address_id=str(doctor.address.pk), **more)


@pytest.fixture
def ready(doctor, entity, patient):
    makers.save(doctor, 'invisalign', CREDENTIALS)
    return patient


@pytest.fixture
def ready_vo(advanced, entity, patient):
    advanced.address = shipping.create(advanced, ADDRESS)
    makers.save(advanced, 'invisalign', CREDENTIALS)
    return patient


@pytest.fixture
def basic(advanced, other_doctor):
    """A Basic doctor of the same catalogue, ready to order but not offered VirtuaOrtho."""
    from accounts import fiscal
    fiscal.create(other_doctor, {'kind': 'company', 'legal_name': 'Otra S.L.',
                                 'tax_id': 'B12345674', 'country': 'ES',
                                 'address_line1': 'Calle 1', 'city': 'Madrid',
                                 'postal_code': '28001'})
    other_doctor.address = shipping.create(other_doctor, ADDRESS)
    other_doctor.patient = patients_service.create_patient(
        other_doctor, first_name='Eva',
        actor=patients_service.Actor('doctor', other_doctor.uid))
    return other_doctor


def live_order(patient, product_id, stage='assigned'):
    return WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(), stage=stage,
                                    product_id=product_id, actor_type='system')


# --- The steps ---------------------------------------------------------------------------------

def test_tps_happy_path_adds_the_item_and_the_cart_shows_it(doctor, ready):
    client = browser(doctor)
    first = go(client, ready, 'product', product='tps')
    assert first.status_code == 302 and first['Location'] == url(ready, 'maker')
    second = go(client, ready, 'maker', kind='brand', maker='invisalign')
    assert second['Location'] == url(ready, 'options')
    html = page(client.get(url(ready, 'options')))
    for code in ('JUNIOR-1', 'JUNIOR-2', 'BYTE-SYNC'):
        assert f'data-add-on="{code}"' in html
    last = go(client, ready, 'options', 'options_tps', size='medium', add_on=['JUNIOR-2'])
    assert last.status_code == 302 and last['Location'] == CART
    cart = page(client.get(CART))
    assert f'data-cart-item="{ready.code}"' in cart and 'Invisalign' in cart
    assert 'Junior II' in cart and 'TPS branded medium' in cart and 'Ana Pérez' in cart
    item = CartItem.objects.get()
    assert (item.add_on_codes, str(item.product_id)) == (['JUNIOR-2'], BRANDED['medium'])
    # The wizard is empty again: nothing of it stays in the session.
    assert client.session.get(f'wizard_case_{ready.code}', {}).get('step_data', {}) == {}


def test_a_missing_maker_login_is_refused_on_the_maker_step_with_the_save_link(doctor, ready):
    client, patient = browser(doctor), ready
    go(client, patient, 'product', product='tps')
    html = page(client.get(url(patient, 'maker')))
    link = f'/en/settings/maker/smartee/?next={quote(url(patient, "maker"), safe="")}'
    assert f'href="{link}"' in html and 'data-missing-login="smartee"' in html
    assert 'data-missing-login="invisalign"' not in html
    refused = go(client, patient, 'maker', kind='brand', maker='smartee')
    assert refused.status_code == 200 and 'data-error' in page(refused)
    assert 'Save your login for this maker first.' in page(refused)
    # The wizard did not move on.
    assert client.get(url(patient, 'options'))['Location'] == url(patient, 'maker')
    saved = client.post(link, {'username': 'u', 'password': 'p', 'next': url(patient, 'maker')})
    assert saved.status_code == 302 and saved['Location'] == url(patient, 'maker')
    assert go(client, patient, 'maker', kind='brand', maker='smartee').status_code == 302


def test_not_decided_and_other_never_block_and_other_needs_a_name(doctor, entity, patient):
    client = browser(doctor)
    go(client, patient, 'product', product='tps')
    assert go(client, patient, 'maker', kind='brand', maker='undecided').status_code == 302
    nameless = go(client, patient, 'maker', kind='brand', maker='other')
    assert nameless.status_code == 200 and 'Enter the name of the maker' in page(nameless)
    assert go(client, patient, 'maker', kind='brand', maker='other',
              other_name='Acme').status_code == 302
    assert go(client, patient, 'options', 'options_tps', size='short').status_code == 302
    assert 'Acme' in page(client.get(CART))


def test_in_house_ice_has_no_maker(doctor, ready):
    client = browser(doctor)
    go(client, ready, 'product', product='tps')
    go(client, ready, 'maker', kind='in_house')
    html = page(client.get(url(ready, 'options')))
    assert html.index('data-size-row="ice"') < html.index('data-size-row="short"')
    row = html[html.index('data-size-row="ice"'):]
    assert re.search(r'data-price>[^<]*50', row[:row.index('</label>')])
    assert go(client, ready, 'options', 'options_tps', size='ice').status_code == 302
    item = CartItem.objects.get()
    assert str(item.product_id) == ICE and item.maker == ''
    assert 'In-house printing' in page(client.get(CART)) and '(ICE)' in page(client.get(CART))


def test_ice_with_a_maker_and_in_house_prices(doctor, ready):
    client = browser(doctor)
    assert tps(client, ready, size='ice').status_code == 302
    assert CartItem.objects.get().maker == 'invisalign'


def test_ice_is_not_listed_when_not_offered(doctor, ready, admin):
    from integration.tests.snapshot_rows import pull
    from patients.tests.conftest import ice_catalogue
    section = ice_catalogue()
    section['products'] = [row for row in section['products'] if row['id'] != ICE]
    for row in section['subscriptions'] + section.get('manual_subscriptions', []):
        row['products'] = [pid for pid in row['products'] if pid != ICE]
    assert pull(admin, 2, section) == ('applied', 2)
    client = browser(doctor)
    go(client, ready, 'product', product='tps')
    go(client, ready, 'maker', kind='in_house')
    html = page(client.get(url(ready, 'options')))
    assert 'data-size-row="short"' in html and 'data-size-row="ice"' not in html


def test_a_tampered_add_on_is_refused_on_the_options_step_and_keeps_the_choices(doctor, ready):
    client = browser(doctor)
    response = tps(client, ready, size='short', add_on=['JUNIOR-1'])
    html = page(response)
    assert response.status_code == 200 and 'data-error' in html
    assert 'not available for this service' in html and not CartItem.objects.exists()
    assert re.search(r'value="short"[^>]*checked', html)
    assert re.search(r'value="JUNIOR-1"[^>]*checked', html)
    # Still at the options step, with the maker kept.
    assert 'checked' in page(client.get(url(ready, 'maker')))
    assert go(client, ready, 'options', 'options_tps', size='short').status_code == 302


def test_a_live_work_order_refuses_on_the_options_step(doctor, ready):
    live_order(ready, BRANDED['medium'])
    response = tps(browser(doctor), ready, size='medium')
    assert response.status_code == 200 and 'case in progress' in page(response)
    assert not CartItem.objects.exists()


def test_a_live_ice_work_order_lets_the_planning_through(doctor, ready):
    live_order(ready, ICE)
    assert tps(browser(doctor), ready, size='medium').status_code == 302
    assert str(CartItem.objects.get().product_id) == BRANDED['medium']


def test_no_ice_when_any_live_work_order(doctor, ready):
    live_order(ready, ICE)
    response = tps(browser(doctor), ready, size='ice')
    assert response.status_code == 200 and 'case in progress' in page(response)


def test_back_keeps_the_earlier_choices(doctor, ready):
    client = browser(doctor)
    tps(client, ready, maker='invisalign', size='long', add_on=['ONE-ARCH'])
    CartItem.objects.all().delete()
    go(client, ready, 'product', product='tps')
    go(client, ready, 'maker', kind='brand', maker='invisalign')
    html = page(client.get(url(ready, 'maker')))
    assert re.search(r'value="brand"[^>]*checked', html)
    assert re.search(r'<option value="invisalign" selected>', html)
    options = page(client.get(url(ready, 'options')))
    assert f'href="{url(ready, "maker")}"' in options and 'data-back' in options
    assert 'data-step-number' in options and 'Step 3 of 3' in options
    first = page(client.get(url(ready, 'product')))
    assert 'href="/en/start-a-case/"' in first  # Back from the first step: the patients


def test_the_included_cases_show_on_the_tps_options_step(doctor, ready, monkeypatch):
    from catalogue import resolver
    monkeypatch.setattr(resolver, 'included_cases', lambda subscription, category: 2)
    client = browser(doctor)
    go(client, ready, 'product', product='tps')
    go(client, ready, 'maker', kind='brand', maker='undecided')
    html = page(client.get(url(ready, 'options')))
    assert 'includes 2 branded TPS cases per year' in html


# --- VirtuaOrtho -------------------------------------------------------------------------------

def test_virtuaortho_digitaltwin_adds_the_item_with_the_default_address(advanced, ready_vo):
    client = browser(advanced)
    go(client, ready_vo, 'product', product='vo')
    assert go(client, ready_vo, 'vo_type', variant='digitaltwin')['Location'] == \
        url(ready_vo, 'options')
    html = page(client.get(url(ready_vo, 'options')))
    assert 'data-address-select' in html and 'Clínica Madrid · Madrid' in html
    assert f'value="{advanced.address.pk}" selected' in html
    assert 'data-add-on="ODB-DT"' not in html and 'data-add-on="BYTE-SYNC"' in html
    assert 'one:case' not in html and 'spark' not in html  # DigitalTwin: no Spark on Demand
    assert '879' in html  # the ODB under DigitalTwin, priced with ODB DigitalTwin (559 + 320)
    response = go(client, ready_vo, 'options', 'options_vo', item='case:medium',
                  add_on=['BYTE-SYNC'], address_id=str(advanced.address.pk))
    assert response['Location'] == CART
    item = CartItem.objects.get()
    assert str(item.product_id) == VO_TWIN['medium'] and item.add_on_codes == ['BYTE-SYNC']
    assert item.shipping_address_id == advanced.address.pk and item.maker == ''
    assert '1,320.00 EUR' in page(client.get(CART))


def test_virtuaortho_one_lists_spark_and_the_one_rows(advanced, ready_vo):
    client = browser(advanced)
    go(client, ready_vo, 'product', product='vo')
    go(client, ready_vo, 'vo_type', variant='one')
    html = page(client.get(url(ready_vo, 'options')))
    assert f'value="spark:{SPARK_ONE}"' in html and 'per aligner' in html
    assert 'includes ODB DigitalTwin' not in html and 'data-add-on="ODB-DT"' not in html
    assert go(client, ready_vo, 'options', 'options_vo', item='case:short',
              address_id=str(advanced.address.pk)).status_code == 302
    assert str(CartItem.objects.get().product_id) == VO_ONE['short']


def test_a_spark_quantity_outside_the_range_re_renders_the_options_with_the_message(
        advanced, ready_vo):
    client = browser(advanced)
    response = vo(client, advanced, ready_vo, variant='one', item=f'spark:{SPARK_ONE}',
                  **{f'quantity_{SPARK_ONE}': '3'})
    html = page(response)
    assert response.status_code == 200 and not CartItem.objects.exists()
    assert 'number of aligners within the range' in html
    assert re.search(rf'value="spark:{SPARK_ONE}"[^>]*checked', html)
    assert 'value="3"' in html
    ok = go(client, ready_vo, 'options', 'options_vo', item=f'spark:{SPARK_ONE}',
            address_id=str(advanced.address.pk), **{f'quantity_{SPARK_ONE}': '5'})
    assert ok.status_code == 302 and CartItem.objects.get().quantity == 5


def test_retainers_are_listed_only_after_a_finished_treatment(advanced, ready_vo):
    client = browser(advanced)
    go(client, ready_vo, 'product', product='vo')
    go(client, ready_vo, 'vo_type', variant='digitaltwin')
    assert 'value="retainers"' not in page(client.get(url(ready_vo, 'options')))
    live_order(ready_vo, VO_ONE['short'], stage='approved')
    assert 'value="retainers"' in page(client.get(url(ready_vo, 'options')))
    WorkOrder.objects.all().delete()
    forged = go(client, ready_vo, 'options', 'options_vo', item='retainers',
                address_id=str(advanced.address.pk))
    assert forged.status_code == 200 and 'data-error' in page(forged)
    assert not CartItem.objects.exists()


def test_without_an_address_the_options_step_links_to_add_one_and_back(advanced, entity, patient):
    client = browser(advanced)
    go(client, patient, 'product', product='vo')
    go(client, patient, 'vo_type', variant='one')
    html = page(client.get(url(patient, 'options')))
    link = f'/en/settings/shipping/add/?next={quote(url(patient, "options"), safe="")}'
    assert 'data-shipping-block' in html and f'href="{link}"' in html
    saved = client.post(link, {**ADDRESS, 'next': url(patient, 'options')})
    assert saved.status_code == 302 and saved['Location'] == url(patient, 'options')
    assert 'data-shipping-block' not in page(client.get(url(patient, 'options')))


def test_a_doctor_without_a_virtuaortho_offer_sees_the_card_greyed_out(basic):
    doctor, patient = basic, basic.patient
    client = browser(doctor)
    html = page(client.get(url(patient, 'product')))
    card = html[html.index('data-card="vo"'):]
    card = card[:card.index('</label>')]
    assert 'disabled' in card and 'Not included in your plan' in card
    assert 'href="/en/upgrade/"' in html and 'data-card="tps"' in html
    assert not re.search(r'data-card="tps"[^>]*>\s*<input[^>]*disabled', html)
    # A forged post of it is invalid: no step moves on.
    forged = go(client, patient, 'product', product='vo')
    assert forged.status_code == 200 and not CartItem.objects.exists()
    assert client.get(url(patient, 'vo_type'))['Location'] == url(patient, 'product')
    assert tps(browser(doctor), patient, maker='undecided').status_code == 302


# --- Access, redirects, session -----------------------------------------------------------------

def test_the_old_step_two_address_redirects_to_the_first_step(doctor, ready):
    response = browser(doctor).get(f'/en/start-a-case/{ready.code}/')
    assert response.status_code == 302 and response['Location'] == url(ready, 'product')
    assert browser(doctor).get('/en/start-a-case/EU-NOSUCH00/').status_code == 404


def test_other_doctors_staff_inactive_patients_and_anonymous(doctor, other_doctor, entity, ready):
    assert browser(other_doctor).get(url(ready, 'product')).status_code == 404
    assert go(browser(other_doctor), ready, 'product', product='tps').status_code == 404
    staff = get_user_model().objects.create_user('staff@example.com', 'violet-orbit-canyon-42',
                                                 is_staff=True)
    assert browser(staff).get(url(ready, 'product')).status_code == 404
    assert Client().get(url(ready, 'product')).status_code == 302
    ready.status = 'inactive'
    ready.save()
    assert browser(doctor).get(url(ready, 'product')).status_code == 404


def test_a_later_step_without_the_earlier_ones_goes_back_and_done_adds_nothing(doctor, ready):
    client = browser(doctor)
    for step in ('maker', 'options', 'vo_type', 'nothing'):
        assert client.get(url(ready, step))['Location'] == url(ready, 'product'), step
    go(client, ready, 'product', product='tps')
    assert client.get(url(ready, 'options'))['Location'] == url(ready, 'maker')
    # The "done" address never adds anything (the item is added by the last step's POST).
    assert client.get(url(ready, 'done'))['Location'] == url(ready, 'product')
    assert not CartItem.objects.exists()


def test_without_a_fiscal_entity_every_step_shows_the_block_back_to_that_step(doctor, patient):
    client = browser(doctor)
    html = page(client.get(url(patient, 'product')))
    assert 'data-fiscal-block' in html and 'data-wizard-form' not in html
    assert f'next={quote(url(patient, "product"), safe="")}' in html
    assert go(client, patient, 'product', product='tps').status_code == 200
    assert not CartItem.objects.exists()


def test_the_session_holds_only_the_patient_code_and_the_choices(doctor, ready):
    client = browser(doctor)
    go(client, ready, 'product', product='tps')
    go(client, ready, 'maker', kind='brand', maker='other', other_name='Acme')
    stored = {key: client.session[key] for key in client.session.keys()
              if key.startswith('wizard_')}
    assert list(stored) == [f'wizard_case_{ready.code}']
    text = json.dumps(stored)
    assert 'csrfmiddlewaretoken' not in text and 'Ana' not in text and 'Pérez' not in text
    assert 'current_step' not in json.dumps(stored[f'wizard_case_{ready.code}']['step_data'])
    assert 'Ana' not in url(ready, 'maker') and 'invisalign' not in text
    # Nothing of the patient but the code in the wizard's URLs.
    assert client.get(url(ready, 'maker')).status_code == 200


def test_each_patient_has_its_own_wizard(doctor, ready):
    other = patients_service.create_patient(
        doctor, first_name='Eva', actor=patients_service.Actor('doctor', doctor.uid))
    client = browser(doctor)
    go(client, ready, 'product', product='tps')
    assert client.get(url(other, 'maker'))['Location'] == url(other, 'product')
    assert client.get(url(ready, 'maker')).status_code == 200


def test_the_steps_are_in_spanish(doctor, ready):
    client = browser(doctor)
    html = page(client.get(url(ready, 'product', 'es')))
    assert 'Paso 1 de 3' in html and 'Continuar' in html and 'Atrás' in html
    assert 'Treatment Planning' not in html and 'Continue' not in html
    go(client, ready, 'product', language='es', product='tps')
    go(client, ready, 'maker', language='es', kind='brand', maker='invisalign')
    html = page(client.get(url(ready, 'options', 'es')))
    assert 'Añadir al carrito' in html and 'Corto' in html and 'Largo' in html
    assert 'Short' not in html


def test_the_dialog_patient_goes_to_the_first_step(doctor, entity):
    client = browser(doctor)
    client.post('/en/start-a-case/patient/', {'first_name': 'Eva', 'surname': 'Ruiz',
                                              'reference': '', 'comments': ''})
    from patients.models import Patient
    patient = Patient.objects.get()
    assert page(client.get(url(patient, 'product'))).count('data-wizard-form') == 1


def test_a_stale_or_forged_current_step_never_causes_a_500_or_an_item(advanced, ready_vo):
    client = browser(advanced)
    go(client, ready_vo, 'product', product='tps')
    go(client, ready_vo, 'maker', kind='in_house')
    go(client, ready_vo, 'product', product='vo')  # the product is switched in another tab
    stale = go(client, ready_vo, 'options', 'options_tps', size='medium')
    assert stale.status_code == 302 and not CartItem.objects.exists()
    forged = go(client, ready_vo, 'options', 'nothing', size='medium')
    assert forged.status_code == 302 and not CartItem.objects.exists()
