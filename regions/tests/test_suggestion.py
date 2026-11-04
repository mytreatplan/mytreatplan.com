"""Epic-regions entry 5: the region suggestion bar (regions.suggestion, the
`{% region_suggestion_bar %}` tag) and its "Stay here" view, on both region profiles, with
the staging override header giving the country; and the DB-IP attribution in the footer."""
import logging
import re

import pytest
from django.urls import reverse
from django.utils import translation

from regions import geoip, suggestion
from regions.geoip import OVERRIDE_META_KEY
from regions.profiles import get_profile
from regions.suggestion import STAY_COOKIE, is_bot

pytestmark = pytest.mark.django_db

EU_SITE = 'https://eu.example.test'
AE_SITE = 'https://ae.example.test'
BROWSER_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36')
BAR = 'data-region-bar'

# Each profile, a country of the other region, and the other region's site.
PROFILES = {
    'eu': ('SA', AE_SITE),
    'ae': ('ES', EU_SITE),
}


@pytest.fixture(autouse=True)
def geo(settings, tmp_path):
    """The override header on, no geo-IP database, test site URLs, the eu profile. The
    thread's language is restored afterwards: a request to /es/ leaves Spanish active,
    which later tests that translate lazily would see."""
    language = translation.get_language()
    settings.GEOIP_OVERRIDE_HEADER_ENABLED = True
    settings.GEOIP_DB_PATH = str(tmp_path / 'missing' / 'dbip-country-lite.mmdb')
    settings.REGION_SITE_URLS = {'eu': EU_SITE, 'ae': AE_SITE}
    use_profile(settings, 'eu')
    geoip.clear_cache()
    yield
    geoip.clear_cache()
    translation.activate(language)


def use_profile(settings, code):
    settings.REGION = code
    settings.REGION_PROFILE = get_profile(code)


def get(client, url='/en/', country=None, user_agent=BROWSER_UA, **extra):
    if country:
        extra[OVERRIDE_META_KEY] = country
    if user_agent is not None:
        extra['HTTP_USER_AGENT'] = user_agent
    response = client.get(url, **extra)
    assert response.status_code == 200
    return response.content.decode()


def bar_of(html):
    match = re.search(r'<div [^>]*data-region-bar.*?data-region-close.*?</a>\s*</div>\s*</div>',
                      html, re.S)
    return match.group(0) if match else None


# --- When the bar shows ---------------------------------------------------------------

@pytest.mark.parametrize('profile', PROFILES)
def test_other_region_shows_the_bar(client, settings, profile):
    country, site = PROFILES[profile]
    use_profile(settings, profile)
    html = get(client, '/en/', country=country)

    bar = bar_of(html)
    assert bar is not None
    host = site.removeprefix('https://')
    name = {'SA': 'Saudi Arabia', 'ES': 'Spain'}[country]
    assert (f"It looks like you're in {name}. MyTreatPlan for your region is at "
            f"{host}.") in bar
    assert f'href="{site}/"' in bar and f'Go to {host}' in bar
    assert 'target=' not in bar  # same tab
    stay = reverse('region_stay') + '?next=/en/'
    assert bar.count(f'href="{stay}"') == 2  # "Stay here" and the close link
    assert 'Stay here' in bar
    # The first child of <body>, before the page's header.
    assert re.search(r'<body>\s*<div [^>]*data-region-bar', html)


def test_the_bar_shows_on_the_legal_pages(client):
    for url in ('/en/privacy/', '/es/terms/'):
        assert bar_of(get(client, url, country='SA')), url


def test_the_stay_link_keeps_the_query(client):
    bar = bar_of(get(client, '/en/privacy/?a=1&b=2', country='SA'))
    assert 'href="/region/stay/?next=/en/privacy/%3Fa%3D1%26b%3D2"' in bar


def test_spanish_bar_names_the_country_in_spanish(client):
    """Acceptance: ES interface, eu profile, SA. The name is django-countries' Spanish one
    ("Arabia Saudita"; the plan's acceptance line says "Arabia Saudí")."""
    bar = bar_of(get(client, '/es/', country='SA'))
    assert ('Parece que está en Arabia Saudita. MyTreatPlan para su región está en '
            'ae.example.test.') in bar
    assert 'Ir a ae.example.test' in bar and 'Quedarme aquí' in bar


# --- When it does not -----------------------------------------------------------------

@pytest.mark.parametrize('profile, country', [('eu', 'ES'), ('eu', 'IE'), ('ae', 'SA'),
                                              ('ae', 'AE')])
def test_own_region_gets_no_bar(client, settings, profile, country):
    use_profile(settings, profile)
    assert BAR not in get(client, '/en/', country=country)


@pytest.mark.parametrize('profile', PROFILES)
@pytest.mark.parametrize('country', ['IR', 'NG', 'RU'])
def test_banned_country_gets_no_bar(client, settings, profile, country):
    use_profile(settings, profile)
    assert BAR not in get(client, '/en/', country=country)


@pytest.mark.parametrize('ip', ['10.0.0.7', '127.0.0.1', '8.8.8.8'])
def test_unknown_country_gets_no_bar(client, ip):
    """A private address, or a public one with no database file: request_country is None."""
    assert BAR not in get(client, '/en/', REMOTE_ADDR=ip)


@pytest.mark.parametrize('user_agent', [
    '', '   ', None,
    'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
    'Mozilla/5.0 (compatible; bingbot/2.0)',
    'Mozilla/5.0 (compatible; Yahoo! Slurp)',
    'facebookexternalhit/1.1',
    'Mozilla/5.0 (Macintosh) AppleWebKit/537.36 HeadlessChrome/130.0.0.0 Safari/537.36',
    'python-requests/2.32', 'curl/8.4.0', 'Wget/1.21', 'SomeCrawler/1.0', 'Spider',
    'Slackbot-LinkExpanding 1.0', 'WhatsApp link Preview',
])
def test_bots_get_no_bar(client, user_agent):
    assert BAR not in get(client, '/en/', country='SA', user_agent=user_agent)


def test_the_bot_pattern():
    class Request:
        def __init__(self, user_agent):
            self.META = {'HTTP_USER_AGENT': user_agent}
    assert is_bot(Request('')) and is_bot(Request('GOOGLEBOT'))
    assert not is_bot(Request(BROWSER_UA))
    assert not is_bot(Request('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                              'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 '
                              'Mobile/15E148 Safari/604.1'))


def test_a_stayed_visitor_gets_no_bar(client):
    client.cookies[STAY_COOKIE] = '1'
    assert BAR not in get(client, '/en/', country='SA')


def test_portal_pages_get_no_bar(client, portal_urls):
    for name in ('login', 'signup'):
        url = reverse(name)
        html = get(client, url, country='SA')
        assert BAR not in html, url
        assert 'href="https://db-ip.com"' in html, url  # the footer is shared


def test_override_off_gets_no_bar(client, settings):
    settings.GEOIP_OVERRIDE_HEADER_ENABLED = False
    assert BAR not in get(client, '/en/', country='SA')


# --- The order of the checks ----------------------------------------------------------

def fail(*args, **kwargs):
    raise AssertionError('should not be called')


def test_cookie_and_bot_checks_come_before_the_country(client, monkeypatch):
    monkeypatch.setattr(suggestion, 'request_country', fail)
    monkeypatch.setattr(suggestion, 'lookup_country', fail)
    client.cookies[STAY_COOKIE] = '1'
    assert BAR not in get(client, '/en/', country='SA')
    client.cookies.clear()
    assert BAR not in get(client, '/en/', country='SA', user_agent='Googlebot')


def test_no_map_lookup_without_a_country(client, monkeypatch):
    monkeypatch.setattr(suggestion, 'lookup_country', fail)
    assert BAR not in get(client, '/en/')


def test_nothing_is_logged(client, caplog):
    caplog.set_level(logging.DEBUG)
    get(client, '/en/', country='SA', REMOTE_ADDR='203.0.113.9')
    for record in caplog.records:
        message = record.getMessage()
        assert 'SA' not in message and '203.0.113.9' not in message, message


# --- "Stay here" ----------------------------------------------------------------------

def stay(client, query=''):
    return client.get(reverse('region_stay') + query)


def test_stay_sets_the_cookie_and_returns(client, settings):
    settings.SESSION_COOKIE_SECURE = False  # as with DEBUG on
    response = stay(client, '?next=/es/privacy/')

    assert response.status_code == 302
    assert response['Location'] == '/es/privacy/'
    cookie = response.cookies[STAY_COOKIE]
    assert cookie.value == '1'
    assert cookie['max-age'] == 365 * 24 * 60 * 60
    assert cookie['path'] == '/'
    assert cookie['httponly'] is True
    assert cookie['samesite'] == 'Lax'
    assert not cookie['secure']
    assert 'no-cache' in response['Cache-Control']


def test_stay_cookie_is_secure_as_the_session_cookie(client, settings):
    settings.SESSION_COOKIE_SECURE = True
    assert stay(client, '?next=/en/').cookies[STAY_COOKIE]['secure'] is True


@pytest.mark.parametrize('query', [
    '', '?next=', '?next=https://evil.example', '?next=//evil.example/',
    '?next=https://evil.example/en/', '?next=javascript:alert(1)', '?next=\\\\evil.example',
])
def test_stay_refuses_an_unsafe_next(client, query):
    response = stay(client, query)
    assert response.status_code == 302
    assert response['Location'] == '/'
    assert response.cookies[STAY_COOKIE].value == '1'


def test_stay_keeps_the_query_of_next(client):
    response = stay(client, '?next=/en/privacy/%3Fa%3D1%26b%3D2')
    assert response['Location'] == '/en/privacy/?a=1&b=2'


def test_stay_accepts_get_only(client):
    assert client.post(reverse('region_stay')).status_code == 405


def test_stay_route_is_outside_the_language_prefixes():
    assert reverse('region_stay') == '/region/stay/'


def test_following_stay_hides_the_bar(client):
    bar = bar_of(get(client, '/es/privacy/', country='SA'))
    href = re.search(r'href="(/region/stay/[^"]+)" [^>]*data-region-stay', bar).group(1)

    response = client.get(href.replace('&amp;', '&'), **{OVERRIDE_META_KEY: 'SA',
                                                          'HTTP_USER_AGENT': BROWSER_UA})
    assert response['Location'] == '/es/privacy/'
    assert BAR not in get(client, '/es/privacy/', country='SA')
    assert BAR not in get(client, '/en/', country='SA')


# --- The DB-IP attribution ------------------------------------------------------------

@pytest.mark.parametrize('url', ['/en/', '/es/', '/en/privacy/', '/es/terms/'])
def test_footer_credits_db_ip(client, url):
    html = get(client, url)
    footer = re.search(r'<footer.*?</footer>', html, re.S).group(0)
    assert re.search(r'<a href="https://db-ip.com"[^>]*>(IP geolocation by DB-IP|'
                     r'Geolocalización por IP de DB-IP)</a>', footer)


def test_spanish_attribution(client):
    assert 'Geolocalización por IP de DB-IP' in get(client, '/es/')
