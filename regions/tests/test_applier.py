"""Epic-catalogue entry 3: the configuration snapshot's `country_map` section, applied
(regions.applier through integration.config). The admin site is the fake of
integration/tests/test_config.py (fixed, non-secret test seeds; no network)."""
import logging

import pytest

from integration import config
from integration.tests.test_config import (ADMIN, admin,  # noqa: F401
                                           interface_settings, snapshot, state)
from mytp_core import snapshots
from regions import applier
from regions.countries import lookup_country, seed_state
from regions.models import BANNED, Country
from regions.profiles import get_profile
from signup.models import PendingRegistration
from signup.tests.helpers import _step1

pytestmark = pytest.mark.django_db

EU_SITE = 'https://eu.example.test'
AE_SITE = 'https://ae.example.test'


@pytest.fixture
def eu_site(settings):
    settings.REGION = 'eu'
    settings.REGION_PROFILE = get_profile('eu')
    settings.REGION_SITE_URLS = {'eu': EU_SITE, 'ae': AE_SITE}


def country_map(*pairs):
    return {'countries': [{'code': code, 'state': state} for code, state in pairs]}


def pull(admin, version, section):  # noqa: F811
    admin.snapshot = snapshot(version=version)
    if section is not None:
        admin.snapshot['country_map'] = section
    return config.pull()


def rows():
    return dict(Country.objects.values_list('code', 'state'))


def answers():
    return {code: lookup_country(code, 'en') for code in rows()}


# --- The I/O matrix ---------------------------------------------------------------------

def test_a_seed_equal_map_changes_no_answer(admin, eu_site, caplog):  # noqa: F811
    caplog.set_level(logging.INFO, logger='integration.config')
    before_rows, before = rows(), answers()
    assert len(before_rows) == 251
    section = {'countries': [{'code': code, 'state': seed_state(code)}
                             for code in sorted(before_rows)]}
    assert pull(admin, 1, section) == ('applied', 1)
    assert rows() == before_rows
    assert answers() == before
    assert 'country map: 251 listed, 0 changed, 0 added, 0 skipped' in caplog.text


def test_a_partial_map_changes_only_its_countries(admin, eu_site, caplog):  # noqa: F811
    caplog.set_level(logging.INFO, logger='integration.config')
    seed = rows()
    pull(admin, 1, country_map(('FR', 'ae')))
    after = rows()
    assert after['FR'] == 'ae'
    assert {code: state for code, state in after.items() if code != 'FR'} == {
        code: state for code, state in seed.items() if code != 'FR'}
    assert len(after) == 251
    assert 'country map: 1 listed, 1 changed, 0 added, 0 skipped' in caplog.text


def test_a_moved_country_is_answered_by_its_new_region(admin, eu_site):  # noqa: F811
    assert lookup_country('FR').state == 'eu'
    pull(admin, 1, country_map(('FR', 'ae')))
    answer = lookup_country('FR', 'en')
    assert answer.state == 'ae' and answer.other_region
    assert answer.signup_url == AE_SITE + '/en/signup/'
    # Countries the section does not list keep their rows.
    assert lookup_country('DE').state == 'eu' and lookup_country('RU').banned


def test_a_newly_banned_country_is_refused_at_signup(client, admin, eu_site,  # noqa: F811
                                                     portal_urls):
    assert lookup_country('US').state == 'eu'
    pull(admin, 1, country_map(('US', BANNED)))
    assert lookup_country('US').banned
    response = _step1(client, country='US')
    errors = response.context['form'].errors.as_data()
    assert [error.code for error in errors['country']] == ['country_banned']
    assert not PendingRegistration.objects.exists()
    # Set back: the country is served again.
    pull(admin, 2, country_map(('US', 'eu')))
    assert lookup_country('US').state == 'eu'


def test_a_snapshot_without_the_section_keeps_the_rows(admin, eu_site):  # noqa: F811
    seed = rows()
    # An older admin site: the seed stays the map.
    assert pull(admin, 1, None) == ('applied', 1)
    assert rows() == seed
    pull(admin, 2, country_map(('FR', 'ae')))
    # The section missing again (an admin site rolled back) keeps the applied rows.
    assert pull(admin, 3, None) == ('applied', 3)
    assert rows() == {**seed, 'FR': 'ae'}


def test_an_unknown_state_is_skipped_and_the_row_kept(admin, eu_site):  # noqa: F811
    pull(admin, 1, country_map(('FR', 'quarantined'), ('DE', 'ae'), ('XK', 'quarantined')))
    assert lookup_country('FR').state == 'eu'
    assert lookup_country('DE').state == 'ae'
    # A new code with an unknown state is not created either.
    assert not Country.objects.filter(code='XK').exists()
    assert len(rows()) == 251


def test_a_country_without_a_row_is_added(admin, eu_site):  # noqa: F811
    assert not Country.objects.filter(code='XK').exists()
    assert lookup_country('XK').state == 'eu'
    pull(admin, 1, country_map(('XK', BANNED)))
    assert Country.objects.get(code='XK').state == BANNED


@pytest.mark.parametrize('section', [
    country_map(('FR', 'ae'), ('FR', BANNED)),
    country_map(('fr', 'ae')),
    country_map(('FR', 'AE')),
    {'countries': [{'code': 'FR'}]},
], ids=['code-twice', 'code-lowercase', 'state-uppercase', 'state-missing'])
def test_an_invalid_map_is_refused_and_the_rows_kept(admin, eu_site, section):  # noqa: F811
    import jwt
    pull(admin, 1, country_map(('FR', 'ae')))
    applied = rows()

    def invalid(answer):
        # Signed correctly over a snapshot whose country map breaks the contract.
        bad = {**snapshot(version=2), 'country_map': section}
        claims = jwt.decode(answer['signature'], options={'verify_signature': False})
        claims['sha'] = snapshots.snapshot_sha(bad)
        answer['snapshot'] = bad
        answer['signature'] = ADMIN.sign(claims)
    admin.tamper = invalid
    with pytest.raises(config.PullFailed) as error:
        pull(admin, 2, country_map(('DE', 'ae')))
    assert error.value.code == 'invalid_body'
    assert rows() == applied and state().version == 1


def test_a_failing_apply_keeps_the_previous_map(admin, eu_site, monkeypatch):  # noqa: F811
    # The real country map applier runs; the catalogue's (applied in the same
    # transaction) fails, so the whole snapshot is rolled back, the map's change too.
    pull(admin, 1, country_map(('FR', 'ae')))
    assert config._SECTION_APPLIERS['country_map'] is applier.apply

    def broken(section):
        raise RuntimeError('boom')

    monkeypatch.setitem(config._SECTION_APPLIERS, 'catalogue', broken)
    with pytest.raises(config.PullFailed) as error:
        pull(admin, 2, country_map(('DE', BANNED)))
    assert error.value.code == 'apply_failed'
    assert state().version == 1
    assert lookup_country('DE').state == 'eu' and lookup_country('FR').state == 'ae'


def test_the_country_map_applier_is_registered_once():
    assert 'country_map' in config.section_applier_names()
    config.register_section_applier('country_map', applier.apply)  # the same: a no-op
    with pytest.raises(ValueError):
        config.register_section_applier('country_map', lambda section: None)


def test_the_applied_log_line_has_counts_only(admin, eu_site, caplog):  # noqa: F811
    caplog.set_level(logging.INFO, logger='integration.config')
    pull(admin, 1, country_map(('FR', 'ae'), ('DE', 'eu'), ('XK', BANNED),
                               ('IT', 'quarantined')))
    assert ('country map: 4 listed, 1 changed, 1 added, 1 skipped') in caplog.text
    caplog.clear()
    pull(admin, 2, None)
    assert caplog.messages == ['config snapshot v2 applied (2 admin directory entries); '
                               'no catalogue section; no provider costs section; '
                               'no vouchers']
    assert 'quarantined' not in caplog.text and 'XK' not in caplog.text
