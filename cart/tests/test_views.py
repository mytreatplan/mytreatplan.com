"""The case wizard and the cart page (epic-ordering 9.3): access, the two steps with their
plain-POST fallback, the maker-login detour and the cart."""
import re
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from accounts import makers
from cart.models import CartItem
from patients import service as patients_service
from patients.models import Patient
from patients.tests.conftest import BRANDED, ICE

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]
START, NEW, CART = '/en/start-a-case/', '/en/start-a-case/patient/', '/en/cart/'
CREDENTIALS = {'username': 'dr-user', 'password': 'dr-password'}


def browser(user):
    client = Client()
    client.force_login(user)
    return client


def page(response):
    return response.content.decode()


def step2(patient):
    return f'/en/start-a-case/{patient.code}/'


@pytest.fixture
def ready(doctor, entity, patient):
    makers.save(doctor, 'invisalign', CREDENTIALS)
    return patient


def test_step_one_lists_active_patients_and_has_the_dialog(doctor, entity, patient):
    html = page(browser(doctor).get(START))
    assert f'data-patient="{patient.code}"' in html and 'data-patient-form' in html
    assert 'data-fiscal-block' not in html
    assert f'href="{step2(patient)}"' in html


def test_step_one_renders_the_chooser_with_active_patients_only(doctor, entity, patient):
    inactive = patients_service.create_patient(
        doctor, first_name='Eva', surname='Ruiz',
        actor=patients_service.Actor('doctor', doctor.uid))
    inactive.status = 'inactive'
    inactive.save()
    html = page(browser(doctor).get(START))
    assert 'data-patient-chooser' in html and 'data-patient-search' in html
    assert re.findall(r'data-patient="([^"]+)"', html) == [patient.code]
    assert inactive.code not in html
    assert f'data-search="{patient.code.lower()} ana perez"' in html


def test_step_one_leaves_out_inactive_patients(doctor, entity, patient):
    patient.status = 'inactive'
    patient.save()
    assert 'data-no-patients' in page(browser(doctor).get(START))


def test_without_a_fiscal_entity_a_block_links_to_add_one(doctor, patient):
    html = page(browser(doctor).get(START))
    assert re.search(r'data-fiscal-link', html)
    assert 'href="/en/settings/fiscal/add/?next=%2Fen%2Fstart-a-case%2F"' in html
    page2 = page(browser(doctor).get(step2(patient)))
    assert 'next=%2Fen%2Fstart-a-case%2F' + patient.code in page2 and 'data-case-form' not in page2


def test_a_new_patient_goes_straight_to_step_two(doctor, entity):
    client = browser(doctor)
    response = client.post(NEW, {'first_name': 'Eva', 'surname': 'Ruiz', 'reference': '',
                                 'comments': ''})
    patient = Patient.objects.get()
    assert response.status_code == 302 and response['Location'] == step2(patient)
    assert 'Eva Ruiz' in page(client.get(step2(patient)))
    assert patient.code in page(client.get('/en/patients/'))


def test_an_invalid_new_patient_shows_the_form_again(doctor, entity):
    response = browser(doctor).post(NEW, {'first_name': '', 'surname': '', 'reference': ''})
    html = page(response)
    assert response.status_code == 200 and 'data-patient-form' in html
    assert 'data-patients' in html or 'data-no-patients' in html  # step 1 again
    assert re.search(r'<dialog[^>]*data-open', html)
    assert not Patient.objects.exists()
    assert 'data-patient-form' in page(browser(doctor).get(NEW))


def test_step_two_without_javascript_adds_the_item_and_the_cart_shows_it(doctor, ready):
    client = browser(doctor)
    html = page(client.get(step2(ready)))
    for code in ('JUNIOR-1', 'JUNIOR-2', 'BYTE-SYNC'):
        assert f'data-add-on="{code}"' in html
    assert 'data-missing-login="smartee"' in html
    assert 'data-missing-login="invisalign"' not in html
    response = client.post(step2(ready), {'kind': 'maker', 'maker': 'invisalign',
                                          'size': 'medium', 'add_on': ['JUNIOR-2']})
    assert response.status_code == 302 and response['Location'] == '/en/cart/'
    cart = page(client.get(CART))
    assert f'data-cart-item="{ready.code}"' in cart and 'Invisalign' in cart
    assert 'Ana Pérez' in cart and 'Junior II' in cart and 'TPS branded medium' in cart
    assert CartItem.objects.get().add_on_codes == ['JUNIOR-2']


def test_the_missing_login_link_returns_to_step_two(doctor, entity, patient):
    html = page(browser(doctor).get(step2(patient)))
    from urllib.parse import quote
    link = f'/en/settings/maker/smartee/?next={quote(step2(patient), safe="")}'
    assert f'href="{link}"' in html
    client = browser(doctor)
    saved = client.post(link, {'username': 'u', 'password': 'p', 'next': step2(patient)})
    assert saved.status_code == 302 and saved['Location'] == step2(patient)


def test_a_missing_login_is_refused_with_a_message(doctor, entity, patient):
    response = browser(doctor).post(step2(patient), {'kind': 'maker', 'maker': 'smartee',
                                                     'size': 'medium'})
    assert response.status_code == 200 and 'data-error' in page(response)
    assert not CartItem.objects.exists()


def test_other_with_a_name_and_not_decided_are_added(doctor, entity, patient):
    client = browser(doctor)
    assert client.post(step2(patient), {'kind': 'maker', 'maker': 'other',
                                        'other_name': 'Acme', 'size': 'short'}).status_code == 302
    assert 'Acme' in page(client.get(CART))
    assert page(client.get(step2(patient))).count('data-case-form') == 1  # still reachable
    empty = browser(doctor).post(step2(patient), {'kind': 'maker', 'maker': 'other',
                                                  'size': 'short'})
    assert 'data-error' in page(empty)


def test_in_house_and_a_tampered_post(doctor, ready):
    client = browser(doctor)
    tampered = client.post(step2(ready), {'kind': 'maker', 'maker': 'invisalign',
                                          'size': 'short', 'add_on': ['JUNIOR-1']})
    assert 'data-error' in page(tampered) and not CartItem.objects.exists()
    assert client.post(step2(ready), {'kind': 'in_house', 'size': 'long',
                                      'add_on': ['ONE-ARCH']}).status_code == 302
    assert 'In-house printing' in page(client.get(CART))


def test_a_live_work_order_refuses_in_the_page(doctor, ready):
    from patients.models import WorkOrder
    WorkOrder.objects.create(patient=ready, order_line_id=uuid.uuid4(), stage='assigned',
                             product_id=BRANDED['medium'], actor_type='system')
    response = browser(doctor).post(step2(ready), {'kind': 'in_house', 'size': 'long'})
    assert 'data-error' in page(response) and not CartItem.objects.exists()


def test_other_doctors_and_inactive_patients_are_404(doctor, other_doctor, entity, ready):
    assert browser(other_doctor).get(step2(ready)).status_code == 404
    assert browser(other_doctor).post(step2(ready), {'size': 'short'}).status_code == 404
    ready.status = 'inactive'
    ready.save()
    assert browser(doctor).get(step2(ready)).status_code == 404
    assert browser(doctor).get('/en/start-a-case/EU-NOSUCH00/').status_code == 404


def test_staff_gets_404_everywhere(doctor, ready):
    staff = get_user_model().objects.create_user('staff@example.com', 'violet-orbit-canyon-42',
                                                 is_staff=True)
    client = browser(staff)
    for url in (START, NEW, step2(ready), CART):
        assert client.get(url).status_code == 404, url
    assert client.post(NEW, {'first_name': 'Eva'}).status_code == 404


def test_remove_is_a_post_of_the_doctors_own_item(doctor, other_doctor, ready):
    from cart import service
    item = service.add_tps(doctor, ready, maker='undecided', size='medium')
    url = f'/en/cart/{item.pk}/remove/'
    assert browser(doctor).get(url).status_code == 405
    assert browser(other_doctor).post(url).status_code == 404 and CartItem.objects.count() == 1
    assert browser(doctor).post(url).status_code == 302
    assert 'data-cart-empty' in page(browser(doctor).get(CART))


def test_the_home_cart_link_reaches_the_cart_and_anonymous_is_sent_to_login(doctor):
    assert 'href="/en/cart/"' in page(browser(doctor).get('/en/home/'))
    assert Client().get(CART).status_code == 302


def test_included_cases_show_when_the_subscription_has_them(doctor, entity, patient, monkeypatch):
    from catalogue import resolver
    monkeypatch.setattr(resolver, 'included_cases', lambda subscription, category: 2)
    html = page(browser(doctor).get(step2(patient)))
    assert 'includes 2 branded TPS cases per year' in html
    assert 'remaining' not in html


def test_an_unknown_size_shows_as_its_code():
    from cart.views import size_label
    assert size_label('xl') == 'xl' and size_label('ice') == 'ICE'


def test_step_two_is_in_spanish(doctor, entity, patient):
    html = page(browser(doctor).get(f'/es/start-a-case/{patient.code}/'))
    assert 'elija el servicio' in html and 'Añadir al carrito' in html
    assert 'Corto' in html and 'Medio' in html and 'Largo' in html
    assert 'Short' not in html


def test_ice_is_the_first_size_with_its_price_and_goes_to_the_cart(doctor, ready):
    client = browser(doctor)
    html = page(client.get(step2(ready)))
    assert html.index('data-size-row="ice"') < html.index('data-size-row="short"')
    row = html[html.index('data-size-row="ice"'):html.index('</label>', html.index('data-size-row="ice"'))]
    assert re.search(r'data-price-ice>[^<]*50', row)
    assert client.post(step2(ready), {'kind': 'in_house', 'size': 'ice'}).status_code == 302
    item = CartItem.objects.get()
    assert str(item.product_id) == ICE and item.maker == ''
    cart = page(client.get(CART))
    assert 'Initial case evaluation' in cart and '(ICE)' in cart


def test_ice_with_a_maker_and_in_spanish(doctor, ready):
    client = browser(doctor)
    assert client.post(step2(ready), {'kind': 'maker', 'maker': 'invisalign',
                                      'size': 'ice'}).status_code == 302
    assert CartItem.objects.get().maker == 'invisalign'
    assert '(ICE)' in page(client.get('/es/cart/'))


def test_a_tampered_ice_post_is_refused_with_a_live_work_order(doctor, ready):
    from patients.models import WorkOrder
    WorkOrder.objects.create(patient=ready, order_line_id=uuid.uuid4(), stage='assigned',
                             product_id=ICE, actor_type='system')
    response = browser(doctor).post(step2(ready), {'kind': 'in_house', 'size': 'ice'})
    assert 'data-error' in page(response) and not CartItem.objects.exists()


def test_ice_is_not_listed_when_not_offered(doctor, ready, admin):
    from integration.tests.snapshot_rows import pull
    from patients.tests.conftest import ice_catalogue
    section = ice_catalogue()
    section['products'] = [row for row in section['products'] if row['id'] != ICE]
    for row in section['subscriptions'] + section.get('manual_subscriptions', []):
        row['products'] = [pid for pid in row['products'] if pid != ICE]
    assert pull(admin, 2, section) == ('applied', 2)
    html = page(browser(doctor).get(step2(ready)))
    assert 'data-size-row="short"' in html and 'data-size-row="ice"' not in html
