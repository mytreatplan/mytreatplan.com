"""Story 1.8: staging-ready settings. HTTPS with DEBUG off, database TLS deploy check,
noindex switch and the PORTAL_ENABLED hook."""
import importlib
import runpy
import types
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core.checks import run_checks
from django.http import HttpResponse
from django.test import override_settings
from django.urls import NoReverseMatch, Resolver404, clear_url_caches, path, resolve, reverse
from django.utils import translation

import mytreatplan.urls
from mytp_publicsite.checks import (INSECURE_DB_TLS, TURNSTILE_TEST_KEYS, check_db_tls,
                                    check_turnstile_keys)

SETTINGS_FILE = str(django_settings.BASE_DIR / 'mytreatplan' / 'settings.py')
# Every key these tests depend on, at its documented default. Set explicitly in the
# environment (which wins over settings.env), so a developer's settings.env can't leak in.
BASELINE_ENV = {
    'CSRF_TRUSTED_ORIGINS': '',
    'SECURE_SSL_REDIRECT': 'True',
    'SECURE_HSTS_SECONDS': '0',
    'DB_SSLMODE': '',
    'DB_SSLROOTCERT': '',
    'ROBOTS_NOINDEX': 'False',
    'PORTAL_ENABLED': 'False',
    'SENTRY_DSN': '',
}


@pytest.fixture
def load_settings(monkeypatch):
    """Evaluate settings.py afresh with the given environment; returns its namespace."""
    def load(**env):
        for name, value in {**BASELINE_ENV, **env}.items():
            monkeypatch.setenv(name, value)
        return runpy.run_path(SETTINGS_FILE)
    return load


# --- Secure settings with DEBUG off ---------------------------------------------------

def test_debug_off_turns_on_https_settings(load_settings):
    s = load_settings(DEBUG='False', SECURE_HSTS_SECONDS='3600',
                      CSRF_TRUSTED_ORIGINS='https://dev.mytpdso.com')

    assert s['SESSION_COOKIE_SECURE'] is True
    assert s['CSRF_COOKIE_SECURE'] is True
    assert s['SESSION_COOKIE_HTTPONLY'] is True
    assert s['SECURE_PROXY_SSL_HEADER'] == ('HTTP_X_FORWARDED_PROTO', 'https')
    assert s['SECURE_SSL_REDIRECT'] is True
    assert s['SECURE_HSTS_SECONDS'] == 3600
    assert not s.get('SECURE_HSTS_PRELOAD', False)
    assert s['SECURE_CONTENT_TYPE_NOSNIFF'] is True
    assert s['X_FRAME_OPTIONS'] == 'DENY'
    assert s['CSRF_TRUSTED_ORIGINS'] == ['https://dev.mytpdso.com']


def test_debug_off_ssl_redirect_can_be_overridden(load_settings):
    s = load_settings(DEBUG='False', SECURE_SSL_REDIRECT='False')

    assert s['SECURE_SSL_REDIRECT'] is False


def test_debug_off_proxied_https_request_is_secure(client, settings):
    settings.SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    settings.SECURE_SSL_REDIRECT = True
    settings.X_FRAME_OPTIONS = 'DENY'
    settings.SECURE_CONTENT_TYPE_NOSNIFF = True

    plain = client.get('/en/')
    proxied = client.get('/en/', headers={'x-forwarded-proto': 'https'})

    assert plain.status_code == 301 and plain['Location'].startswith('https://')
    assert proxied.status_code == 200
    assert proxied['X-Frame-Options'] == 'DENY'
    assert proxied['X-Content-Type-Options'] == 'nosniff'


# --- Local development is unchanged ---------------------------------------------------

def test_local_dev_defaults(load_settings):
    s = load_settings(DEBUG='True')

    for name in ['SESSION_COOKIE_SECURE', 'CSRF_COOKIE_SECURE', 'SECURE_PROXY_SSL_HEADER',
                 'SECURE_SSL_REDIRECT', 'SECURE_HSTS_SECONDS', 'X_FRAME_OPTIONS']:
        assert name not in s, name
    assert s['CSRF_TRUSTED_ORIGINS'] == []
    assert s['ROBOTS_NOINDEX'] is False
    assert s['PORTAL_ENABLED'] is False
    options = s['DATABASES']['default'].get('OPTIONS', {})
    assert 'sslmode' not in options and 'sslrootcert' not in options


# --- Noindex --------------------------------------------------------------------------

@override_settings(ROBOTS_NOINDEX=True)
@pytest.mark.parametrize('url', ['/en/', '/es/privacy/', '/robots.txt', '/sitemap.xml',
                                 '/', '/en/no-such-page/'])
def test_noindex_header_on_every_response(client, url):
    assert client.get(url)['X-Robots-Tag'] == 'noindex, nofollow'


@override_settings(ROBOTS_NOINDEX=True)
def test_noindex_robots_txt_disallows_all(client):
    response = client.get('/robots.txt')

    assert response.status_code == 200
    assert response['Content-Type'].startswith('text/plain')
    lines = response.content.decode().splitlines()
    assert lines == ['User-agent: *', 'Disallow: /']
    assert not any(line.startswith('Sitemap') for line in lines)


@pytest.mark.parametrize('url', ['/en/', '/robots.txt'])
def test_noindex_off_by_default(client, url):
    assert not django_settings.ROBOTS_NOINDEX
    assert 'X-Robots-Tag' not in client.get(url)


def test_noindex_off_robots_txt_as_before(client):
    lines = client.get('/robots.txt').content.decode().splitlines()

    assert 'Allow: /' in lines
    assert any(line.startswith('Sitemap: ') for line in lines)


def test_noindex_env_var(load_settings):
    assert load_settings(DEBUG='True', ROBOTS_NOINDEX='True')['ROBOTS_NOINDEX'] is True


# --- Database TLS ---------------------------------------------------------------------

@pytest.mark.parametrize('sslmode', ['', 'require', 'disable', 'prefer'])
def test_insecure_db_tls_fails_deploy_check(settings, sslmode):
    settings.DEBUG = False
    settings.DB_SSLMODE = sslmode

    errors = check_db_tls(None)

    assert [e.id for e in errors] == [INSECURE_DB_TLS]
    assert 'DB_SSLMODE' in errors[0].msg
    assert INSECURE_DB_TLS in {e.id for e in run_checks(include_deployment_checks=True)}


@pytest.mark.parametrize('sslmode', ['verify-full', 'verify-ca'])
def test_verified_db_tls_passes_deploy_check(settings, sslmode):
    settings.DEBUG = False
    settings.DB_SSLMODE = sslmode

    assert check_db_tls(None) == []


def test_db_tls_check_skipped_with_debug_on(settings):
    settings.DEBUG = True
    settings.DB_SSLMODE = ''

    assert check_db_tls(None) == []


def test_db_tls_check_only_runs_on_deploy(settings):
    settings.DEBUG = False
    settings.DB_SSLMODE = ''

    assert INSECURE_DB_TLS not in {e.id for e in run_checks()}


def test_db_tls_env_feeds_connection_options(load_settings):
    s = load_settings(DEBUG='False', DB_SSLMODE='verify-full',
                      DB_SSLROOTCERT='/etc/mytreatplan/db-ca.pem')

    options = s['DATABASES']['default']['OPTIONS']
    assert options['sslmode'] == 'verify-full'
    assert options['sslrootcert'] == '/etc/mytreatplan/db-ca.pem'
    assert s['DB_SSLMODE'] == 'verify-full'


# --- PORTAL_ENABLED hook --------------------------------------------------------------

def _stub(request):
    return HttpResponse('stub')


@pytest.fixture
def stub_portal_patterns(monkeypatch):
    monkeypatch.setattr(mytreatplan.urls, 'portal_patterns',
                        [path('signup/', _stub, name='signup')])


def _fresh_urlconf():
    """A fresh copy of mytreatplan.urls, built with the current PORTAL_ENABLED, without
    touching the real URLconf module."""
    module = types.ModuleType('mytp_publicsite.tests._portal_urlconf')
    module.__file__ = mytreatplan.urls.__file__
    source = Path(module.__file__).read_text()
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    return module


PORTAL_URL_NAMES = ['signup', 'signup_step', 'welcome']


def test_portal_patterns_hold_the_signup_routes(settings):
    settings.PORTAL_ENABLED = True
    urlconf = _fresh_urlconf()
    try:
        with translation.override('en'):
            assert reverse('signup', urlconf) == '/en/signup/'
            assert reverse('signup_step', urlconf, args=['verify-email']) == \
                '/en/signup/verify-email/'
            assert reverse('welcome', urlconf) == '/en/welcome/'
    finally:
        clear_url_caches()


@override_settings(PORTAL_ENABLED=True)
def test_portal_hook_included_when_enabled(stub_portal_patterns):
    assert [p.name for p in mytreatplan.urls.portal_urlpatterns()] == ['signup']


@override_settings(PORTAL_ENABLED=False)
def test_portal_hook_excluded_when_disabled(stub_portal_patterns):
    assert mytreatplan.urls.portal_urlpatterns() == []


@pytest.fixture
def reload_urlconf():
    original = django_settings.PORTAL_ENABLED
    yield lambda: (importlib.reload(mytreatplan.urls), clear_url_caches())
    # Rebuild with the switch as it was, before pytest-django's `settings` restores it,
    # so the portal routes don't stay in the URLconf for the rest of the session.
    with override_settings(PORTAL_ENABLED=original):
        importlib.reload(mytreatplan.urls)
    clear_url_caches()


@pytest.mark.parametrize('enabled', [True, False])
def test_portal_hook_is_wired_into_language_patterns(settings, enabled):
    settings.PORTAL_ENABLED = enabled
    urlconf = _fresh_urlconf()
    try:
        for lang in ['en', 'es']:
            with translation.override(lang):  # the prefix matches the active language
                for url, name in [(f'/{lang}/signup/', 'signup'),
                                  (f'/{lang}/signup/verify-email/', 'signup_step'),
                                  (f'/{lang}/welcome/', 'welcome')]:
                    if enabled:
                        assert resolve(url, urlconf).url_name == name
                    else:
                        with pytest.raises(Resolver404):
                            resolve(url, urlconf)
                if not enabled:
                    for name in PORTAL_URL_NAMES:
                        with pytest.raises(NoReverseMatch):
                            reverse(name, urlconf, args=['x'] if name == 'signup_step' else [])
        for url in ['/signup/', '/welcome/']:
            with pytest.raises(Resolver404):
                resolve(url, urlconf)  # only under the language prefixes
        assert resolve('/en/', urlconf).url_name == 'home'
    finally:
        clear_url_caches()


@pytest.mark.django_db
@pytest.mark.parametrize('enabled', [True, False])
def test_urlconf_loads_with_portal_switch(settings, reload_urlconf, client, enabled):
    """The real URLconf, rebuilt with the switch: signup is served and linked from the
    nav only when it is on (the Log in link waits for epic-login)."""
    settings.PORTAL_ENABLED = enabled
    reload_urlconf()

    with translation.override('en'):  # an earlier /es/ request may have left Spanish active
        assert resolve('/en/').url_name == 'home'
    home = client.get('/en/')
    assert home.status_code == 200
    html = home.content.decode()
    assert ('href="/en/signup/"' in html) is enabled
    assert 'data-portal-link="login"' not in html
    assert client.get('/en/signup/').status_code == (200 if enabled else 404)
    assert client.get('/es/signup/').status_code == (200 if enabled else 404)


@pytest.mark.django_db
def test_portal_routes_do_not_leak_after_reload(client):
    """Runs after the reloads above: reload_urlconf's teardown put the switch-off URLconf back."""
    assert not django_settings.PORTAL_ENABLED
    assert client.get('/en/signup/').status_code == 404
    assert 'href="/en/signup/"' not in client.get('/en/').content.decode()


def test_portal_env_var(load_settings):
    assert load_settings(DEBUG='True', PORTAL_ENABLED='True')['PORTAL_ENABLED'] is True


# --- Turnstile keys (signup entry 9) ------------------------------------------------------

REAL_SITE_KEY = '0x4AAAAAAAexampleSiteKey'
REAL_SECRET_KEY = '0x4AAAAAAAexampleSecretKey'


@pytest.mark.parametrize('site_key, secret_key, names', [
    ('1x00000000000000000000AA', '1x0000000000000000000000000000000AA',
     'TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY'),
    (REAL_SITE_KEY, '2x0000000000000000000000000000000AA', 'TURNSTILE_SECRET_KEY'),
    ('3x00000000000000000000FF', REAL_SECRET_KEY, 'TURNSTILE_SITE_KEY'),
])
def test_turnstile_test_keys_fail_deploy_check(settings, site_key, secret_key, names):
    settings.DEBUG = False
    settings.TURNSTILE_SITE_KEY = site_key
    settings.TURNSTILE_SECRET_KEY = secret_key

    errors = check_turnstile_keys(None)

    assert [e.id for e in errors] == [TURNSTILE_TEST_KEYS]
    assert errors[0].msg.startswith(names + ' ')
    assert TURNSTILE_TEST_KEYS in {e.id for e in run_checks(include_deployment_checks=True)}


def test_default_turnstile_keys_are_the_test_keys_and_fail(settings):
    """The settings default to the test keys, so an unset key fails the deploy check."""
    settings.DEBUG = False
    assert [e.id for e in check_turnstile_keys(None)] == [TURNSTILE_TEST_KEYS]


def test_real_turnstile_keys_pass_deploy_check(settings):
    settings.DEBUG = False
    settings.TURNSTILE_SITE_KEY = REAL_SITE_KEY
    settings.TURNSTILE_SECRET_KEY = REAL_SECRET_KEY

    assert check_turnstile_keys(None) == []


def test_turnstile_check_skipped_with_debug_on(settings):
    settings.DEBUG = True

    assert check_turnstile_keys(None) == []
