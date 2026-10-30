"""Epic-platform entry 6: settings take the time zone, the languages and the default
hosts and site URL from the REGION profile; settings.env values still override."""
import os
import runpy

import decouple
import pytest
from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured

SETTINGS_FILE = str(django_settings.BASE_DIR / 'mytreatplan' / 'settings.py')


@pytest.fixture
def load_settings(monkeypatch):
    """Evaluate settings.py afresh with only the given environment (and the two required
    values); a developer's settings.env is not read."""
    def load(**env):
        monkeypatch.setattr(decouple, 'RepositoryEnv', lambda path: decouple.RepositoryEmpty())
        monkeypatch.setattr(os, 'environ', {
            'SECRET_KEY': 'regions-tests-not-secret',
            'DATABASE_URL': 'sqlite:///:memory:',
            **env,
        })
        return runpy.run_path(SETTINGS_FILE)
    return load


@pytest.mark.parametrize('env', [{}, {'REGION': ''}, {'REGION': 'eu'}])
def test_eu_by_default(load_settings, env):
    s = load_settings(**env)

    assert s['REGION'] == 'eu'
    assert s['REGION_PROFILE'].code == 'eu'
    # Part 1's values, unchanged.
    assert s['TIME_ZONE'] == 'Europe/Madrid'
    assert s['LANGUAGES'] == [('en', 'English'), ('es', 'Español')]
    assert s['REGION_PROFILE'].currencies == ('EUR', 'USD')
    assert s['REGION_PROFILE'].patient_code_prefix == 'EU'
    assert s['REGION_PROFILE'].invoice_prefix == 'EU'
    assert s['ALLOWED_HOSTS'] == ['mytreatplan.com', 'www.mytreatplan.com']
    assert s['CSRF_TRUSTED_ORIGINS'] == ['https://mytreatplan.com', 'https://www.mytreatplan.com']
    assert s['SITE_URL'] == 'https://mytreatplan.com'
    assert s['STORAGE_ENDPOINT'] == 'https://s3.gra.io.cloud.ovh.net'


def test_ae(load_settings):
    s = load_settings(REGION='ae')

    assert s['REGION'] == 'ae'
    assert s['TIME_ZONE'] == 'Asia/Dubai'
    assert s['LANGUAGES'] == [('en', 'English')]
    assert s['REGION_PROFILE'].currencies == ('AED', 'USD')
    assert s['REGION_PROFILE'].patient_code_prefix == 'AE'
    assert s['REGION_PROFILE'].invoice_prefix == 'AE'
    assert s['ALLOWED_HOSTS'] == ['mytreatplan.ae', 'www.mytreatplan.ae']
    assert s['CSRF_TRUSTED_ORIGINS'] == ['https://mytreatplan.ae', 'https://www.mytreatplan.ae']
    assert s['SITE_URL'] == 'https://mytreatplan.ae'
    assert s['STORAGE_ENDPOINT'] == ''


def test_env_values_override_the_profile(load_settings):
    """dev.mytpdso.com (deploy/staging.env.example) keeps working unchanged."""
    s = load_settings(REGION='eu', ALLOWED_HOSTS='dev.mytpdso.com',
                      CSRF_TRUSTED_ORIGINS='https://dev.mytpdso.com',
                      SITE_URL='https://dev.mytpdso.com/',
                      STORAGE_ENDPOINT='https://s3.sbg.io.cloud.ovh.net')

    assert s['ALLOWED_HOSTS'] == ['dev.mytpdso.com']
    assert s['CSRF_TRUSTED_ORIGINS'] == ['https://dev.mytpdso.com']
    assert s['SITE_URL'] == 'https://dev.mytpdso.com'
    assert s['STORAGE_ENDPOINT'] == 'https://s3.sbg.io.cloud.ovh.net'
    assert s['TIME_ZONE'] == 'Europe/Madrid'


def test_explicit_empty_value_overrides_too(load_settings):
    s = load_settings(CSRF_TRUSTED_ORIGINS='')

    assert s['CSRF_TRUSTED_ORIGINS'] == []


def test_debug_allows_the_local_hosts(load_settings):
    """Without ALLOWED_HOSTS, local development works as before: Django's own DEBUG list."""
    s = load_settings(DEBUG='True')

    assert s['ALLOWED_HOSTS'] == ['mytreatplan.com', 'www.mytreatplan.com',
                                  '.localhost', '127.0.0.1', '[::1]']


@pytest.mark.parametrize('region', ['us', 'EU', 'europe'])
def test_unknown_region_refuses_to_start(load_settings, region):
    with pytest.raises(ImproperlyConfigured, match=r'REGION must be one of ae, eu, got'):
        load_settings(REGION=region)


def test_running_settings_have_the_profile():
    assert django_settings.REGION_PROFILE.code == django_settings.REGION
    assert django_settings.TIME_ZONE == django_settings.REGION_PROFILE.time_zone
    assert django_settings.LANGUAGES == django_settings.REGION_PROFILE.language_choices
