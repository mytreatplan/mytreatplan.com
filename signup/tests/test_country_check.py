"""Epic-regions entry 3: step 1's country check, with the Django client. Every row of the
plan's I/O matrix on both region profiles: the live check (signup.views.check_country),
the note rendered with step 1, and the banned refusal (AccountForm.clean_country)."""
import time

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from eventlog.models import Attempt
from regions.profiles import get_profile
from signup.models import PendingRegistration
from signup.views import SEEN_SESSION_KEY

from .helpers import _complete, _step1

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()

EU_SITE = 'https://eu.example.test'
AE_SITE = 'https://ae.example.test'
CHECK_URL = '/{lang}/signup/check-country/'

BANNED_ES = 'No podemos ofrecer MyTreatplan a doctores de este país.'
# As the pages show it (escaped).
BANNED_EN_HTML = 'We can&#x27;t offer MyTreatplan to doctors in this country.'
SA_NOTE = ('Saudi Arabia is served by our ae.example.test site. Sign up there to have your '
           'account in your region.')
SA_NOTE_ES = ('Arabia Saudita se atiende desde nuestro sitio ae.example.test. Regístrese allí '
              'para tener su cuenta en su región.')
ES_NOTE = ('Spain is served by our eu.example.test site. Sign up there to have your account '
           'in your region.')


def _profile(settings, code):
    settings.REGION = code
    settings.REGION_PROFILE = get_profile(code)
    settings.REGION_SITE_URLS = {'eu': EU_SITE, 'ae': AE_SITE}


@pytest.fixture
def eu_site(settings):
    _profile(settings, 'eu')


@pytest.fixture
def ae_site(settings):
    _profile(settings, 'ae')


def _check(client, country, lang='en'):
    response = client.post(CHECK_URL.format(lang=lang), {'country': country})
    assert response.status_code == 200
    return response.content.decode()


# --- The live check --------------------------------------------------------------------

def test_the_country_select_runs_the_live_check(client, eu_site):
    page = client.get('/en/signup/').content.decode()
    assert 'hx-post="/en/signup/check-country/"' in page
    assert 'hx-params="country,csrfmiddlewaretoken"' in page
    assert '<div id="country-check" aria-live="polite">' in page
    # Nothing chosen yet: the note area is empty.
    assert 'data-country-' not in page


def test_own_region_shows_nothing(client, eu_site):
    assert 'data-country-' not in _check(client, 'ES')


def test_own_region_shows_nothing_on_ae(client, ae_site):
    assert 'data-country-' not in _check(client, 'AE')


def test_other_region_links_to_its_signup(client, eu_site):
    html = _check(client, 'SA')
    assert 'data-country-other-region' in html and SA_NOTE in html
    assert f'href="{AE_SITE}/en/signup/"' in html
    assert "Go to ae.example.test's signup" in html
    assert 'target=' not in html  # same tab


def test_other_region_mirror_on_ae(client, ae_site):
    html = _check(client, 'ES')
    assert ES_NOTE in html
    assert f'href="{EU_SITE}/en/signup/"' in html


@pytest.mark.parametrize('country', ['IR', 'NG'])
def test_banned_shows_the_refusal(client, eu_site, country):
    html = _check(client, country)
    assert 'data-country-banned' in html and BANNED_EN_HTML in html
    assert 'sanction' not in html.lower()


def test_banned_on_ae(client, ae_site):
    assert BANNED_EN_HTML in _check(client, 'IR')


@pytest.mark.parametrize('country', ['', 'XX', 'zz9'])
def test_blank_or_unknown_country_shows_nothing(client, ae_site, country):
    # On ae an unknown code would read as eu (the map's default): it shows nothing here.
    assert _check(client, country).strip() == ''


def test_spanish_other_region_note(client, eu_site):
    html = _check(client, 'SA', lang='es')
    assert SA_NOTE_ES in html
    # ae offers only English: the link goes to its English signup.
    assert f'href="{AE_SITE}/en/signup/"' in html
    assert 'Ir al registro de ae.example.test' in html


def test_spanish_refusal(client, eu_site):
    assert BANNED_ES in _check(client, 'IR', lang='es')


def test_live_check_is_post_only(client, eu_site):
    assert client.get(CHECK_URL.format(lang='en')).status_code == 405


def test_live_check_logged_in_answers_nothing(client, eu_site):
    client.force_login(User.objects.create_user('ana@example.com', 'a-password-1234'))
    assert _check(client, 'SA').strip() == ''
    assert _check(client, 'IR').strip() == ''


def test_live_check_after_the_idle_timeout_answers_nothing(client, eu_site):
    assert _step1(client, password2='a-different-password-1').status_code == 200
    session = client.session
    session[SEEN_SESSION_KEY] = time.time() - 31 * 60
    session.save()
    assert _check(client, 'SA').strip() == ''


# --- Step 1 ----------------------------------------------------------------------------

def test_own_region_continues(client, eu_site):
    response = _step1(client, country='ES')
    assert response['Location'] == '/en/signup/legal/'


def test_other_region_continues_and_the_account_has_its_country(client, eu_site):
    response = _complete(client, country='SA')
    assert response['Location'] == '/en/welcome/'
    assert User.objects.get().country == 'SA'


def test_other_region_note_on_a_re_rendered_step1(client, eu_site):
    """Without JavaScript: a form error, or Back to step 1, shows the note with the page."""
    html = _step1(client, country='SA', first_name='').content.decode()
    assert SA_NOTE in html and f'href="{AE_SITE}/en/signup/"' in html

    other = Client()
    assert _step1(other, country='SA')['Location'] == '/en/signup/legal/'
    html = other.get('/en/signup/').content.decode()
    assert SA_NOTE in html


def test_own_region_no_note_on_a_re_rendered_step1(client, eu_site):
    html = _step1(client, country='ES', first_name='').content.decode()
    assert 'data-country-' not in html


@pytest.mark.parametrize('country', ['IR', 'NG'])
def test_banned_is_refused_and_logged(client, eu_site, country):
    response = _step1(client, country=country)
    assert response.status_code == 200
    errors = response.context['form'].errors.as_data()
    assert [error.code for error in errors['country']] == ['country_banned']
    assert not PendingRegistration.objects.exists()
    html = response.content.decode()
    # Once, as the field error: the note area does not repeat it.
    assert html.count(BANNED_EN_HTML) == 1
    assert 'data-country-banned' not in html
    events = list(Attempt.objects.get().events.filter(event='rejected'))
    assert [(e.field, e.reason) for e in events] == [('country', 'country_banned')]


def test_banned_is_refused_on_ae(client, ae_site):
    assert _step1(client, country='IR').status_code == 200
    assert not PendingRegistration.objects.exists()


def test_banned_refusal_in_spanish(client, eu_site):
    response = _step1(client, lang='es', country='IR')
    assert response.status_code == 200
    assert BANNED_ES in response.content.decode()
    assert not PendingRegistration.objects.exists()
