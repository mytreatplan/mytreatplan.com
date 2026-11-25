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
    """The wizard's first step after the patient (9.16); the old path redirects to it."""
    return f'/en/start-a-case/{patient.code}/product/'


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


def test_step_one_without_a_fiscal_entity_shows_the_block_linking_to_add_one(doctor, patient):
    html = page(browser(doctor).get(START))
    assert 'data-fiscal-link' in html
    assert 'href="/en/settings/fiscal/add/?next=%2Fen%2Fstart-a-case%2F"' in html


def test_step_one_leaves_out_inactive_patients(doctor, entity, patient):
    patient.status = 'inactive'
    patient.save()
    assert 'data-no-patients' in page(browser(doctor).get(START))


def test_a_new_patient_goes_straight_to_the_wizard(doctor, entity):
    client = browser(doctor)
    response = client.post(NEW, {'first_name': 'Eva', 'surname': 'Ruiz', 'reference': '',
                                 'comments': ''})
    patient = Patient.objects.get()
    assert response.status_code == 302 and response['Location'] == step2(patient)
    assert 'Eva Ruiz' in page(client.get(step2(patient)))
    assert client.get(f'/en/start-a-case/{patient.code}/')['Location'] == step2(patient)
    assert patient.code in page(client.get('/en/patients/'))


def test_an_invalid_new_patient_shows_the_form_again(doctor, entity):
    response = browser(doctor).post(NEW, {'first_name': '', 'surname': '', 'reference': ''})
    html = page(response)
    assert response.status_code == 200 and 'data-patient-form' in html
    assert 'data-patients' in html or 'data-no-patients' in html  # step 1 again
    assert re.search(r'<dialog[^>]*data-open', html)
    assert not Patient.objects.exists()
    assert 'data-patient-form' in page(browser(doctor).get(NEW))


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


def test_an_unknown_size_shows_as_its_code():
    from cart.views import size_label
    assert size_label('xl') == 'xl' and size_label('ice') == 'ICE'
