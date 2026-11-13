"""Epic-catalogue entry 11, the rules of the snapshot on the portal: a deactivated object
of every type is still in the snapshot, stored inactive, read by its id or code and no
longer offered; a past order's country group and billing rule are read by id whatever
their state (`country_group_by_id`, `billing_rule_by_id`, `price_catalogue`); a voucher of
another region never reaches this portal. The write guard is
integration/tests/test_snapshot_tables.py. The admin site is the fake of
integration/tests/test_config.py (no network)."""
import copy
import logging
import uuid

import pytest
from django.core.management import CommandError

from catalogue import resolver
from catalogue.models import (AddOn, BillingCompany, BillingRule, CountryGroup, Product,
                              ProviderProduct, Subscription, Voucher)
from catalogue.tests.test_add_ons_costs import ADD_ONS, BYTE_SYNC, COSTS, SCAN
from catalogue.tests.test_billing import (EMIRATES, SPAIN, SUBSCRIPTIONS, TPS, UAE,
                                          billed, offered, rule)
from catalogue.tests.test_catalogue import IBERIA, WORLD, run
from catalogue.tests.test_vouchers import GENERAL, TRY_ME, voucher
from integration import config
from integration.tests.test_config import (admin, interface_settings,  # noqa: F401
                                           snapshot)
from mytp_core.contracts import vouchers_for_region

pytestmark = pytest.mark.django_db

IBERIA_TPS_RULE = rule(1, IBERIA, 'tps_branded', SPAIN, '0.00', [])['id']
AE_VOUCHER = voucher('f0000009-0000-4000-8000-000000000009', 'AEAEAE2345', region='ae',
                     currency='AED')


def everything():
    """The full catalogue (billing's, with the add-ons), the provider costs and a
    voucher, all active."""
    section = billed()
    section['add_ons'] = copy.deepcopy(ADD_ONS)
    return {'catalogue': section, 'provider_costs': copy.deepcopy(COSTS),
            'vouchers': [dict(GENERAL)]}


def pull(admin, version, sections):  # noqa: F811
    admin.snapshot = snapshot(version=version, **sections)
    return config.pull()


def deactivate(entries, id_):
    found = [entry for entry in entries if entry['id'] == id_]
    assert len(found) == 1
    found[0]['active'] = False


def tps_offered(country='ES'):
    return 'TPS branded' in offered(country)


# --- Deactivated object (the I/O matrix): sent, inactive, read, not offered ---------------

def test_a_deactivated_product_is_read_by_id_and_not_offered(admin):  # noqa: F811
    pull(admin, 1, everything())
    assert tps_offered()
    sections = everything()
    deactivate(sections['catalogue']['products'], TPS)
    pull(admin, 2, sections)
    assert Product.objects.filter(pk=TPS, active=False).exists()
    assert resolver.product(TPS).active is False
    assert not tps_offered()


def test_a_deactivated_subscription_is_read_by_id_and_code_and_offers_nothing(admin):  # noqa: F811
    sections = everything()
    deactivate(sections['catalogue']['subscriptions'], SUBSCRIPTIONS[IBERIA])
    pull(admin, 1, sections)
    found = resolver.subscription_by_id(SUBSCRIPTIONS[IBERIA])
    assert found.active is False and resolver.subscription_by_code(found.code) == found
    assert resolver.subscription('ES', 'basic') is None and offered('ES') == []
    assert Subscription.objects.filter(active=False).count() == 1


def test_a_deactivated_add_on_is_read_by_code_and_not_offered(admin):  # noqa: F811
    pull(admin, 1, everything())
    assert 'BYTE-SYNC' in [found.code for found in resolver.add_ons(resolver.product(TPS))]
    sections = everything()
    deactivate(sections['catalogue']['add_ons'], BYTE_SYNC)
    pull(admin, 2, sections)
    assert resolver.add_on('byte-sync').active is False
    assert AddOn.objects.filter(pk=BYTE_SYNC, active=False).exists()
    assert 'BYTE-SYNC' not in [found.code
                               for found in resolver.add_ons(resolver.product(TPS))]


def test_a_deactivated_country_group_is_read_by_id_and_never_chosen(admin):  # noqa: F811
    pull(admin, 1, everything())
    assert resolver.country_group('ES').pk == uuid.UUID(IBERIA)
    sections = everything()
    deactivate(sections['catalogue']['country_groups'], IBERIA)
    pull(admin, 2, sections)
    group = resolver.country_group_by_id(IBERIA)
    assert group.name == 'Iberia' and group.active is False
    assert resolver.country_group_by_id(uuid.UUID(IBERIA)) == group
    assert resolver.country_group('ES').pk == uuid.UUID(WORLD)    # falls to Rest of world
    assert CountryGroup.objects.filter(active=False).count() == 1


def test_a_deactivated_billing_company_is_read_by_code(admin):  # noqa: F811
    sections = everything()
    deactivate(sections['catalogue']['billing_companies'], EMIRATES)
    for found in sections['catalogue']['billing_rules']:
        if found['company'] == EMIRATES:
            found['active'] = False
    pull(admin, 1, sections)
    company = resolver.billing_company('ae')
    assert company.pk == uuid.UUID(EMIRATES) and company.active is False
    assert BillingCompany.objects.filter(active=False).count() == 1
    assert resolver.billing_rule('AE', 'tps_branded') is None and offered('AE') == []
    assert resolver.country_group('AE').pk == uuid.UUID(UAE)


def test_a_deactivated_billing_rule_is_read_by_id_and_not_the_rule(admin):  # noqa: F811
    pull(admin, 1, everything())
    assert resolver.billing_rule('ES', 'tps_branded').pk == uuid.UUID(IBERIA_TPS_RULE)
    sections = everything()
    deactivate(sections['catalogue']['billing_rules'], IBERIA_TPS_RULE)
    pull(admin, 2, sections)
    found = resolver.billing_rule_by_id(IBERIA_TPS_RULE)
    assert found.active is False and found.company.code == 'ES'
    assert resolver.billing_rule('ES', 'tps_branded') is None and not tps_offered()
    assert BillingRule.objects.filter(active=False).count() == 1


def test_a_deactivated_provider_product_is_read_by_id(admin):  # noqa: F811
    sections = everything()
    deactivate(sections['provider_costs']['products'], SCAN)
    pull(admin, 1, sections)
    found = resolver.provider_product_by_id(SCAN)
    assert found.active is False and found.code == 'SCAN' and len(found.costs.all()) == 1
    assert ProviderProduct.objects.filter(active=False).count() == 1


def test_a_deactivated_voucher_is_read_by_code_and_not_usable(admin):  # noqa: F811
    sections = everything()
    sections['vouchers'][0]['active'] = False
    pull(admin, 1, sections)
    found = resolver.voucher_by_code('K7QM2XH9TR')
    assert found.pk == uuid.UUID(TRY_ME) and found.active is False
    assert resolver.voucher_state(found) == resolver.INACTIVE


# --- Past orders: by id whatever the state; an unknown id gives nothing -----------------

@pytest.mark.parametrize('typed', [None, '', 'not-a-uuid', str(uuid.uuid4()), uuid.uuid4()])
def test_an_unknown_id_gives_nothing(admin, typed):  # noqa: F811
    pull(admin, 1, everything())
    assert resolver.country_group_by_id(typed) is None
    assert resolver.billing_rule_by_id(typed) is None
    assert resolver.provider_product_by_id(typed) is None


def test_price_catalogue_prints_a_deactivated_group_and_rule_by_id(admin):  # noqa: F811
    sections = everything()
    deactivate(sections['catalogue']['country_groups'], IBERIA)
    deactivate(sections['catalogue']['billing_rules'], IBERIA_TPS_RULE)
    pull(admin, 1, sections)
    out = run('--country-group', IBERIA)
    assert f'{IBERIA}  Iberia' in out and 'status: inactive' in out
    assert 'countries: ES, PT' in out and 'chosen for new doctors: no' in out
    assert 'every country in no other active group' in run('--country-group', WORLD)
    out = run('--billing-rule', IBERIA_TPS_RULE)
    assert 'Iberia tps_branded: billing rule' in out and 'status: inactive' in out
    assert 'company: ES  MyTPDSO Spain S.L.' in out and 'bills new orders: no' in out
    for option in ('--country-group', '--billing-rule'):
        with pytest.raises(CommandError, match='No '):
            run(option, str(uuid.uuid4()))
    with pytest.raises(CommandError, match='alone'):
        run('--country-group', IBERIA, '--billing-rule', IBERIA_TPS_RULE)
    with pytest.raises(CommandError, match='alone'):
        run('--country-group', IBERIA, '--country', 'ES', '--tier', 'basic')


# --- A voucher of ae never reaches dev (the acceptance criterion) -------------------------

def test_a_voucher_of_ae_leaves_no_row_and_no_skipped_line_on_dev(admin, settings,  # noqa: F811
                                                                  caplog):
    """The admin site stores every region's vouchers in one body and serves dev only
    eu's (mytp-core's vouchers_for_region, as interface.views.config does)."""
    settings.REGION = 'eu'
    stored = snapshot(version=1, **everything())
    stored['vouchers'] = [dict(GENERAL), dict(AE_VOUCHER)]
    admin.snapshot = vouchers_for_region(stored, 'eu')
    with caplog.at_level(logging.WARNING):
        assert config.pull() == ('applied', 1)
    assert list(Voucher.objects.values_list('code', flat=True)) == ['K7QM2XH9TR']
    assert resolver.voucher_by_code('AEAEAE2345') is None
    assert 'skipped' not in caplog.text
