"""The catalogue per region on the portal (epic-catalogue entry 16, mytp-core 0.25.0): the
applier skips a country group, a billing company or a product of another region and what
hangs from a skipped group, with one warning giving the counts; a record without a region
(an admin site before 0.25.0) is this region's, as before; a doctor of a country this
region does not serve (another region's, or banned) has no group, no subscription and no
offers; each region's catch-all covers only its own countries; an empty rules list offers
nothing. The admin site is the fake of integration/tests/snapshot_rows.py (no network)."""
import logging

import pytest

from catalogue import resolver
from catalogue.models import (BillingCompany, BillingRule, CatalogueState, CountryGroup,
                              Product, Subscription)
from integration.tests.snapshot_rows import full_subscription, group, product, pull
from mytp_core.contracts import snapshot_for_region
from regions.profiles import get_profile

IBERIA = 'a0000001-0000-4000-8000-000000000001'
WORLD = 'a0000002-0000-4000-8000-000000000002'
UAE = 'a0000003-0000-4000-8000-000000000003'
MIDDLE_EAST = 'a0000004-0000-4000-8000-000000000004'
EUR_TPS = 'b0000001-0000-4000-8000-000000000001'
USD_TPS = 'b0000002-0000-4000-8000-000000000002'
AED_TPS = 'b0000003-0000-4000-8000-000000000003'
SPAIN = 'c0000001-0000-4000-8000-000000000001'
IRELAND = 'c0000002-0000-4000-8000-000000000002'
EMIRATES = 'c0000003-0000-4000-8000-000000000003'
SUBS = {IBERIA: 'e0000001-0000-4000-8000-000000000001',
        WORLD: 'e0000002-0000-4000-8000-000000000002',
        UAE: 'e0000003-0000-4000-8000-000000000003',
        MIDDLE_EAST: 'e0000004-0000-4000-8000-000000000004'}
MANUAL_AE = 'e0000005-0000-4000-8000-000000000005'
RULES = {IBERIA: 'd0000001-0000-4000-8000-000000000001',
         WORLD: 'd0000002-0000-4000-8000-000000000002',
         UAE: 'd0000003-0000-4000-8000-000000000003',
         MIDDLE_EAST: 'd0000004-0000-4000-8000-000000000004'}
REGION_OF = {IBERIA: 'eu', WORLD: 'eu', UAE: 'ae', MIDDLE_EAST: 'ae'}
CURRENCY_OF = {IBERIA: 'EUR', WORLD: 'USD', UAE: 'AED', MIDDLE_EAST: 'USD'}
PRODUCT_OF = {'EUR': EUR_TPS, 'USD': USD_TPS, 'AED': AED_TPS}
COMPANY_OF = {IBERIA: SPAIN, WORLD: IRELAND, UAE: EMIRATES, MIDDLE_EAST: EMIRATES}


def company(id_, code, country, region):
    return {'id': id_, 'code': code, 'legal_name': f'MyTPDSO {code}', 'address': 'Street 1',
            'country': country, 'time_zone': 'Europe/Madrid', 'vat_number': f'VAT-{code}',
            'active': True, 'region': region}


def two_regions(regions=True):
    """The whole stored body's catalogue, both regions' records: Iberia and Rest of world
    (eu), UAE and Rest of Middle East (ae), each with a basic subscription and a TPS
    branded rule; products in EUR and USD (eu) and AED (ae); an ae manual subscription.
    `regions` False: the records carry no region (an admin site before 0.25.0, with ae's
    records left out: one catch-all)."""
    groups = [group(IBERIA, 'Iberia', ['ES', 'PT']),
              group(WORLD, 'Rest of world', [], catch_all=True),
              group(UAE, 'UAE', ['AE']),
              group(MIDDLE_EAST, 'Rest of Middle East', [], catch_all=True)]
    products = [product(EUR_TPS, 'TPS EUR', 'tps_branded', '100.00', 'EUR', code='TPS-EUR'),
                product(USD_TPS, 'TPS USD', 'tps_branded', '110.00', 'USD', code='TPS-USD'),
                product(AED_TPS, 'TPS AED', 'tps_branded', '400.00', 'AED', code='TPS-AED')]
    companies = [company(SPAIN, 'ES', 'ES', 'eu'), company(IRELAND, 'IE', 'IE', 'eu'),
                 company(EMIRATES, 'AE', 'AE', 'ae')]
    section = {
        'country_groups': groups, 'products': products,
        'subscriptions': [full_subscription(SUBS[g], 'basic', g, f'BAS-{n}', 'Basic',
                                            [PRODUCT_OF[CURRENCY_OF[g]]])
                          for n, g in enumerate(SUBS)],
        'manual_subscriptions': [full_subscription(MANUAL_AE, 'premium', UAE, 'VIP-AE',
                                                   'VIP', [AED_TPS])],
        'billing_companies': companies,
        'billing_rules': [{'id': RULES[g], 'country_group': g, 'category': 'tps_branded',
                           'company': COMPANY_OF[g], 'vat_percent': '5.00', 'gateways': [],
                           'currency': CURRENCY_OF[g], 'available': True, 'active': True}
                          for g in RULES]}
    for row in groups:
        row['region'] = REGION_OF[row['id']]
    for row in products:
        row['region'] = 'ae' if row['net_price']['currency'] == 'AED' else 'eu'
    if not regions:
        section = snapshot_for_region({'catalogue': section}, 'eu')['catalogue']
        for name in ('country_groups', 'products', 'billing_companies'):
            for row in section[name]:
                del row['region']
    return section


def ids(model):
    return {str(pk) for pk in model.objects.values_list('pk', flat=True)}


def offered(country, tier='basic'):
    return [found.code for found, _price in resolver.offers(country, tier)]


# --- The served bodies (Acceptance: dev and dev-ae pull) ----------------------------------

@pytest.mark.django_db
def test_dev_applies_eus_body_and_holds_no_ae_record(admin, caplog):
    caplog.set_level(logging.WARNING, logger='catalogue.applier')
    assert pull(admin, 1, snapshot_for_region({'catalogue': two_regions()}, 'eu')[
        'catalogue']) == ('applied', 1)
    assert ids(CountryGroup) == {IBERIA, WORLD}
    assert ids(Product) == {EUR_TPS, USD_TPS} and ids(BillingCompany) == {SPAIN, IRELAND}
    assert ids(Subscription) == {SUBS[IBERIA], SUBS[WORLD]}
    assert ids(BillingRule) == {RULES[IBERIA], RULES[WORLD]}
    assert 'another region' not in caplog.text     # nothing to skip


@pytest.mark.django_db
def test_dev_ae_applies_aes_body_and_holds_no_eu_record(admin, ae_portal):
    pull(admin, 1, snapshot_for_region({'catalogue': two_regions()}, 'ae')['catalogue'])
    assert ids(CountryGroup) == {UAE, MIDDLE_EAST}
    assert ids(Product) == {AED_TPS} and ids(BillingCompany) == {EMIRATES}
    assert ids(Subscription) == {SUBS[UAE], SUBS[MIDDLE_EAST], MANUAL_AE}
    assert ids(BillingRule) == {RULES[UAE], RULES[MIDDLE_EAST]}


# --- The portal skip ------------------------------------------------------------------------

@pytest.mark.django_db
def test_a_record_of_another_region_is_skipped_with_what_hangs_from_it(
        admin, caplog):
    caplog.set_level(logging.WARNING, logger='catalogue.applier')
    section = two_regions()
    # A subscription of eu linking an ae product (the admin site refuses it; skipped).
    section['subscriptions'][0]['products'].append(AED_TPS)
    assert pull(admin, 1, section) == ('applied', 1)
    assert ids(CountryGroup) == {IBERIA, WORLD}
    assert ids(Product) == {EUR_TPS, USD_TPS} and ids(BillingCompany) == {SPAIN, IRELAND}
    assert ids(Subscription) == {SUBS[IBERIA], SUBS[WORLD]}
    assert ids(BillingRule) == {RULES[IBERIA], RULES[WORLD]}
    assert Subscription.objects.get(pk=SUBS[IBERIA]).products == [EUR_TPS]
    assert ('config snapshot: records of another region skipped: 2 country groups, '
            '1 products, 1 billing companies, 3 subscriptions, 2 billing rules') in caplog.text
    assert 'UAE' not in caplog.text and 'TPS-AED' not in caplog.text   # counts only
    # Still a working catalogue: ES and US are offered their own products.
    assert offered('ES') == ['TPS-EUR'] and offered('US') == ['TPS-USD']


@pytest.mark.django_db
def test_a_rule_naming_a_company_of_another_region_is_skipped(admin, caplog):
    caplog.set_level(logging.WARNING, logger='catalogue.applier')
    section = two_regions()
    section['billing_rules'][0]['company'] = EMIRATES     # Iberia × the AE company
    pull(admin, 1, section)
    assert ids(BillingRule) == {RULES[WORLD]}
    assert offered('ES') == []                    # no rule for the pair: not offered


@pytest.mark.django_db
def test_an_old_admin_body_without_regions_is_this_regions(admin, caplog):
    caplog.set_level(logging.WARNING, logger='catalogue.applier')
    pull(admin, 1, two_regions(regions=False))
    assert ids(CountryGroup) == {IBERIA, WORLD} and ids(Product) == {EUR_TPS, USD_TPS}
    assert 'another region' not in caplog.text
    assert offered('ES') == ['TPS-EUR'] and offered('US') == ['TPS-USD']


# --- Who is offered what (Acceptance: US on dev, SA on dev-ae, SA on dev) ------------------

@pytest.mark.django_db
def test_each_doctor_falls_into_their_own_regions_catch_all(admin, settings):
    pull(admin, 1, two_regions())
    world = resolver.country_group('US')
    assert str(world.pk) == WORLD and offered('US') == ['TPS-USD']
    # Saudi Arabia is ae's: no group, no subscription, no offers on dev.
    assert resolver.country_group('SA') is None
    assert resolver.subscription('SA', 'basic') is None and offered('SA') == []
    assert resolver.billing_rule('SA', 'tps_branded') is None
    # On dev-ae, Rest of Middle East catches it; the US is now another region's.
    settings.REGION, settings.REGION_PROFILE = 'ae', get_profile('ae')
    pull(admin, 2, two_regions())
    assert str(resolver.country_group('SA').pk) == MIDDLE_EAST
    # Its subscription links eu's USD product only (left out for ae): nothing offered yet.
    assert offered('SA') == []
    assert str(resolver.subscription('SA', 'basic').pk) == SUBS[MIDDLE_EAST]
    assert str(resolver.billing_rule('SA', 'tps_branded').pk) == RULES[MIDDLE_EAST]
    assert str(resolver.country_group('AE').pk) == UAE and offered('AE') == ['TPS-AED']
    assert resolver.country_group('US') is None and offered('US') == []


@pytest.mark.django_db
def test_a_doctor_of_a_banned_country_is_offered_nothing(admin):
    pull(admin, 1, two_regions())
    assert resolver.country_group('RU') is None      # banned in the country map
    assert offered('RU') == [] and resolver.billing_rule('RU', 'tps_branded') is None


# --- No rules left for this region ---------------------------------------------------------

@pytest.mark.django_db
def test_an_empty_rules_list_offers_nothing(admin, ae_portal):
    section = two_regions()
    section['billing_rules'] = [rule for rule in section['billing_rules']
                                if REGION_OF[rule['country_group']] == 'eu']
    served = snapshot_for_region({'catalogue': section}, 'ae')['catalogue']
    assert served['billing_rules'] == []
    pull(admin, 1, served)
    assert not BillingRule.objects.exists() and resolver.has_billing_rules()
    assert CatalogueState.objects.get().billing_rules_listed is True
    assert offered('AE') == [] and offered('SA') == []
    # No list at all (no rule anywhere): every linked product, as before rules.
    del served['billing_rules']
    pull(admin, 2, served)
    assert not resolver.has_billing_rules() and offered('AE') == ['TPS-AED']
    assert CatalogueState.objects.get().billing_rules_listed is False
    # Without a catalogue section: no state row, no rules.
    pull(admin, 3, None)
    assert not CatalogueState.objects.exists() and not resolver.has_billing_rules()



@pytest.mark.django_db
def test_a_body_mixing_records_with_and_without_a_region(admin, caplog):
    """Iberia without a region (as an admin site before 0.25.0 wrote it), the rest with
    theirs: on the eu portal Iberia is kept, the ae records are skipped."""
    caplog.set_level(logging.WARNING, logger='catalogue.applier')
    section = two_regions()
    del next(row for row in section['country_groups'] if row['id'] == IBERIA)['region']
    pull(admin, 1, section)
    assert ids(CountryGroup) == {IBERIA, WORLD}
    assert ids(Product) == {EUR_TPS, USD_TPS} and ids(BillingCompany) == {SPAIN, IRELAND}
    assert ids(Subscription) == {SUBS[IBERIA], SUBS[WORLD]}
    assert 'records of another region skipped: 2 country groups' in caplog.text
    assert offered('ES') == ['TPS-EUR']


@pytest.mark.django_db
def test_price_catalogue_names_rest_of_middle_east_on_dev_ae(admin, ae_portal):
    """The staging check of deploy/README.md: Rest of Middle East is created empty (no
    subscription, no rule), while the UAE has its rule."""
    from catalogue.tests.test_catalogue import run
    section = two_regions()
    section['subscriptions'] = [row for row in section['subscriptions']
                                if row['country_group'] != MIDDLE_EAST]
    section['billing_rules'] = [row for row in section['billing_rules']
                                if row['country_group'] != MIDDLE_EAST]
    pull(admin, 1, snapshot_for_region({'catalogue': section}, 'ae')['catalogue'])
    # (The fixture's AE company is not the profile's: a warning line comes first.)
    assert run('--country', 'SA', '--category', 'tps_branded').endswith(
        '\nno billing rule for SA tps_branded (Rest of Middle East)\n')
    assert run('--country', 'SA', '--tier', 'basic').endswith('\nno offers for SA basic\n')
