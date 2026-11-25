"""The patient chooser (epic-ordering 9.15) in Chromium against live_server: search by code
and by name (partial, case- and accent-insensitive), no match, paging a search, clearing, no
JavaScript, and the wizard's patient step; the address bar never changes while searching."""
import re

import pytest

pytest.importorskip('pytest_playwright')

from playwright.sync_api import expect  # noqa: E402

from accounts.models import User  # noqa: E402
from accounts.tests.browser.helpers import log_in, log_out, sign_up  # noqa: E402
from patients import service  # noqa: E402
from patients.models import Patient  # noqa: E402

pytestmark = [
    pytest.mark.browser,
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
    pytest.mark.usefixtures('portal_urls'),
]

ROWS = 'tbody tr[data-patient]'


@pytest.fixture
def signed_in(live_server, page):
    sign_up(page, live_server)
    log_out(page, live_server)
    log_in(page, live_server)
    return User.objects.get()


def _patients(doctor, count, surname='Lopez'):
    actor = service.Actor('doctor', doctor.uid)
    return [service.create_patient(doctor, first_name=f'P{n}', surname=surname, actor=actor)
            for n in range(count)]


def _visible(page):
    return page.locator(f'{ROWS}:not([hidden])')


def test_search_by_code_and_name_on_the_patient_list(live_server, page, signed_in):
    _patients(signed_in, 3)
    garcia = service.create_patient(signed_in, first_name='Ana', surname='García',
                                    actor=service.Actor('doctor', signed_in.uid))
    page.goto(f'{live_server.url}/en/patients/')
    url = page.url
    search = page.locator('[data-patient-search]')
    expect(search).to_be_visible()
    expect(_visible(page)).to_have_count(4)

    search.fill('GARC')  # partial, case and accent insensitive
    expect(_visible(page)).to_have_count(1)
    expect(page.locator(f'[data-patient="{garcia.code}"]')).to_be_visible()

    search.fill(garcia.code.upper())  # a code
    expect(_visible(page)).to_have_count(1)
    search.fill(garcia.code[3:7].lower())  # part of a code
    expect(_visible(page)).to_have_count(1)

    search.fill('zzz')
    expect(_visible(page)).to_have_count(0)
    expect(page.get_by_text('No patients match your search.')).to_be_visible()
    expect(page.locator('[data-pagination]')).to_be_hidden()

    search.fill('')  # cleared: the full list again
    expect(_visible(page)).to_have_count(4)
    assert page.url == url


def test_a_compatibility_character_matches_like_the_server_folds_it(live_server, page,
                                                                  signed_in):
    service.create_patient(signed_in, reference='Nº 12',
                           actor=service.Actor('doctor', signed_in.uid))
    page.goto(f'{live_server.url}/en/patients/')
    page.locator('[data-patient-search]').fill('nº 12')
    expect(_visible(page)).to_have_count(1)


def test_paging_a_search_leaves_the_url_alone(live_server, page, signed_in):
    _patients(signed_in, 30)
    _patients(signed_in, 5, surname='Other')
    page.goto(f'{live_server.url}/en/patients/')
    url = page.url
    expect(_visible(page)).to_have_count(10)
    expect(page.locator('[data-showing]')).to_have_text('Showing 1 to 10 of 35')
    page.locator('[data-patient-search]').fill('lopez')
    expect(page.locator('[data-showing]')).to_have_text('Showing 1 to 10 of 30')
    page.locator('[data-page-link="2"]').click()
    expect(page.locator('[data-showing]')).to_have_text('Showing 11 to 20 of 30')
    expect(page.locator('[data-page-links] button:focus')).to_have_count(1)
    page.locator('[data-next]').click()
    expect(page.locator('[data-showing]')).to_have_text('Showing 21 to 30 of 30')
    expect(page.locator('[data-page-links] button:focus')).to_have_count(1)
    page.locator('[data-page-link="1"]').click()
    expect(_visible(page)).to_have_count(10)
    page.locator('[data-page-size]').select_option('25')
    expect(page.locator('[data-showing]')).to_have_text('Showing 1 to 25 of 30')
    assert page.url == url and 'lopez' not in page.url.lower()


def test_without_javascript_every_row_shows_and_no_search_box(live_server, page, signed_in):
    _patients(signed_in, 30)
    state = page.context.storage_state()
    context = page.context.browser.new_context(storage_state=state, java_script_enabled=False)
    try:
        plain = context.new_page()
        plain.goto(f'{live_server.url}/en/patients/')
        expect(plain.locator(ROWS)).to_have_count(30)
        expect(plain.locator('[data-patient-search]')).to_be_hidden()
        expect(plain.locator('[data-pagination]')).to_be_hidden()
    finally:
        context.close()


def test_the_wizards_patient_step_searches_and_chooses(live_server, page, signed_in):
    patients = _patients(signed_in, 30)
    ana = service.create_patient(signed_in, first_name='Ana', surname='Pérez',
                                 actor=service.Actor('doctor', signed_in.uid))
    page.goto(f'{live_server.url}/en/start-a-case/')
    url = page.url
    expect(_visible(page)).to_have_count(10)
    expect(page.locator('[data-page-link="2"]')).to_be_visible()
    page.locator('[data-patient-search]').fill('perez')
    expect(_visible(page)).to_have_count(1)
    assert page.url == url
    _visible(page).locator('[data-choose]').click()
    expect(page).to_have_url(re.compile(rf'/en/start-a-case/{ana.code}/product/$'))
    assert Patient.objects.count() == len(patients) + 1
