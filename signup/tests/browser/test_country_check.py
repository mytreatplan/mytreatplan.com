"""Epic-regions entry 3: step 1's live country check in Chromium against live_server.
Choosing a country of the other region shows the note with that region's signup link;
choosing a banned one shows the refusal, and Continue keeps the person on step 1. The
rows of the plan's matrix are the Django client tests' (signup/tests/test_country_check.py)."""
import re

import pytest

pytest.importorskip('pytest_playwright')

from playwright.sync_api import expect  # noqa: E402

from regions.countries import seed_state  # noqa: E402
from regions.models import Country  # noqa: E402
from regions.profiles import get_profile  # noqa: E402
from signup.models import PendingRegistration  # noqa: E402

from .helpers import fill_step1, press, url  # noqa: E402

pytestmark = [
    pytest.mark.browser,
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
    pytest.mark.usefixtures('portal_urls'),
]

AE_SITE = 'https://ae.example.test'
CHECK_COUNTRY = re.compile(r'/signup/check-country/$')
BANNED = "We can't offer MyTreatplan to doctors in this country."


@pytest.fixture(autouse=True)
def country_map():
    """The countries this test chooses, as the seed has them. The migration's map may be
    gone: an earlier transactional test flushes the tables, and serialized_rollback
    restores nothing when the test database was not serialized (as conftest.py's
    legal_documents)."""
    for code in ('ES', 'SA', 'IR'):
        Country.objects.get_or_create(code=code, defaults={'state': seed_state(code)})


def _choose(page, country):
    """Choose a country and wait for the live check (htmx) to answer."""
    with page.expect_response(CHECK_COUNTRY):
        page.select_option('#id_country', country)


def test_live_country_check(page, live_server, settings):
    settings.REGION = 'eu'
    settings.REGION_PROFILE = get_profile('eu')
    settings.REGION_SITE_URLS = {**settings.REGION_SITE_URLS, 'ae': AE_SITE}

    page.goto(f'{live_server.url}/en/signup/')
    fill_step1(page)  # chooses ES: let its live check answer first
    page.wait_for_load_state('networkidle')
    note = page.locator('#country-check')

    _choose(page, 'SA')
    expect(note).to_contain_text('Saudi Arabia is served by our ae.example.test site.')
    link = note.get_by_role('link', name="Go to ae.example.test's signup")
    expect(link).to_have_attribute('href', f'{AE_SITE}/en/signup/')

    _choose(page, 'IR')
    expect(note).to_have_text(BANNED)

    press(page, 'Continue')
    expect(page).to_have_url(url('en'))
    expect(page.locator('body')).to_contain_text(BANNED)
    assert not PendingRegistration.objects.exists()
