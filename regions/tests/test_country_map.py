"""Epic-regions entry 1: the country map (regions.models.Country), its seed and the
lookup (regions.countries.lookup_country)."""
import phonenumbers
import pytest
from django.urls import reverse
from django.utils import translation
from django_countries.data import COUNTRIES

from regions import countries
from regions.countries import (
    AE_COUNTRIES, BANNED_COUNTRIES, SIGNUP_PATH, lookup_country, seed_state,
)
from regions.models import BANNED, Country
from regions.profiles import get_profile

pytestmark = pytest.mark.django_db

# The decision lists (user, 2026-10-30 and 2026-11-01), written out once more.
DECIDED_AE = {
    'AE', 'SA', 'KW', 'QA', 'BH', 'OM', 'JO', 'LB', 'IQ', 'PS', 'YE', 'TR', 'EG', 'MA', 'ZA',
}
# The 51 sovereign African states other than EG, MA and ZA.
DECIDED_BANNED_AFRICAN_STATES = {
    'DZ', 'AO', 'BJ', 'BW', 'BF', 'BI', 'CV', 'CM', 'CF', 'TD', 'KM', 'CG', 'CD', 'CI',
    'DJ', 'GQ', 'ER', 'SZ', 'ET', 'GA', 'GM', 'GH', 'GN', 'GW', 'KE', 'LS', 'LR', 'LY',
    'MG', 'MW', 'ML', 'MR', 'MU', 'MZ', 'NA', 'NE', 'NG', 'RW', 'ST', 'SN', 'SC', 'SL',
    'SO', 'SS', 'SD', 'TZ', 'TG', 'TN', 'UG', 'ZM', 'ZW',
}
DECIDED_BANNED = DECIDED_BANNED_AFRICAN_STATES | {
    'EH', 'RE', 'YT', 'SH', 'AC', 'TA', 'IO',
    'IR', 'SY', 'KP', 'CU', 'RU', 'BY',
}

EU_SITE = 'https://eu.example.test'
AE_SITE = 'https://ae.example.test'


@pytest.fixture
def eu_site(settings):
    settings.REGION = 'eu'
    settings.REGION_PROFILE = get_profile('eu')
    settings.REGION_SITE_URLS = {'eu': EU_SITE, 'ae': AE_SITE}


@pytest.fixture
def ae_site(settings):
    settings.REGION = 'ae'
    settings.REGION_PROFILE = get_profile('ae')
    settings.REGION_SITE_URLS = {'eu': EU_SITE, 'ae': AE_SITE}


# --- The lists and the seed -----------------------------------------------------------

def test_the_lists_are_the_decision_lists():
    assert len(DECIDED_BANNED_AFRICAN_STATES) == 51
    assert AE_COUNTRIES == DECIDED_AE
    assert BANNED_COUNTRIES == DECIDED_BANNED
    assert not AE_COUNTRIES & BANNED_COUNTRIES


def test_the_lists_are_iso_codes():
    # AC and TA (Saint Helena) exist only in phonenumbers; they are banned with SH.
    assert AE_COUNTRIES | BANNED_COUNTRIES <= set(COUNTRIES) | {'AC', 'TA'}


def test_the_migrated_map_matches_the_lists():
    """A seed (or later data migration) and the lists in regions/countries.py diverging
    fails here. The seed freezes django-countries 9.1.0's codes: if a package upgrade
    adds or drops a code, this fails until a new data migration follows it."""
    rows = dict(Country.objects.values_list('code', 'state'))

    # AC and TA are not ISO codes (only phonenumbers has them) but are seeded as banned.
    assert set(rows) == set(COUNTRIES) | {'AC', 'TA'}
    assert {code for code, state in rows.items() if state == 'ae'} == AE_COUNTRIES
    assert {code for code, state in rows.items() if state == BANNED} == BANNED_COUNTRIES
    assert {code for code, state in rows.items() if state == 'eu'} == (
        set(COUNTRIES) | {'AC', 'TA'}) - AE_COUNTRIES - BANNED_COUNTRIES
    assert rows == {code: seed_state(code) for code in rows}


@pytest.mark.parametrize('code, state', [
    ('AE', 'ae'), ('SA', 'ae'), ('TR', 'ae'), ('EG', 'ae'), ('MA', 'ae'), ('ZA', 'ae'),
    ('IR', BANNED), ('NG', BANNED),
    ('ES', 'eu'), ('US', 'eu'),
])
def test_the_map_reads(code, state):
    assert Country.objects.get(code=code).state == state


# --- The lookup -----------------------------------------------------------------------

def test_own_region(eu_site):
    answer = lookup_country('ES')

    assert answer.state == 'eu'
    assert answer.own_region and not answer.other_region and not answer.banned
    assert answer.site_url == '' and answer.signup_url == ''


def test_other_region(eu_site):
    answer = lookup_country('AE', language='en')

    assert answer.state == 'ae'
    assert answer.other_region and not answer.own_region and not answer.banned
    assert answer.site_url == AE_SITE
    assert answer.signup_url == f'{AE_SITE}/en/signup/'


@pytest.mark.parametrize('code', ['IR', 'NG'])
def test_banned(eu_site, code):
    answer = lookup_country(code)

    assert answer.state == BANNED
    assert answer.banned and not answer.own_region and not answer.other_region
    assert answer.site_url == '' and answer.signup_url == ''


def test_lowercase_reads_as_uppercase(eu_site):
    assert lookup_country('ae', language='en') == lookup_country('AE', language='en')
    assert lookup_country(' ae ', language='en').state == 'ae'


@pytest.mark.parametrize('code', ['', None, '   '])
def test_blank_reads_as_eu(eu_site, code):
    answer = lookup_country(code)

    assert answer.state == 'eu' and answer.own_region
    assert answer.signup_url == ''


def test_missing_reads_as_eu(eu_site):
    assert not Country.objects.filter(code='XK').exists()

    assert lookup_country('XK').state == 'eu'


def test_signup_url_in_a_language_the_region_offers(eu_site):
    """The ae site offers only English: a Spanish visitor of the eu site goes to /en/."""
    assert lookup_country('AE', language='es').signup_url == f'{AE_SITE}/en/signup/'
    with translation.override('es'):
        assert lookup_country('AE').signup_url == f'{AE_SITE}/en/signup/'


def test_from_the_ae_site(ae_site):
    assert lookup_country('AE').own_region
    assert lookup_country('AE').signup_url == ''
    assert lookup_country('NG').banned

    spain = lookup_country('ES', language='es')
    assert spain.state == 'eu' and spain.other_region
    assert spain.site_url == EU_SITE
    assert spain.signup_url == f'{EU_SITE}/es/signup/'
    assert lookup_country('ES', language='en').signup_url == f'{EU_SITE}/en/signup/'
    # A missing or blank country reads as eu: the other region here.
    assert lookup_country('XK', language='en').signup_url == f'{EU_SITE}/en/signup/'
    assert lookup_country(None, language='en').other_region


def test_production_site_url_by_default(settings):
    settings.REGION_PROFILE = get_profile('eu')
    settings.REGION_SITE_URLS = {}

    assert lookup_country('AE', language='en').signup_url == 'https://mytreatplan.ae/en/signup/'


def test_signup_path_is_the_signup_route(portal_urls):
    for language in ('en', 'es'):
        with translation.override(language):
            assert reverse('signup') == SIGNUP_PATH.format(language=language)


def test_every_code_signup_or_phonenumbers_can_produce_gets_an_answer(eu_site):
    codes = set(COUNTRIES) | set(phonenumbers.SUPPORTED_REGIONS)
    states = {'eu', 'ae', BANNED}

    for code in sorted(codes):
        answer = lookup_country(code, language='en')
        assert answer.state in states, code
        assert answer.code == code


def test_the_module_reads_regions_from_the_profiles():
    """The default state is the default region (regions/profiles.py)."""
    assert countries.DEFAULT_STATE == 'eu'
