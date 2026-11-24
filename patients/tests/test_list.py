"""The patient list (epic-ordering 9.2): columns, status, all rows for the browser to search and page (9.15), empty state."""
import re

import pytest
from django.test import Client

from patients import service
from patients.models import Patient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls', 'fake_bucket')]
URL = '/en/patients/'


def browser(user):
    client = Client()
    client.force_login(user)
    return client


def html(response):
    return response.content.decode()


def rows(page):
    return re.findall(r'data-patient="([^"]+)"', page)


def make(doctor, count):
    from datetime import timedelta

    from django.utils import timezone
    base = timezone.now()
    made = []
    for index in range(count):
        patient = service.create_patient(doctor, reference=f'R{index}',
                                         actor=service.Actor('doctor', doctor.uid))
        Patient.objects.filter(pk=patient.pk).update(created_at=base + timedelta(minutes=index))
        made.append(patient.code)
    return made


def test_rows_newest_first_with_status_and_placeholder(doctor):
    codes = make(doctor, 3)
    inactive = Patient.objects.get(code=codes[0])
    inactive.status = Patient.INACTIVE
    inactive.save()
    page = html(browser(doctor).get(URL))
    assert rows(page) == list(reversed(codes))
    assert page.count('data-portrait-thumb') == 3 and 'data-portrait-image' not in page
    assert page.count('data-status="active"') == 2 and page.count('data-status="inactive"') == 1
    assert 'Inactive' in page and 'Showing 1 to 3 of 3' in page
    assert f'/en/patients/{codes[0]}/' in page


def test_the_empty_state_offers_adding_a_patient(doctor):
    page = html(browser(doctor).get(URL))
    assert 'data-no-patients' in page
    link = re.search(r'<a [^>]*data-add-patient-empty[^>]*>', page).group(0)
    assert 'href="/en/patients/new/"' in link and 'data-opens-dialog="add-patient"' in link
    assert 'data-pagination' not in page


def test_every_row_is_rendered_for_the_browser_to_page(doctor):
    codes = make(doctor, 30)
    page = html(browser(doctor).get(URL))
    assert rows(page) == list(reversed(codes))
    assert 'data-patient-chooser' in page and 'data-patient-search' in page
    assert 'data-pagination' in page and 'data-no-match' in page
    for query in ('size=7', 'page=2', 'size=25&page=2'):
        assert rows(html(browser(doctor).get(f'{URL}?{query}'))) == list(reversed(codes))


def test_each_row_carries_folded_search_text(doctor):
    service.create_patient(doctor, first_name='Ana', surname='García', reference='Ref ÑU',
                           actor=service.Actor('doctor', doctor.uid))
    patient = Patient.objects.get()
    page = html(browser(doctor).get(URL))
    search = re.search(r'data-search="([^"]*)"', page).group(1)
    assert search == f'{patient.code.lower()} ana garcia ref nu'


def test_an_erased_row_searches_by_code_only(doctor):
    service.create_patient(doctor, first_name='Ana', surname='García',
                           actor=service.Actor('doctor', doctor.uid))
    patient = Patient.objects.get()
    Patient.objects.filter(pk=patient.pk).update(status=Patient.ERASED)
    page = html(browser(doctor).get(URL))
    search = re.search(r'data-search="([^"]*)"', page).group(1)
    assert search == patient.code.lower()
    assert 'garc' not in page.lower().replace('data-search', '')


def test_the_page_is_not_cached(doctor, patient):
    response = browser(doctor).get(URL)
    assert 'no-store' in response['Cache-Control']


def test_the_search_term_is_not_a_server_input(doctor, patient):
    page = html(browser(doctor).get(URL + '?q=Ana'))
    assert rows(page) == [patient.code]


def test_only_the_doctors_own_patients_are_listed(doctor, other_doctor, patient):
    service.create_patient(other_doctor, reference='x',
                           actor=service.Actor('doctor', other_doctor.uid))
    assert rows(html(browser(doctor).get(URL))) == [patient.code]


def test_an_erased_patients_code_is_not_a_link(doctor, patient):
    Patient.objects.filter(pk=patient.pk).update(status=Patient.ERASED)
    page = html(browser(doctor).get(URL))
    assert patient.code in page and f'/en/patients/{patient.code}/' not in page
