"""The patient list (epic-ordering 9.2): columns, status, pagination, empty state."""
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


def test_pages_of_ten_by_default(doctor):
    codes = make(doctor, 30)
    client = browser(doctor)
    page = html(client.get(URL))
    assert rows(page) == list(reversed(codes))[:10]
    assert 'Showing 1 to 10 of 30' in page
    assert set(re.findall(r'data-page-link="(\d+)"', page)) == {'1', '2', '3'}
    second = html(client.get(URL + '?page=2'))
    assert rows(second) == list(reversed(codes))[10:20] and 'Showing 11 to 20 of 30' in second


def test_a_size_of_25_gives_two_pages(doctor):
    make(doctor, 30)
    page = html(browser(doctor).get(URL + '?size=25'))
    assert len(rows(page)) == 25 and 'Showing 1 to 25 of 30' in page
    assert set(re.findall(r'data-page-link="(\d+)"', page)) == {'1', '2'}
    assert '<option value="25" selected>' in page


@pytest.mark.parametrize('query, count, shown', [
    ('size=7', 10, 'Showing 1 to 10 of 30'),
    ('size=abc', 10, 'Showing 1 to 10 of 30'),
    ('page=abc', 10, 'Showing 1 to 10 of 30'),
    ('page=99', 10, 'Showing 21 to 30 of 30'),
    ('page=-3', 10, 'Showing 1 to 10 of 30'),
    ('page=0', 10, 'Showing 1 to 10 of 30'),
    ('size=100&page=2', 30, 'Showing 1 to 30 of 30'),
])
def test_invalid_page_or_size_falls_back(doctor, query, count, shown):
    make(doctor, 30)
    page = html(browser(doctor).get(f'{URL}?{query}'))
    assert len(rows(page)) == count and shown in page


def test_only_the_doctors_own_patients_are_listed(doctor, other_doctor, patient):
    service.create_patient(other_doctor, reference='x',
                           actor=service.Actor('doctor', other_doctor.uid))
    assert rows(html(browser(doctor).get(URL))) == [patient.code]


def test_an_erased_patients_code_is_not_a_link(doctor, patient):
    Patient.objects.filter(pk=patient.pk).update(status=Patient.ERASED)
    page = html(browser(doctor).get(URL))
    assert patient.code in page and f'/en/patients/{patient.code}/' not in page
