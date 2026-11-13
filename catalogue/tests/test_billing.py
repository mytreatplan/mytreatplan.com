"""Billing companies and billing rules on the portal (epic-catalogue entry 9, mytp-core
0.23.0): the companies and rules stored by the snapshot apply (catalogue.applier), the
resolver (the rule of a country and a product category; only what has an available rule,
in the rule's currency, is offered once rules exist; the rule of a subscription's own
price), the region profile mismatch warning and `price_catalogue`. One test (or more) per
row of the plan's matrix. The admin site is the fake of integration/tests/test_config.py
(no network)."""
import copy
import logging
from decimal import Decimal

import pytest
from django.core.management import CommandError

from catalogue import billing, resolver
from catalogue.models import BillingCompany, BillingRule, CountryGroup
from catalogue.tests.test_catalogue import IBERIA, WORLD, group, product, pull, run
from catalogue.tests.test_subscriptions import full_subscription
from integration import config
from integration.tests.snapshot_rows import snapshot_writes
from integration.tests.test_config import (ADMIN, admin,  # noqa: F401
                                           interface_settings, snapshot, state)
from mytp_core import snapshots
from mytp_core.money import Money
from regions.profiles import get_profile

EU = 'a1111111-1111-4111-8111-111111111111'
UAE = 'a2222222-2222-4222-8222-222222222222'
TPS = 'b0000001-0000-4000-8000-000000000001'
TPS_USD = 'b0000002-0000-4000-8000-000000000002'
TPS_AED = 'b0000003-0000-4000-8000-000000000003'
VIRTUA = 'b0000004-0000-4000-8000-000000000004'
VIRTUA_AED = 'b0000005-0000-4000-8000-000000000005'
RETAINERS = 'b0000006-0000-4000-8000-000000000006'
ODB = 'b0000007-0000-4000-8000-000000000007'
SPAIN = 'c0000001-0000-4000-8000-000000000001'
IRELAND = 'c0000002-0000-4000-8000-000000000002'
EMIRATES = 'c0000003-0000-4000-8000-000000000003'
SUBSCRIPTIONS = {IBERIA: 'e0000001-0000-4000-8000-000000000001',
                 EU: 'e0000002-0000-4000-8000-000000000002',
                 WORLD: 'e0000003-0000-4000-8000-000000000003',
                 UAE: 'e0000004-0000-4000-8000-000000000004'}
MANUAL = 'e0000005-0000-4000-8000-000000000005'

EXEMPT = 'Exempt from VAT (art. 20 LIVA).'
REVERSE = 'Reverse charge (art. 196 Directive 2006/112/EC).'
EXPORT = 'Export of services: outside the scope of EU VAT.'
CATEGORIES = ('tps_branded', 'virtuaortho_one')

GROUPS = [group(IBERIA, 'Iberia', ['ES', 'PT']), group(EU, 'Rest of EU', ['IE', 'DE']),
          group(UAE, 'UAE', ['AE']), group(WORLD, 'Rest of world', [], catch_all=True)]
PRODUCTS = [
    product(TPS, 'TPS branded', 'tps_branded', '100.00', code='TPS-BR'),
    product(TPS_USD, 'TPS branded USD', 'tps_branded', '110.00', 'USD', code='TPS-BR-USD'),
    product(TPS_AED, 'TPS branded AED', 'tps_branded', '400.00', 'AED', code='TPS-BR-AED'),
    product(VIRTUA, 'VirtuaOrtho One', 'virtuaortho_one', '250.00', code='VO-ONE'),
    product(VIRTUA_AED, 'VirtuaOrtho One AED', 'virtuaortho_one', '1000.00', 'AED',
            code='VO-ONE-AED'),
    product(RETAINERS, 'Retainers', 'retainers', '50.00', code='RET-1'),
    product(ODB, 'ODB', 'odb', '80.00', code='ODB-1'),
]
ALL = [TPS, TPS_USD, TPS_AED, VIRTUA, VIRTUA_AED, RETAINERS, ODB]
COMPANIES = [
    {'id': SPAIN, 'code': 'ES', 'legal_name': 'MyTPDSO Spain S.L.',
     'address': 'Avda. Labradores, 1, Edificio COS, Planta 4, Oficina 6\n'
                'TRES CANTOS 28760 (MADRID)',
     'country': 'ES', 'time_zone': 'Europe/Madrid', 'vat_number': 'B93858371',
     'tax_number': 'B93858371', 'active': True},
    {'id': IRELAND, 'code': 'IE', 'legal_name': 'MyTPDSO Limited', 'address': 'Dublin',
     'country': 'IE', 'time_zone': 'Europe/Dublin',
     'company_number': 'Company number 815526 (Ireland)', 'active': True},
    {'id': EMIRATES, 'code': 'AE', 'legal_name': 'MyTPDSO L.L.C-FZ', 'address': 'Dubai',
     'country': 'AE', 'time_zone': 'Asia/Dubai',
     'company_number': 'License 2542361.01 (Dubai)', 'vat_number': 'VAT-100',
     'tax_number': 'TAX-100', 'active': True},
]


def rule(number, group_id, category, company, percent, gateways, currency='EUR',
         available=True, active=True, **texts):
    return {'id': f'd00000{number:02d}-0000-4000-8000-000000000001',
            'country_group': group_id, 'category': category, 'company': company,
            'vat_percent': percent, 'gateways': list(gateways), 'currency': currency,
            'available': available, 'active': active, **texts}


def rules():
    """The plan's rules, on the TPS branded and VirtuaOrtho One categories: Iberia → the
    Spanish company, exempt, Redsys then bank transfer; Rest of EU → the Irish company,
    23 % with a reverse-charge text, Revolut; Rest of world → the Irish company, rate 0
    with its export text; UAE → the UAE company, 5 %, no gateway, AED. And Iberia's
    `subscription` rule (21 %) and an unavailable ODB rule. Retainers have no rule."""
    found = []
    for index, category in enumerate(CATEGORIES):
        base = index * 10
        found += [
            rule(base + 1, IBERIA, category, SPAIN, '0.00', ['redsys', 'bank_transfer'],
                 exemption_text=EXEMPT),
            rule(base + 2, EU, category, IRELAND, '23.00', ['revolut'],
                 reverse_charge_text=REVERSE),
            rule(base + 3, WORLD, category, IRELAND, '0.00', ['revolut'],
                 exemption_text=EXPORT),
            rule(base + 4, UAE, category, EMIRATES, '5.00', [], 'AED'),
        ]
    return found + [
        rule(31, IBERIA, 'subscription', SPAIN, '21.00', ['redsys']),
        rule(32, IBERIA, 'odb', SPAIN, '21.00', ['redsys'], available=False),
    ]


def billed(rules_=None, companies=COMPANIES, products=PRODUCTS, manual=True):
    """A catalogue with a basic subscription per country group, each linked to every
    product, the companies and the rules (`rules_` None: the plan's; `companies` None and
    `rules_` False: none of either, as an admin site before mytp-core 0.23.0)."""
    section = {
        'country_groups': copy.deepcopy(GROUPS), 'products': copy.deepcopy(products),
        'subscriptions': [
            full_subscription(id_, 'basic', group_id, f'BAS-{index}', 'Basic', ALL,
                              **({'price': {'currency': 'EUR', 'monthly': '60.00'}}
                                 if group_id in (IBERIA, EU) else {}))
            for index, (group_id, id_) in enumerate(SUBSCRIPTIONS.items())],
    }
    if manual:
        section['manual_subscriptions'] = [full_subscription(
            MANUAL, 'premium', IBERIA, 'ORMCO', 'Ormco', [TPS, TPS_USD, RETAINERS],
            {'tps_branded': '20.00'})]
    if companies is not None:
        section['billing_companies'] = copy.deepcopy(companies)
    if rules_ is not False:
        section['billing_rules'] = copy.deepcopy(rules() if rules_ is None else rules_)
    return section


def offered(country):
    return [found.name for found, _price in resolver.offers(country, 'basic')]


def resolved(country, category):
    found = resolver.billing_rule(country, category)
    return (found.company.code, found.vat_percent, found.exemption_text,
            found.known_gateways)


# --- Stored as sent ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_the_companies_and_rules_are_stored(admin):  # noqa: F811
    assert pull(admin, 1, billed()) == ('applied', 1)
    spain = BillingCompany.objects.get(code='ES')
    assert (spain.legal_name, spain.country, spain.time_zone, spain.active) == (
        'MyTPDSO Spain S.L.', 'ES', 'Europe/Madrid', True)
    assert spain.address.endswith('TRES CANTOS 28760 (MADRID)') and '\n' in spain.address
    assert (spain.company_number, spain.vat_number, spain.tax_number) == (
        '', 'B93858371', 'B93858371')
    # The UAE company has its three identifiers.
    uae = resolver.billing_company('ae')
    assert uae.identifiers == [('company number', 'License 2542361.01 (Dubai)'),
                               ('VAT', 'VAT-100'), ('TAX', 'TAX-100')]
    assert BillingCompany.objects.count() == 3 and BillingRule.objects.count() == 10
    stored = BillingRule.objects.get(country_group_id=IBERIA, category='tps_branded')
    assert (str(stored.company_id), stored.vat_percent, stored.currency, stored.available,
            stored.active) == (SPAIN, Decimal('0.00'), 'EUR', True, True)
    assert stored.exemption_text == EXEMPT and stored.reverse_charge_text == ''
    # Read-only, as the rest of the catalogue.
    with pytest.raises(TypeError):
        stored.save()
    with pytest.raises(TypeError):
        spain.delete()
    # The next snapshot replaces them; one without the lists empties them.
    pull(admin, 2, billed(rules_=rules()[:1]))
    assert BillingRule.objects.count() == 1 and BillingCompany.objects.count() == 3
    pull(admin, 3, billed(rules_=False, companies=None))
    assert not BillingRule.objects.exists() and not BillingCompany.objects.exists()
    pull(admin, 4, billed())
    pull(admin, 5, None)
    assert not BillingRule.objects.exists() and not CountryGroup.objects.exists()


@pytest.mark.django_db
def test_the_applied_log_line_counts_the_companies_and_rules(admin, caplog):  # noqa: F811
    caplog.set_level(logging.INFO, logger='integration.config')
    pull(admin, 1, billed())
    assert ('4 country groups, 7 products, 5 subscriptions, 0 add-ons, 3 billing '
            'companies, 10 billing rules;') in caplog.text
    assert EXEMPT not in caplog.text and 'B93858371' not in caplog.text


# --- The rule of a country and a category -----------------------------------------------------

@pytest.mark.django_db
def test_each_country_resolves_to_its_company_vat_and_gateways(admin):  # noqa: F811
    pull(admin, 1, billed())
    for category in CATEGORIES:
        # Iberia: the Spanish company, exempt with its text, Redsys then bank transfer.
        for country in ('ES', 'PT', 'es '):
            assert resolved(country, category) == (
                'ES', Decimal('0.00'), EXEMPT, ['redsys', 'bank_transfer'])
        # Domestic and reverse charge: IE and DE both resolve to the Irish company at
        # 23 %, with the reverse-charge text available to entry 12.
        for country in ('IE', 'DE'):
            assert resolved(country, category) == ('IE', Decimal('23.00'), '', ['revolut'])
            assert resolver.billing_rule(country, category).reverse_charge_text == REVERSE
            assert not resolver.billing_rule(country, category).exempt
        # Export: the rest of the world, rate 0 with its export text.
        assert resolved('US', category) == ('IE', Decimal('0.00'), EXPORT, ['revolut'])
        assert resolver.billing_rule('US', category).exempt
        # The UAE: 5 %, an empty gateway list, AED.
        assert resolved('AE', category) == ('AE', Decimal('5.00'), '', [])
        assert resolver.billing_rule('AE', category).currency == 'AED'
    spain = resolver.billing_rule('ES', 'tps_branded').company
    assert (spain.legal_name, spain.vat_number) == ('MyTPDSO Spain S.L.', 'B93858371')
    assert resolver.billing_rule('ES', 'tps_branded').vat_text == f'VAT 0.00 %: {EXEMPT}'
    assert resolver.billing_rule('IE', 'tps_branded').vat_text == 'VAT 23.00 %'
    # "tps", the code of an admin site before entry 5, is tps_branded.
    assert resolver.billing_rule('ES', ' TPS ').category == 'tps_branded'
    # No rule for the pair, no group for the country, or a malformed country: None.
    assert resolver.billing_rule('ES', 'retainers') is None
    assert resolver.billing_rule('ESP', 'tps_branded') is None
    assert resolver.billing_rule(None, 'tps_branded') is None
    assert resolver.billing_company('XX') is None and resolver.billing_company('') is None


@pytest.mark.django_db
def test_the_gateways_keep_their_order_and_an_unknown_one_is_skipped(admin):  # noqa: F811
    changed = rules()
    changed[0]['gateways'] = ['bank_transfer', 'wio', 'revolut', 'redsys']
    pull(admin, 1, billed(rules_=changed))
    found = resolver.billing_rule('ES', 'tps_branded')
    assert found.gateways == ['bank_transfer', 'wio', 'revolut', 'redsys']      # as sent
    assert found.known_gateways == ['bank_transfer', 'revolut', 'redsys']


@pytest.mark.django_db
def test_a_deactivated_rule_is_not_the_rule(admin):  # noqa: F811
    changed = rules()
    changed[0]['active'] = False
    changed.append(rule(40, IBERIA, 'tps_branded', IRELAND, '21.00', ['revolut']))
    pull(admin, 1, billed(rules_=changed))
    assert resolved('ES', 'tps_branded') == ('IE', Decimal('21.00'), '', ['revolut'])
    assert BillingRule.objects.filter(country_group_id=IBERIA,
                                      category='tps_branded').count() == 2


# --- What is offered ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_only_what_has_an_available_rule_in_its_currency_is_offered(admin):  # noqa: F811
    pull(admin, 1, billed())
    # Currency: an ES doctor is offered the EUR products, not the USD or AED clones.
    # No rule: Retainers (no rule for Iberia) are not offered. Unavailable: nor the ODB.
    assert offered('ES') == offered('PT') == ['TPS branded', 'VirtuaOrtho One']
    assert offered('DE') == offered('IE') == ['TPS branded', 'VirtuaOrtho One']
    assert offered('US') == ['TPS branded', 'VirtuaOrtho One']
    # The UAE: sold in AED; an empty gateway list does not stop the offer.
    assert offered('AE') == ['TPS branded AED', 'VirtuaOrtho One AED']
    assert resolver.offers('ES', 'basic')[0][1] == Money('100.00', 'EUR')
    assert resolver.has_billing_rules()
    # The products stay readable, and a line is still priced (entry 12 applies the rule).
    retainers = resolver.product(RETAINERS)
    basic = resolver.subscription('ES', 'basic')
    assert retainers.active and resolver.price(retainers, basic) == Money('50.00', 'EUR')


@pytest.mark.django_db
def test_a_rule_marked_unavailable_or_missing_takes_its_products_away(admin):  # noqa: F811
    changed = rules()
    changed[0]['available'] = False                # Iberia × tps_branded
    del changed[1]                                 # Rest of EU × tps_branded
    pull(admin, 1, billed(rules_=changed))
    assert offered('ES') == ['VirtuaOrtho One']
    assert offered('DE') == ['VirtuaOrtho One']
    assert offered('US') == ['TPS branded', 'VirtuaOrtho One']
    assert resolver.billing_rule('ES', 'tps_branded').available is False
    assert resolver.billing_rule('DE', 'tps_branded') is None
    # Only deactivated rules: rules exist, so nothing is offered.
    pull(admin, 2, billed(rules_=[{**row, 'active': False} for row in rules()]))
    assert resolver.has_billing_rules() and offered('ES') == [] and offered('AE') == []


@pytest.mark.django_db
def test_a_rule_in_a_currency_or_category_this_release_does_not_know(admin):  # noqa: F811
    changed = rules()
    changed[0]['currency'] = 'JPY'
    changed[4]['category'] = 'a_new_category'      # Iberia × virtuaortho_one, renamed
    pull(admin, 1, billed(rules_=changed))
    assert BillingRule.objects.count() == 10       # stored as sent
    assert offered('ES') == []                     # no product is in JPY; no VO rule left
    assert offered('DE') == ['TPS branded', 'VirtuaOrtho One']


@pytest.mark.django_db
def test_a_manual_subscription_offers_under_its_groups_rules(admin):  # noqa: F811
    pull(admin, 1, billed())
    ormco = resolver.subscription_by_id(MANUAL)
    # Linked to the EUR and USD TPS and Retainers: Iberia's rules leave the EUR TPS.
    assert [(found.name, price) for found, price in resolver.offers_under(ormco)] == [
        ('TPS branded', Money('80.00', 'EUR'))]
    # For a doctor of another country group, that group's rules.
    assert [found.name for found, _p in resolver.offers_under(ormco, country='AE')] == []
    assert [found.name for found, _p in resolver.offers_under(ormco, country='US')] == [
        'TPS branded']
    assert resolver.offers_under(ormco, country='ESP') == []


# --- A subscription's own price ----------------------------------------------------------------

@pytest.mark.django_db
def test_the_rule_of_a_subscriptions_own_price(admin):  # noqa: F811
    pull(admin, 1, billed())
    iberia = resolver.subscription('ES', 'basic')
    found = resolver.subscription_rule(iberia)
    assert (found.category, found.company.code, found.vat_percent, found.known_gateways) == (
        'subscription', 'ES', Decimal('21.00'), ['redsys'])
    assert iberia.monthly_price == Money('60.00', 'EUR')
    assert resolver.subscription_rule(resolver.subscription_by_id(MANUAL)) == found
    # Rest of EU has no `subscription` rule: the price has no rule.
    assert resolver.subscription_rule(resolver.subscription('DE', 'basic')) is None


# --- Snapshots without rules, and the release before ---------------------------------------------

@pytest.mark.django_db
def test_an_old_snapshot_keeps_every_offer_in_every_currency(admin):  # noqa: F811
    everything = ['ODB', 'Retainers', 'TPS branded', 'TPS branded AED', 'TPS branded USD',
                  'VirtuaOrtho One', 'VirtuaOrtho One AED']
    # An admin site before this release: no lists at all.
    pull(admin, 1, billed(rules_=False, companies=None))
    assert not resolver.has_billing_rules()
    assert offered('ES') == offered('AE') == offered('US') == everything
    assert resolver.billing_rule('ES', 'tps_branded') is None
    assert resolver.subscription_rule(resolver.subscription('ES', 'basic')) is None
    # Companies typed, no rule yet (the admin site then sends no rules list), or an
    # empty list: the same.
    pull(admin, 2, billed(rules_=False))
    assert BillingCompany.objects.count() == 3 and offered('ES') == everything
    pull(admin, 3, billed(rules_=[]))
    assert offered('ES') == everything
    # The first rule switches to "only what has a rule".
    pull(admin, 4, billed(rules_=rules()[:1]))
    assert offered('ES') == ['TPS branded'] and offered('DE') == []


@pytest.mark.django_db
def test_the_first_pull_of_this_release_stores_the_rules_of_the_applied_version(
        admin, monkeypatch):  # noqa: F811
    # The release before applied version 1 without knowing companies and rules.
    monkeypatch.setattr(config, 'current_release', lambda: 'release-before')
    pull(admin, 1, billed())
    # What that release's apply left: no companies, no rules (written as an apply does).
    with snapshot_writes():
        BillingRule.objects.all().delete()
        BillingCompany.objects.all().delete()
    monkeypatch.setattr(config, 'current_release', lambda: 'this-release')
    assert pull(admin, 1, billed()) == ('applied', 1)
    assert BillingRule.objects.count() == 10 and offered('ES') == [
        'TPS branded', 'VirtuaOrtho One']


@pytest.mark.django_db
def test_the_release_before_can_still_replace_the_country_groups(admin):  # noqa: F811
    """A portal before this release applies a snapshot with companies and rules and
    ignores them: it replaces the country groups without knowing the rules table, which
    a database constraint from the rules to the groups would stop."""
    pull(admin, 1, billed())
    field = BillingRule._meta.get_field('country_group')
    assert field.db_constraint is False
    # What the release before does: a raw replace of the groups, the rules untouched.
    with snapshot_writes():
        CountryGroup.objects.all()._raw_delete(CountryGroup.objects.db)
    assert BillingRule.objects.count() == 10 and not CountryGroup.objects.exists()
    assert resolver.billing_rule('ES', 'tps_branded') is None       # no group, no rule
    assert pull(admin, 2, billed()) == ('applied', 2)
    assert offered('ES') == ['TPS branded', 'VirtuaOrtho One']


@pytest.mark.django_db
@pytest.mark.parametrize('change', [
    lambda section: section['billing_rules'].append(
        rule(50, IBERIA, 'tps_branded', IRELAND, '21.00', [])),         # a second active rule
    lambda section: section['billing_rules'][0].pop('exemption_text'),  # rate 0, no text
    lambda section: section['billing_rules'][0].update(company=TPS),    # company not listed
    lambda section: section['billing_rules'][0].update(country_group=TPS),
    lambda section: section['billing_rules'][0].update(gateways=['redsys', 'redsys']),
    lambda section: section['billing_companies'][1].update(code='ES'),  # a code twice
], ids=['second-active-rule', 'rate-0-without-text', 'company-not-listed',
        'group-not-listed', 'gateway-twice', 'company-code-twice'])
def test_an_invalid_section_is_refused_and_the_applied_one_kept(admin, change):  # noqa: F811
    import jwt
    pull(admin, 1, billed())
    section = billed()
    change(section)

    def invalid(answer):
        bad = {**snapshot(version=2), 'catalogue': section}
        claims = jwt.decode(answer['signature'], options={'verify_signature': False})
        claims['sha'] = snapshots.snapshot_sha(bad)
        answer['snapshot'] = bad
        answer['signature'] = ADMIN.sign(claims)
    admin.tamper = invalid
    with pytest.raises(config.PullFailed) as error:
        pull(admin, 2, billed())
    assert error.value.code == 'invalid_body'
    assert state().version == 1 and BillingRule.objects.count() == 10
    assert offered('ES') == ['TPS branded', 'VirtuaOrtho One']


# --- The region profile's company ----------------------------------------------------------------

@pytest.mark.django_db
def test_no_warning_when_the_regions_company_is_the_profiles(admin, caplog):  # noqa: F811
    caplog.set_level(logging.WARNING, logger='catalogue.applier')
    pull(admin, 1, billed())
    assert billing.profile_mismatch() == '' and 'differs' not in caplog.text
    # The bare number matches the profile's "Company number 815526 (Ireland)" too.
    companies = copy.deepcopy(COMPANIES)
    companies[1].update(company_number='815526', legal_name='mytpdso  limited')
    pull(admin, 2, billed(companies=companies))
    assert billing.profile_mismatch() == '' and 'differs' not in caplog.text
    # Without companies there is nothing to compare.
    pull(admin, 3, billed(rules_=False, companies=None))
    assert billing.profile_mismatch() == ''
    assert 'warning' not in run('--country', 'ES', '--tier', 'basic')


@pytest.mark.django_db
@pytest.mark.parametrize('changes, named', [
    ({'legal_name': 'MyTPDSO Ireland Limited'}, 'legal name "MyTPDSO Ireland Limited"'),
    ({'company_number': '999999'}, 'company number or license "999999"'),
    ({'company_number': '81552'}, 'company number or license "81552"'),   # a part of it
    ({'company_number': 'Ireland'}, 'company number or license "Ireland"'),
    ({'company_number': None, 'vat_number': 'IE1234567T'}, 'company number or license ""'),
])
def test_a_profile_mismatch_is_a_warning_and_nothing_is_refused(
        admin, caplog, changes, named):  # noqa: F811
    caplog.set_level(logging.WARNING, logger='catalogue.applier')
    companies = copy.deepcopy(COMPANIES)
    companies[1].update(changes)
    companies[1] = {key: value for key, value in companies[1].items() if value is not None}
    assert pull(admin, 1, billed(companies=companies)) == ('applied', 1)
    assert 'the billing company IE differs from the eu region profile' in caplog.text
    assert named in caplog.text and named in billing.profile_mismatch()
    # Nothing refused: the rules are used as sent.
    assert resolved('DE', 'tps_branded')[0] == 'IE' and offered('DE') == [
        'TPS branded', 'VirtuaOrtho One']
    # price_catalogue says so, first.
    out = run('--country', 'DE', '--tier', 'basic')
    assert out.startswith('warning: the billing company IE differs from the eu region '
                          'profile: ')
    assert run('--country', 'DE', '--category', 'tps_branded').startswith('warning: ')


@pytest.mark.django_db
def test_the_uae_region_compares_its_own_company(admin, settings):  # noqa: F811
    settings.REGION_PROFILE = get_profile('ae')
    pull(admin, 1, billed())
    assert billing.profile_mismatch() == ''
    companies = copy.deepcopy(COMPANIES)
    companies[2]['legal_name'] = 'Another L.L.C'
    companies[1]['legal_name'] = 'Not compared here'
    pull(admin, 2, billed(companies=companies))
    assert billing.profile_mismatch().startswith(
        'the billing company AE differs from the ae region profile: legal name')


# --- price_catalogue ------------------------------------------------------------------------------

@pytest.mark.django_db
def test_price_catalogue_prints_each_offers_company_vat_and_gateways(admin):  # noqa: F811
    pull(admin, 1, billed())
    assert run('--country', 'ES', '--tier', 'basic') == (
        'ES basic (Iberia): 2 offer(s)\n'
        '  subscription: BAS-0  Basic  60.00 EUR a month\n'
        '  subscription billing: ES  MyTPDSO Spain S.L.  VAT 21.00 %  gateways: redsys  '
        'EUR\n'
        '  TPS-BR  TPS branded  tps_branded  100.00 EUR\n'
        f'      billing: ES  MyTPDSO Spain S.L.  VAT 0.00 %: {EXEMPT}  gateways: redsys, '
        'bank_transfer  EUR\n'
        '  VO-ONE  VirtuaOrtho One  virtuaortho_one  250.00 EUR\n'
        f'      billing: ES  MyTPDSO Spain S.L.  VAT 0.00 %: {EXEMPT}  gateways: redsys, '
        'bank_transfer  EUR\n')
    assert run('--country', 'PT', '--tier', 'basic').count('ES  MyTPDSO Spain S.L.') == 3
    for country in ('IE', 'DE'):
        out = run('--country', country, '--tier', 'basic')
        assert out.count('      billing: IE  MyTPDSO Limited  VAT 23.00 %  gateways: '
                         'revolut  EUR\n') == 2
        assert '  subscription billing: no billing rule\n' in out
    out = run('--country', 'US', '--tier', 'basic')
    assert out.count(f'      billing: IE  MyTPDSO Limited  VAT 0.00 %: {EXPORT}  gateways: '
                     'revolut  EUR\n') == 2
    out = run('--country', 'AE', '--tier', 'basic')
    assert out.startswith('AE basic (UAE): 2 offer(s)\n')
    assert out.count('      billing: AE  MyTPDSO L.L.C-FZ  VAT 5.00 %  gateways: none  '
                     'AED\n') == 2
    assert 'TPS-BR-AED' in out and 'TPS-BR-USD' not in out and 'RET-1' not in out
    # A subscription read by its code, and one line of a product.
    out = run('--subscription', 'ORMCO')
    assert 'ORMCO premium (Iberia), manual: 1 offer(s)\n' in out and 'bank_transfer' in out
    out = run('--product', TPS, '--country', 'ES', '--tier', 'basic')
    assert (f'  billing: ES  MyTPDSO Spain S.L.  VAT 0.00 %: {EXEMPT}  gateways: redsys, '
            'bank_transfer  EUR\n  line price: 100.00 EUR\n') in out


@pytest.mark.django_db
def test_price_catalogue_prints_one_rule(admin):  # noqa: F811
    pull(admin, 1, billed())
    assert run('--country', 'es', '--category', 'tps_branded') == (
        'ES tps_branded (Iberia): billing rule\n'
        '  company: ES  MyTPDSO Spain S.L.\n'
        '  address: Avda. Labradores, 1, Edificio COS, Planta 4, Oficina 6 / TRES CANTOS '
        '28760 (MADRID)  (ES, Europe/Madrid)\n'
        '  VAT: B93858371\n'
        '  TAX: B93858371\n'
        '  VAT: 0.00 %\n'
        f'  exemption text: {EXEMPT}\n'
        '  gateways: redsys, bank_transfer\n'
        '  currency: EUR\n'
        '  available: yes\n')
    out = run('--country', 'DE', '--category', 'virtuaortho_one')
    assert 'DE virtuaortho_one (Rest of EU): billing rule\n' in out
    assert ('  VAT: 23.00 %\n' in out and f'  reverse-charge text: {REVERSE}\n' in out
            and 'exemption text' not in out)
    out = run('--country', 'AE', '--category', 'tps_branded')
    assert ('  company number: License 2542361.01 (Dubai)\n  VAT: VAT-100\n  TAX: TAX-100\n'
            in out and '  gateways: none\n  currency: AED\n' in out)
    assert '  available: no: not offered\n' in run('--country', 'ES', '--category', 'odb')
    assert run('--country', 'ES', '--category', 'retainers') == (
        'no billing rule for ES retainers (Iberia)\n')
    with pytest.raises(CommandError, match='--category goes with --country alone'):
        run('--country', 'ES', '--tier', 'basic', '--category', 'odb')
    with pytest.raises(CommandError):
        run('--category', 'odb')
    with pytest.raises(CommandError):
        run('--country', 'ES', '--category', 'uncategorised')


@pytest.mark.django_db
def test_price_catalogue_without_rules_is_as_before(admin):  # noqa: F811
    pull(admin, 1, billed(rules_=False, companies=None))
    out = run('--country', 'ES', '--tier', 'basic')
    assert out.startswith('ES basic (Iberia): 7 offer(s)\n') and 'billing' not in out
    assert run('--country', 'ES', '--category', 'tps_branded') == (
        'no billing rule for ES tps_branded (Iberia)\n'
        '  the catalogue has no billing rules: every linked product is offered, in every '
        'currency\n')
