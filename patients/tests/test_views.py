"""The patients page (epic-ordering 9.1): create, list, ownership and access."""
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from patients import service
from patients.models import Patient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]
URL = '/en/patients/'


def browser(user):
    client = Client()
    client.force_login(user)
    return client


def rows(page):
    return re.findall(r'data-patient="([^"]+)"', page)


def test_the_page_lists_no_patients_at_first(doctor):
    page = browser(doctor).get(URL).content.decode()
    assert 'data-patient-form' in page and 'data-no-patients' in page


def test_a_doctor_creates_a_patient_and_sees_it_listed(doctor):
    client = browser(doctor)
    response = client.post(URL, {'first_name': 'Ana', 'surname': 'Pérez', 'reference': '',
                                 'comments': 'c'})
    assert response.status_code == 302 and response['Location'] == URL
    patient = Patient.objects.get()
    assert re.match(r'^EU-[0-9A-Z]{8}$', patient.code) and patient.doctor_id == doctor.pk
    assert patient.actor_uuid == doctor.uid
    page = client.get(URL).content.decode()
    assert rows(page) == [patient.code] and 'Ana Pérez' in page
    assert patient.code in client.get(URL).content.decode()


def test_a_reference_only_patient_shows_the_reference(doctor):
    browser(doctor).post(URL, {'first_name': '', 'surname': '', 'reference': 'P-12'})
    page = browser(doctor).get(URL).content.decode()
    assert 'P-12' in page and len(rows(page)) == 1


def test_nothing_given_shows_a_field_error_and_creates_nothing(doctor):
    response = browser(doctor).post(URL, {'first_name': '', 'surname': '', 'reference': '',
                                          'comments': 'only this'})
    assert response.status_code == 200
    assert 'Enter a first name, a surname or a reference.' in response.content.decode()
    assert not Patient.objects.exists()


def test_the_error_is_in_spanish_too(doctor):
    response = browser(doctor).post('/es/patients/', {'first_name': ''})
    assert 'Introduzca un nombre, unos apellidos o una referencia.' in response.content.decode()


def test_another_doctor_does_not_see_the_patients(doctor, other_doctor, patient):
    assert rows(browser(doctor).get(URL).content.decode()) == [patient.code]
    other = browser(other_doctor).get(URL).content.decode()
    assert rows(other) == [] and patient.code not in other and 'Ana' not in other


def test_the_page_needs_a_login_and_a_doctor(doctor, admin):
    assert Client().get(URL)['Location'].startswith('/en/login/')
    staff = get_user_model().objects.create_user('staff@example.com', 'violet-orbit-canyon-42',
                                                 is_staff=True)
    assert browser(staff).get(URL).status_code == 404


def test_the_patients_link_of_the_nav_leads_here(doctor):
    page = browser(doctor).get('/en/home/').content.decode()
    assert re.search(r'href="/en/patients/" class="[^"]*">patients</a>', page)


def test_the_page_is_not_cached(doctor):
    assert 'no-store' in browser(doctor).get(URL)['Cache-Control']


def test_no_name_is_logged(doctor, caplog):
    import logging
    with caplog.at_level(logging.DEBUG):
        browser(doctor).post(URL, {'first_name': 'Zacarías', 'surname': 'Quiroga'})
        browser(doctor).get(URL)
    assert 'Zacar' not in caplog.text and 'Quiroga' not in caplog.text
