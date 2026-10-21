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
from accounts.crypto import DEFAULT_KEY_PROVIDER
from mytp_publicsite.checks import (FAKE_VERIFICATION_PROVIDER, INSECURE_DB_TLS,
                                    NO_ERASURE_RETENTION, PLAIN_FIELD_KEYS, TURNSTILE_TEST_KEYS,
                                    check_db_tls, check_erasure_retention,
                                    check_field_key_provider, check_turnstile_keys,
                                    check_verification_provider)
from signup.twilio_verify import TWILIO_PROVIDER as TWILIO
from signup.verification import FAKE_PROVIDER as FAKE

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
    'ACCOUNT_ERASURE_RETENTION_DAYS': '',
    'VERIFICATION_PROVIDER': FAKE,
    'TWILIO_VERIFY_BASE_URL': '',
    'TWILIO_WHATSAPP_ENABLED': 'False',
    'EMAIL_HOST': '',
    'EMAIL_USE_SSL': 'False',
}
# Unset in the process environment for every load, so their defaults apply (a local
# settings.env setting them would still be read: decouple falls back to it).
UNSET_ENV = ('EMAIL_PORT', 'EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD',
             'EMAIL_TIMEOUT', 'DEFAULT_FROM_EMAIL', 'SERVER_EMAIL')


@pytest.fixture
def load_settings(monkeypatch):
    """Evaluate settings.py afresh with the given environment; returns its namespace."""
    def load(**env):
        for name in UNSET_ENV:
            monkeypatch.delenv(name, raising=False)
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


PORTAL_URL_NAMES = ['signup', 'signup_step', 'welcome', 'login', 'logout']


def test_portal_patterns_hold_the_signup_routes(settings):
    settings.PORTAL_ENABLED = True
    urlconf = _fresh_urlconf()
    try:
        with translation.override('en'):
            assert reverse('signup', urlconf) == '/en/signup/'
            assert reverse('signup_step', urlconf, args=['verify-email']) == \
                '/en/signup/verify-email/'
            assert reverse('welcome', urlconf) == '/en/welcome/'
            assert reverse('login', urlconf) == '/en/login/'
            assert reverse('logout', urlconf) == '/en/logout/'
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
                                  (f'/{lang}/welcome/', 'welcome'),
                                  (f'/{lang}/login/', 'login'),
                                  (f'/{lang}/logout/', 'logout')]:
                    if enabled:
                        assert resolve(url, urlconf).url_name == name
                    else:
                        with pytest.raises(Resolver404):
                            resolve(url, urlconf)
                if not enabled:
                    for name in PORTAL_URL_NAMES:
                        with pytest.raises(NoReverseMatch):
                            reverse(name, urlconf, args=['x'] if name == 'signup_step' else [])
        for url in ['/signup/', '/welcome/', '/login/', '/logout/']:
            with pytest.raises(Resolver404):
                resolve(url, urlconf)  # only under the language prefixes
        assert resolve('/en/', urlconf).url_name == 'home'
    finally:
        clear_url_caches()


@pytest.mark.django_db
@pytest.mark.parametrize('enabled', [True, False])
def test_urlconf_loads_with_portal_switch(settings, reload_urlconf, client, enabled):
    """The real URLconf, rebuilt with the switch: signup and login are served and linked
    from the nav only when it is on."""
    settings.PORTAL_ENABLED = enabled
    reload_urlconf()

    with translation.override('en'):  # an earlier /es/ request may have left Spanish active
        assert resolve('/en/').url_name == 'home'
    home = client.get('/en/')
    assert home.status_code == 200
    html = home.content.decode()
    assert ('href="/en/signup/"' in html) is enabled
    assert ('href="/en/login/"' in html) is enabled
    assert ('data-portal-link="login"' in html) is enabled
    assert client.get('/en/signup/').status_code == (200 if enabled else 404)
    assert client.get('/es/signup/').status_code == (200 if enabled else 404)
    assert client.get('/en/login/').status_code == (200 if enabled else 404)
    assert client.get('/es/login/').status_code == (200 if enabled else 404)


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


# --- Field key provider (story 2.12) -------------------------------------------------------

@pytest.mark.parametrize('provider', [DEFAULT_KEY_PROVIDER, ''])
def test_plain_field_keys_fail_deploy_check(settings, provider):
    settings.DEBUG = False
    settings.FIELD_KEY_PROVIDER = provider

    errors = check_field_key_provider(None)

    assert [e.id for e in errors] == [PLAIN_FIELD_KEYS]
    assert 'EnvKeyProvider' in errors[0].msg
    assert 'accounts.crypto.KMSKeyProvider' in errors[0].hint
    assert PLAIN_FIELD_KEYS in {e.id for e in run_checks(include_deployment_checks=True)}


def test_kms_field_key_provider_passes_deploy_check(settings):
    settings.DEBUG = False
    settings.FIELD_KEY_PROVIDER = 'accounts.crypto.KMSKeyProvider'

    assert check_field_key_provider(None) == []


def test_field_key_provider_check_skipped_with_debug_on(settings):
    settings.DEBUG = True
    settings.FIELD_KEY_PROVIDER = DEFAULT_KEY_PROVIDER

    assert check_field_key_provider(None) == []


# --- Account erasure retention (plan-account-erasure) ---------------------------------------

@pytest.mark.parametrize('days', [None, 0, -5])
def test_unset_erasure_retention_fails_deploy_check(settings, days):
    settings.DEBUG = False
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = days

    errors = check_erasure_retention(None)

    assert [e.id for e in errors] == [NO_ERASURE_RETENTION]
    assert 'ACCOUNT_ERASURE_RETENTION_DAYS must be a positive number of days' in errors[0].msg
    assert NO_ERASURE_RETENTION in {e.id for e in run_checks(include_deployment_checks=True)}
    assert NO_ERASURE_RETENTION not in {e.id for e in run_checks()}


def test_set_erasure_retention_passes_deploy_check(settings):
    settings.DEBUG = False
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 1

    assert check_erasure_retention(None) == []


def test_erasure_retention_check_skipped_with_debug_on(settings):
    settings.DEBUG = True
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = None

    assert check_erasure_retention(None) == []


@pytest.mark.parametrize('value, expected', [('', None), ('  ', None), ('30', 30), ('1', 1)])
def test_erasure_retention_env_var_has_no_default(load_settings, value, expected):
    s = load_settings(DEBUG='True', ACCOUNT_ERASURE_RETENTION_DAYS=value)

    assert s['ACCOUNT_ERASURE_RETENTION_DAYS'] == expected


# --- Verification provider (signup entry 11) ----------------------------------------------

@pytest.mark.parametrize('provider', [FAKE, ''])
def test_fake_verification_provider_fails_deploy_check(settings, provider):
    settings.DEBUG = False
    settings.VERIFICATION_PROVIDER = provider

    errors = check_verification_provider(None)

    assert [e.id for e in errors] == [FAKE_VERIFICATION_PROVIDER]
    assert 'FakeVerificationProvider' in errors[0].msg
    assert TWILIO in errors[0].hint
    assert FAKE_VERIFICATION_PROVIDER in {e.id for e in run_checks(include_deployment_checks=True)}
    assert FAKE_VERIFICATION_PROVIDER not in {e.id for e in run_checks()}


def test_twilio_verification_provider_passes_deploy_check(settings):
    settings.DEBUG = False
    settings.VERIFICATION_PROVIDER = TWILIO

    assert check_verification_provider(None) == []


def test_verification_provider_check_skipped_with_debug_on(settings):
    settings.DEBUG = True
    settings.VERIFICATION_PROVIDER = FAKE

    assert check_verification_provider(None) == []


def test_twilio_settings_defaults(load_settings):
    s = load_settings(DEBUG='True')

    assert s['VERIFICATION_PROVIDER'] == FAKE
    assert s['TWILIO_VERIFY_BASE_URL'] == 'https://verify.twilio.com/v2'
    assert s['TWILIO_WHATSAPP_ENABLED'] is False


def test_twilio_settings_from_env(load_settings):
    s = load_settings(DEBUG='True', VERIFICATION_PROVIDER=TWILIO, TWILIO_WHATSAPP_ENABLED='True',
                      TWILIO_VERIFY_BASE_URL='https://verify.test.invalid/v2')

    assert s['VERIFICATION_PROVIDER'] == TWILIO
    assert s['TWILIO_WHATSAPP_ENABLED'] is True
    assert s['TWILIO_VERIFY_BASE_URL'] == 'https://verify.test.invalid/v2'


# --- Transactional email (epic-login entry 9) --------------------------------------------

NOREPLY = 'MyTreatPlan <noreply@mytreatplan.com>'


def test_email_without_a_host_prints_to_the_console(load_settings):
    s = load_settings()
    assert s['MAILERS'] == {'default': {
        'BACKEND': 'django.core.mail.backends.console.EmailBackend'}}
    assert s['DEFAULT_FROM_EMAIL'] == NOREPLY
    assert s['SERVER_EMAIL'] == NOREPLY


def test_email_host_uses_smtp_with_starttls_by_default(load_settings):
    s = load_settings(EMAIL_HOST='smtp.example.invalid', EMAIL_HOST_USER='noreply@mytreatplan.com',
                      EMAIL_HOST_PASSWORD='not-a-real-password')
    mailer = s['MAILERS']['default']
    assert mailer['BACKEND'] == 'django.core.mail.backends.smtp.EmailBackend'
    assert mailer['OPTIONS'] == {
        'host': 'smtp.example.invalid', 'port': 587,
        'username': 'noreply@mytreatplan.com', 'password': 'not-a-real-password',
        'use_ssl': False, 'use_tls': True, 'timeout': 10,
    }


def test_email_ssl_uses_port_465_and_no_starttls(load_settings):
    options = load_settings(EMAIL_HOST='smtp.example.invalid',
                            EMAIL_USE_SSL='True')['MAILERS']['default']['OPTIONS']
    assert (options['port'], options['use_ssl'], options['use_tls']) == (465, True, False)


def test_no_deprecated_email_settings_next_to_mailers(load_settings):
    """Django 6.1 refuses EMAIL_HOST and the like when MAILERS is defined."""
    s = load_settings(EMAIL_HOST='smtp.example.invalid')
    assert not [name for name in s if name.startswith('EMAIL_')]


@pytest.mark.parametrize('host, failing', [('', True), ('smtp.example.invalid', False)])
def test_deploy_check_fails_while_mail_goes_to_the_console(load_settings, host, failing):
    """The project's own MAILERS: without EMAIL_HOST, `check --deploy` lists mail.E001."""
    mailers = load_settings(EMAIL_HOST=host)['MAILERS']
    with override_settings(DEBUG=False, MAILERS=mailers):
        ids = {error.id for error in run_checks(include_deployment_checks=True)}
    assert ('mail.E001' in ids) is failing
