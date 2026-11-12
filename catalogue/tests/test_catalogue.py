"""The catalogue from the configuration snapshot (epic-catalogue entry 2): its tables,
filled only by the snapshot apply (integration.config with catalogue.applier), the
resolver that prices (catalogue.resolver) and `price_catalogue`; and products in full
(entry 5): texts per language with the English fallback, code, validity dates in Madrid
time, the ten categories and the two TPS discounts. The admin site is the fake of
integration/tests/test_config.py (fixed, non-secret test seeds; no network).

The subscriptions here are sent as an admin site before epic-catalogue entry 8 sends them
(`subscription`: no linked products, no discounts map), so every active product is offered
under each of them and the two TPS discounts apply: what these tests pin is what such a
snapshot still gets. Subscriptions with their linked products, discounts per category,
own price and call extras, and the manual ones, are in test_subscriptions.py."""
import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command

from catalogue import applier, resolver
from catalogue.models import CountryGroup, Product, Subscription
from integration import config
from integration.models import AdminDirectoryEntry
from integration.tests.test_config import (ADA, ADMIN, admin,  # noqa: F401
                                           interface_settings, snapshot, state)
from mytp_core import snapshots
from mytp_core.money import Money

IBERIA = '11111111-1111-4111-8111-111111111111'
REST_OF_EU = '12121212-1212-4212-8212-121212121212'
WORLD = '22222222-2222-4222-8222-222222222222'
TPS = '33333333-3333-4333-8333-333333333333'
OTHER = '44444444-4444-4444-8444-444444444444'
BASIC = '55555555-5555-4555-8555-555555555555'
WORLD_BASIC = '66666666-6666-4666-8666-666666666666'


def group(id_, name, countries, catch_all=False, active=True):
    return {'id': id_, 'name': name, 'countries': countries, 'catch_all': catch_all,
            'active': active}


def product(id_, name, category, amount, currency='EUR', active=True, **more):
    """A product as the admin site sends it; `more`: the optional fields of mytp-core
    0.20.0 (texts, code, valid_from, valid_to)."""
    return {'id': id_, 'name': name, 'category': category,
            'net_price': {'amount': amount, 'currency': currency}, 'active': active, **more}


def subscription(id_, tier, group_id, percent, active=True, in_house=None):
    """A subscription as an admin site before mytp-core 0.22.0 sends it (no linked
    products: every product is offered under it; no discounts map: its two TPS discounts
    apply); without `in_house`, as one before 0.20.0 did (no in-house discount)."""
    data = {'id': id_, 'tier': tier, 'country_group': group_id,
            'tps_discount_percent': percent, 'active': active}
    if in_house is not None:
        data['tps_in_house_discount_percent'] = in_house
    return data


def catalogue(products=None, subscriptions=None, groups=None):
    """Iberia (ES, PT) with a basic subscription at 10 % (without linked products: it
    offers every product), a TPS product at 100.00 EUR; Rest of EU and the catch-all Rest
    of world without a subscription."""
    return {
        'country_groups': groups if groups is not None else [
            group(IBERIA, 'Iberia', ['ES', 'PT']),
            group(REST_OF_EU, 'Rest of EU', ['FR', 'DE']),
            group(WORLD, 'Rest of world', [], catch_all=True)],
        'products': products if products is not None else [
            product(TPS, 'TPS', 'tps_branded', '100.00')],
        'subscriptions': subscriptions if subscriptions is not None else [
            subscription(BASIC, 'basic', IBERIA, '10.00')],
    }


def pull(admin, version, section, **changes):  # noqa: F811
    admin.snapshot = snapshot(version=version, **changes)
    if section is not None:
        admin.snapshot['catalogue'] = section
    return config.pull()


def priced(country, tier='basic'):
    return [(p.name, price) for p, price in resolver.offers(country, tier)]


# --- The I/O matrix ---------------------------------------------------------------------

@pytest.mark.django_db
def test_an_es_doctor_gets_the_tps_product_at_the_discounted_price(admin):  # noqa: F811
    assert pull(admin, 1, catalogue()) == ('applied', 1)
    assert priced('ES') == [('TPS', Money('90.00', 'EUR'))]
    assert priced('es') == priced('PT') == [('TPS', Money('90.00', 'EUR'))]
    assert priced(' es ', ' Basic ') == [('TPS', Money('90.00', 'EUR'))]


@pytest.mark.django_db
def test_a_us_doctor_gets_nothing(admin):  # noqa: F811
    pull(admin, 1, catalogue())
    assert priced('US') == []
    # The catch-all group applies once it has a subscription of the tier.
    pull(admin, 2, catalogue(subscriptions=[
        subscription(BASIC, 'basic', IBERIA, '10.00'),
        subscription(WORLD_BASIC, 'basic', WORLD, '0.00')]))
    assert priced('US') == [('TPS', Money('100.00', 'EUR'))]
    assert priced('FR') == []  # Rest of EU: no subscription, never the catch-all's
    assert priced('ES', 'premium') == [] and priced('', 'basic') == []
    assert priced('ESP') == [] and priced(None) == []


@pytest.mark.django_db
def test_a_country_of_a_deactivated_group_falls_to_rest_of_world(admin):  # noqa: F811
    # Epic-catalogue entry 3: the admin site deactivates Iberia.
    subscriptions = [subscription(BASIC, 'basic', IBERIA, '10.00'),
                     subscription(WORLD_BASIC, 'basic', WORLD, '0.00')]
    pull(admin, 1, catalogue(subscriptions=subscriptions))
    assert resolver.country_group('ES').name == 'Iberia'
    assert priced('ES') == [('TPS', Money('90.00', 'EUR'))]
    pull(admin, 2, catalogue(subscriptions=subscriptions, groups=[
        group(IBERIA, 'Iberia', ['ES', 'PT'], active=False),
        group(REST_OF_EU, 'Rest of EU', ['FR', 'DE']),
        group(WORLD, 'Rest of world', [], catch_all=True)]))
    assert resolver.country_group('ES').name == 'Rest of world'
    assert priced('ES') == priced('US') == [('TPS', Money('100.00', 'EUR'))]
    assert resolver.country_group('FR').name == 'Rest of EU'


@pytest.mark.django_db
def test_a_country_added_to_a_group_is_priced_as_that_group(admin):  # noqa: F811
    # Epic-catalogue entry 3: the admin site adds FR to Iberia (and takes it from Rest of
    # EU, as the one-active-group rule requires).
    pull(admin, 1, catalogue())
    assert priced('FR') == []
    pull(admin, 2, catalogue(groups=[
        group(IBERIA, 'Iberia', ['ES', 'FR', 'PT']),
        group(REST_OF_EU, 'Rest of EU', ['DE']),
        group(WORLD, 'Rest of world', [], catch_all=True)]))
    assert resolver.country_group('FR').name == 'Iberia'
    assert priced('FR') == [('TPS', Money('90.00', 'EUR'))]


@pytest.mark.django_db
def test_a_non_tps_product_is_listed_at_its_net_price(admin):  # noqa: F811
    pull(admin, 1, catalogue(products=[product(TPS, 'TPS', 'tps_branded', '100.00'),
                                       product(OTHER, 'Retainers', 'retainers', '50.00')]))
    assert priced('ES') == [('Retainers', Money('50.00', 'EUR')),
                            ('TPS', Money('90.00', 'EUR'))]


@pytest.mark.django_db
@pytest.mark.parametrize('change', ['product', 'subscription'])
def test_a_deactivated_row_is_gone_after_the_next_snapshot(admin, change):  # noqa: F811
    pull(admin, 1, catalogue())
    assert priced('ES')
    if change == 'product':
        section = catalogue(products=[product(TPS, 'TPS', 'tps_branded', '100.00', active=False)])
    else:
        section = catalogue(subscriptions=[subscription(BASIC, 'basic', IBERIA, '10.00',
                                                        active=False)])
    assert pull(admin, 2, section) == ('applied', 2)
    assert priced('ES') == []
    # Inactive rows are kept (the snapshot lists them).
    assert Product.objects.count() == 1 and Subscription.objects.count() == 1


@pytest.mark.django_db
def test_rounding_is_half_up_to_cents(admin):  # noqa: F811
    pull(admin, 1, catalogue(products=[product(TPS, 'TPS', 'tps_branded', '33.33')],
                             subscriptions=[subscription(BASIC, 'basic', IBERIA, '15.00')]))
    assert priced('ES') == [('TPS', Money('28.33', 'EUR'))]  # 28.3305
    pull(admin, 2, catalogue(products=[product(TPS, 'TPS', 'tps_branded', '0.10')],
                             subscriptions=[subscription(BASIC, 'basic', IBERIA, '50.00')]))
    assert priced('ES') == [('TPS', Money('0.05', 'EUR'))]
    pull(admin, 3, catalogue(products=[product(TPS, 'TPS', 'tps_branded', '0.15')],
                             subscriptions=[subscription(BASIC, 'basic', IBERIA, '50.00')]))
    assert priced('ES') == [('TPS', Money('0.08', 'EUR'))]  # 0.075 half up
    pull(admin, 4, catalogue(products=[product(TPS, 'TPS', 'tps_branded', '80.00', 'AED')],
                             subscriptions=[subscription(BASIC, 'basic', IBERIA, '100.00')]))
    assert priced('ES') == [('TPS', Money('0.00', 'AED'))]


@pytest.mark.django_db
@pytest.mark.parametrize('section', [
    catalogue(subscriptions=[subscription(BASIC, 'basic', IBERIA, '120.00')]),
    catalogue(products=[product(TPS, 'TPS', 'tps_branded', '-1.00')]),
    catalogue(subscriptions=[subscription(BASIC, 'basic', IBERIA, '10.00'),
                             subscription(WORLD_BASIC, 'basic', IBERIA, '5.00')]),
    catalogue(products=[{**product(TPS, 'TPS', 'tps_branded', '1.00'), 'net_price': 1.0}]),
    catalogue(products=[{**product(TPS, 'TPS', 'tps_branded', '1.00'),
                         'net_price': {'amount': 1.0, 'currency': 'EUR'}}]),
    # Entry 5's optional fields, when sent, are checked too.
    catalogue(products=[product(TPS, 'TPS', 'tps_branded', '1.00',
                                texts={'es': {'name': 'TPS'}})]),
    catalogue(products=[product(TPS, 'TPS', 'tps_branded', '1.00', code='tps 1')]),
    catalogue(products=[product(TPS, 'TPS', 'tps_branded', '1.00',
                                valid_from='2026-07-01', valid_to='2026-06-30')]),
    catalogue(subscriptions=[subscription(BASIC, 'basic', IBERIA, '10.00',
                                          in_house='100.01')]),
], ids=['discount-120', 'negative-price', 'second-active-subscription', 'float',
        'float-amount', 'texts-without-english', 'code-malformed', 'validity-backwards',
        'in-house-above-100'])
def test_an_invalid_catalogue_is_refused_and_the_applied_one_kept(admin, section):  # noqa: F811
    import jwt
    pull(admin, 1, catalogue())

    def invalid(answer):
        # Signed correctly over a snapshot whose catalogue breaks the contract.
        bad = {**snapshot(version=2), 'catalogue': section}
        claims = jwt.decode(answer['signature'], options={'verify_signature': False})
        claims['sha'] = snapshots.snapshot_sha(bad)
        answer['snapshot'] = bad
        answer['signature'] = ADMIN.sign(claims)
    admin.tamper = invalid
    with pytest.raises(config.PullFailed) as error:
        pull(admin, 2, catalogue())
    assert error.value.code == 'invalid_body'
    assert state().version == 1
    assert priced('ES') == [('TPS', Money('90.00', 'EUR'))]


@pytest.mark.django_db
def test_the_same_version_changes_nothing(admin):  # noqa: F811
    pull(admin, 1, catalogue())
    assert pull(admin, 1, catalogue(products=[])) == ('unchanged', 1)
    assert priced('ES') == [('TPS', Money('90.00', 'EUR'))]


# --- Acceptance: a snapshot without a catalogue -------------------------------------------

@pytest.mark.django_db
def test_a_snapshot_without_a_catalogue_applies_the_directory_and_empties_the_tables(
        admin):  # noqa: F811
    assert pull(admin, 1, None) == ('applied', 1)
    assert AdminDirectoryEntry.objects.count() == 2
    assert not CountryGroup.objects.exists() and not Product.objects.exists()
    assert priced('ES') == []
    pull(admin, 2, catalogue())
    assert priced('ES')
    # An older admin site again (a rollback): the directory applies, the catalogue empties.
    assert pull(admin, 3, None, admin_directory=[
        {'id': ADA, 'display_name': 'Ada Renamed', 'title': '', 'active': True,
         'time_zone': 'UTC'}]) == ('applied', 3)
    assert list(AdminDirectoryEntry.objects.values_list('display_name', flat=True)) == [
        'Ada Renamed']
    assert not CountryGroup.objects.exists() and not Product.objects.exists()
    assert not Subscription.objects.exists()


# --- The tables and the applier -----------------------------------------------------------

@pytest.mark.django_db
def test_the_tables_hold_every_row_with_the_admin_ids(admin):  # noqa: F811
    pull(admin, 1, catalogue(products=[product(TPS, 'TPS', 'tps_branded', '100.00'),
                                       product(OTHER, 'Old', 'retainers', '5.00', active=False)]))
    assert {str(g.id) for g in CountryGroup.objects.all()} == {IBERIA, REST_OF_EU, WORLD}
    old = Product.objects.get(pk=OTHER)
    assert old.active is False and old.net_price == Money('5.00', 'EUR')
    basic = Subscription.objects.get(pk=BASIC)
    assert str(basic.country_group_id) == IBERIA
    assert basic.tps_discount_percent == Decimal('10.00')


@pytest.mark.django_db
def test_no_write_path_outside_the_snapshot_apply(admin):  # noqa: F811
    pull(admin, 1, catalogue())
    for row in (CountryGroup.objects.first(), Product.objects.first(),
                Subscription.objects.first()):
        with pytest.raises(TypeError, match='read-only'):
            row.save()
        with pytest.raises(TypeError, match='read-only'):
            row.delete()
    with pytest.raises(TypeError):
        Product.objects.create(name='X', category='tps_branded', net_amount=Decimal('1'),
                               currency='EUR', active=True)
    from django.contrib import admin as django_admin
    for model in (CountryGroup, Product, Subscription):
        assert not django_admin.site.is_registered(model)


@pytest.mark.django_db
def test_unknown_codes_are_skipped(admin):  # noqa: F811
    pull(admin, 1, catalogue(
        products=[product(TPS, 'TPS', 'tps_branded', '100.00'),
                  product(OTHER, 'Aligners', 'aligners', '10.00')],
        subscriptions=[subscription(BASIC, 'basic', IBERIA, '10.00'),
                       subscription(WORLD_BASIC, 'platinum', IBERIA, '50.00')]))
    assert list(Product.objects.values_list('name', flat=True)) == ['TPS']
    assert list(Subscription.objects.values_list('tier', flat=True)) == ['basic']


@pytest.mark.django_db
def test_a_failing_catalogue_apply_keeps_the_previous_snapshot(admin, monkeypatch):  # noqa: F811
    pull(admin, 1, catalogue())
    real = applier.apply

    def failing(section):
        real(section)
        raise RuntimeError('disk full')
    monkeypatch.setitem(config._SECTION_APPLIERS, 'catalogue', failing)
    with pytest.raises(config.PullFailed) as error:
        pull(admin, 2, catalogue(products=[]), admin_directory=[])
    assert error.value.code == 'apply_failed'
    assert state().version == 1 and AdminDirectoryEntry.objects.count() == 2
    assert priced('ES') == [('TPS', Money('90.00', 'EUR'))]


@pytest.mark.django_db
def test_the_catalogue_applier_is_registered_once():
    assert 'catalogue' in config.section_applier_names()
    config.register_section_applier('catalogue', applier.apply)  # the same: a no-op
    with pytest.raises(ValueError):
        config.register_section_applier('catalogue', lambda section: None)
    for name in ('coupons', 'version', 'admin_directory'):
        with pytest.raises(ValueError):
            config.register_section_applier(name, lambda section: None)
    # The regions app's country map (epic-catalogue entry 3) is another section, and the
    # provider costs (entry 6) and the vouchers (entry 10) are applied by this app too.
    assert config.section_applier_names() == ('country_map', 'catalogue', 'provider_costs',
                                              'vouchers')
    config.register_section_applier('provider_costs', applier.apply_provider_costs)
    with pytest.raises(ValueError):
        config.register_section_applier('provider_costs', lambda section: None)


@pytest.mark.django_db
def test_the_applied_log_line_has_the_catalogue_counts_only(admin, caplog):  # noqa: F811
    import logging
    caplog.set_level(logging.INFO, logger='integration.config')
    pull(admin, 1, catalogue(products=[
        product(TPS, 'Secret-Product-Name', 'tps_branded', '1.00')]))
    assert ('config snapshot v1 applied (2 admin directory entries); catalogue: '
            '3 country groups, 1 products, 1 subscriptions, 0 add-ons; '
            'no provider costs section') in caplog.text
    pull(admin, 2, None)
    assert ('config snapshot v2 applied (2 admin directory entries); no catalogue section'
            in caplog.text)
    assert 'Secret-Product-Name' not in caplog.text


# --- price_catalogue ----------------------------------------------------------------------

@pytest.mark.django_db
def test_price_catalogue_prints_the_offers(admin):  # noqa: F811
    pull(admin, 1, catalogue())
    out = io.StringIO()
    call_command('price_catalogue', '--country', 'ES', '--tier', 'basic', stdout=out)
    # The offers, after the subscription's line: no code, no name (the tier), no price.
    assert out.getvalue() == ('ES basic (Iberia): 1 offer(s)\n'
                              '  subscription: -  basic  free\n'
                              '  -  TPS  tps_branded  90.00 EUR\n')
    out = io.StringIO()
    call_command('price_catalogue', '--country', 'US', '--tier', 'basic', stdout=out)
    assert out.getvalue() == 'no offers for US basic\n'
    out = io.StringIO()
    call_command('price_catalogue', '--country', ' es ', '--tier', 'Basic', stdout=out)
    assert out.getvalue().startswith('ES basic (Iberia): 1 offer(s)')
    with pytest.raises(CommandError):
        call_command('price_catalogue', '--country', 'ES', '--tier', 'gold')


# --- Products in full (epic-catalogue entry 5) ------------------------------------------

IN_HOUSE = '77777777-7777-4777-8777-777777777777'
CATEGORIES = ('tps_branded', 'tps_in_house', 'initial_case_evaluation', 'virtuaortho_one',
              'virtuaortho_digitaltwin', 'odb', 'retainers', 'spark_on_demand',
              'subscription', 'education')
BOTH = {'en': {'name': 'Plan', 'description': 'A plan.\nIn two lines.'},
        'es': {'name': 'Plan (ES)', 'description': 'Un plan.'}}
DAY = date(2026, 11, 10)


def product_id(index):
    return f'aaaaaaaa-aaaa-4aaa-8aaa-{index:012d}'


def run(*args):
    out = io.StringIO()
    call_command('price_catalogue', *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_a_product_of_each_category_is_readable_in_both_languages(admin):  # noqa: F811
    from django.utils import translation
    products = [product(product_id(index), 'Plan', category, '100.00', texts=BOTH,
                        code=f'P-{index}')
                for index, category in enumerate(CATEGORIES)]
    assert pull(admin, 1, catalogue(products=products)) == ('applied', 1)
    assert set(Product.objects.values_list('category', flat=True)) == set(CATEGORIES)
    assert set(Product.CATEGORIES) == set(CATEGORIES) | {'uncategorised'}
    for row in Product.objects.all():
        assert row.texts == BOTH and row.code.startswith('P-')
        assert row.name_in('en') == 'Plan' and row.name_in('es') == 'Plan (ES)'
        assert row.description_in('en') == 'A plan.\nIn two lines.'
        assert row.description_in('es') == 'Un plan.'
        assert row.name_in('es-ES') == 'Plan (ES)' and row.name_in('fr') == 'Plan'
        with translation.override('es'):
            assert row.public_name == 'Plan (ES)' and row.description == 'Un plan.'
        with translation.override('en'):
            assert row.public_name == 'Plan' and row.description.startswith('A plan.')
    assert len(resolver.offers('ES', 'basic')) == len(CATEGORIES)


@pytest.mark.django_db
def test_a_missing_spanish_text_falls_back_to_english(admin):  # noqa: F811
    from django.utils import translation
    pull(admin, 1, catalogue(products=[
        product(TPS, 'TPS', 'tps_branded', '100.00',
                texts={'en': {'name': 'TPS', 'description': 'A plan.'}}),
        product(OTHER, 'Retainers', 'retainers', '50.00',
                texts={'en': {'name': 'Retainers', 'description': 'A pair.'},
                       'es': {'name': 'Retenedores', 'description': ''}})]))
    tps, retainers = Product.objects.get(pk=TPS), Product.objects.get(pk=OTHER)
    assert tps.name_in('es') == 'TPS' and tps.description_in('es') == 'A plan.'
    # Each text falls back on its own: a Spanish name, the English description.
    assert retainers.name_in('es') == 'Retenedores'
    assert retainers.description_in('es') == 'A pair.'
    with translation.override('es'):
        assert tps.public_name == 'TPS' and retainers.public_name == 'Retenedores'
        assert retainers.description == 'A pair.'
    assert tps.name_in(None) == tps.name_in('') == 'TPS'


@pytest.mark.django_db
def test_a_deactivated_product_stays_readable_by_id(admin):  # noqa: F811
    pull(admin, 1, catalogue(products=[
        product(TPS, 'Plan', 'tps_branded', '100.00', texts=BOTH, code='TPS-1')]))
    assert priced('ES')
    # Deactivated on the admin site after an order was placed.
    pull(admin, 2, catalogue(products=[
        product(TPS, 'Plan', 'tps_branded', '100.00', active=False, texts=BOTH,
                code='TPS-1')]))
    assert priced('ES') == []
    for wanted in (TPS, TPS.upper()):
        found = resolver.product(wanted)
        assert found.active is False and found.code == 'TPS-1'
        assert found.name_in('es') == 'Plan (ES)'
        assert found.net_price == Money('100.00', 'EUR')
    assert resolver.product(OTHER) is None and resolver.product('not-an-id') is None
    assert resolver.product(None) is None


def tps_pair(**more):
    return [product(TPS, 'Branded', 'tps_branded', '100.00', **more),
            product(IN_HOUSE, 'In-house', 'tps_in_house', '100.00', **more)]


@pytest.mark.django_db
def test_branded_and_in_house_tps_have_separate_discounts(admin):  # noqa: F811
    pull(admin, 1, catalogue(products=tps_pair(), subscriptions=[
        subscription(BASIC, 'basic', IBERIA, '10.00', in_house='0.00')]))
    assert priced('ES') == [('Branded', Money('90.00', 'EUR')),
                            ('In-house', Money('100.00', 'EUR'))]
    # The in-house discount set to 5 %: only the in-house product is repriced.
    pull(admin, 2, catalogue(products=tps_pair(), subscriptions=[
        subscription(BASIC, 'basic', IBERIA, '10.00', in_house='5.00')]))
    assert priced('ES') == [('Branded', Money('90.00', 'EUR')),
                            ('In-house', Money('95.00', 'EUR'))]
    basic = Subscription.objects.get(pk=BASIC)
    assert basic.tps_in_house_discount_percent == Decimal('5.00')
    branded, in_house = Product.objects.get(pk=TPS), Product.objects.get(pk=IN_HOUSE)
    assert resolver.discount_percent(branded, basic) == Decimal('10.00')
    assert resolver.discount_percent(in_house, basic) == Decimal('5.00')
    pull(admin, 3, catalogue(products=tps_pair(), subscriptions=[
        subscription(BASIC, 'basic', IBERIA, '0.00', in_house='33.33')]))
    assert priced('ES') == [('Branded', Money('100.00', 'EUR')),
                            ('In-house', Money('66.67', 'EUR'))]


@pytest.mark.django_db
def test_no_other_category_is_ever_discounted(admin):  # noqa: F811
    others = [category for category in CATEGORIES + ('uncategorised',)
              if category not in ('tps_branded', 'tps_in_house')]
    pull(admin, 1, catalogue(
        products=[product(product_id(index), f'P{index}', category, '100.00')
                  for index, category in enumerate(others)],
        subscriptions=[subscription(BASIC, 'basic', IBERIA, '100.00', in_house='100.00')]))
    offers = priced('ES')
    assert len(offers) == len(others) == 9
    assert all(price == Money('100.00', 'EUR') for _name, price in offers)
    basic = Subscription.objects.get(pk=BASIC)
    assert all(resolver.discount_percent(row, basic) == 0 for row in Product.objects.all())


@pytest.mark.django_db
def test_a_product_outside_its_validity_is_not_offered_but_stays_readable(
        admin, monkeypatch):  # noqa: F811
    yesterday, tomorrow = DAY - timedelta(days=1), DAY + timedelta(days=1)
    monkeypatch.setattr(resolver, 'today', lambda now=None: DAY)
    pull(admin, 1, catalogue(products=[
        product(product_id(1), 'Ended', 'odb', '1.00', valid_to=yesterday.isoformat()),
        product(product_id(2), 'Ends today', 'odb', '2.00', valid_to=DAY.isoformat()),
        product(product_id(3), 'Starts today', 'odb', '3.00', valid_from=DAY.isoformat()),
        product(product_id(4), 'Not yet', 'odb', '4.00', valid_from=tomorrow.isoformat()),
        product(product_id(5), 'Open', 'odb', '5.00'),
        product(product_id(6), 'Within', 'odb', '6.00', valid_from=yesterday.isoformat(),
                valid_to=tomorrow.isoformat())]))
    assert [name for name, _price in priced('ES')] == ['Ends today', 'Open', 'Starts today',
                                                      'Within']
    ended = resolver.product(product_id(1))
    assert ended.active is True and ended.valid_to == yesterday
    assert ended.net_price == Money('1.00', 'EUR') and not ended.offered_on(DAY)
    assert resolver.product(product_id(4)).valid_from == tomorrow
    # Another day, asked for explicitly.
    assert [p.name for p, _price in resolver.offers('ES', 'basic', day=yesterday)] == [
        'Ended', 'Ends today', 'Open', 'Within']


@pytest.mark.django_db
def test_the_validity_day_is_madrid_time():
    # 22:30 UTC on 30 June is 00:30 on 1 July in Madrid (summer time, UTC+2).
    assert resolver.today(datetime(2026, 6, 30, 22, 30, tzinfo=timezone.utc)) == date(
        2026, 7, 1)
    assert resolver.today(datetime(2026, 6, 30, 21, 59, tzinfo=timezone.utc)) == date(
        2026, 6, 30)
    # Winter time (UTC+1), and from another region's zone (Dubai, UTC+4).
    assert resolver.today(datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc)) == date(
        2027, 1, 1)
    dubai = timezone(timedelta(hours=4))
    assert resolver.today(datetime(2026, 7, 1, 1, 0, tzinfo=dubai)) == date(2026, 6, 30)
    assert isinstance(resolver.today(), date)


@pytest.mark.django_db
def test_a_snapshot_of_an_admin_site_before_entry_5_still_applies(admin):  # noqa: F811
    """The former categories (`tps`, `other`), no texts, code or dates, no in-house
    discount."""
    pull(admin, 1, catalogue(products=[product(TPS, 'TPS', 'tps', '100.00'),
                                       product(OTHER, 'Retainers', 'other', '50.00'),
                                       product(IN_HOUSE, 'Aligners', 'aligners', '10.00')]))
    tps, other = Product.objects.get(pk=TPS), Product.objects.get(pk=OTHER)
    assert tps.category == 'tps_branded' and other.category == 'uncategorised'
    assert tps.texts == {'en': {'name': 'TPS', 'description': ''}}
    assert tps.name_in('es') == 'TPS' and tps.description_in('es') == ''
    assert tps.code is None and tps.valid_from is None and tps.valid_to is None
    assert not Product.objects.filter(pk=IN_HOUSE).exists()  # an unknown category
    assert Subscription.objects.get(pk=BASIC).tps_in_house_discount_percent == Decimal('0')
    assert priced('ES') == [('Retainers', Money('50.00', 'EUR')),
                            ('TPS', Money('90.00', 'EUR'))]


@pytest.mark.django_db
def test_price_catalogue_prints_codes_texts_and_one_product(admin, monkeypatch):  # noqa: F811
    monkeypatch.setattr(resolver, 'today', lambda now=None: DAY)
    pull(admin, 1, catalogue(products=[
        product(TPS, 'Plan', 'tps_branded', '100.00', texts=BOTH, code='TPS-1'),
        product(IN_HOUSE, 'In-house', 'tps_in_house', '100.00', code='TPS-2'),
        product(OTHER, 'Plan', 'retainers', '50.00', active=False, texts=BOTH,
                code='RET-1', valid_to='2026-11-09')]))
    assert run('--country', 'ES', '--tier', 'basic') == (
        'ES basic (Iberia): 2 offer(s)\n'
        '  subscription: -  basic  free\n'
        '  TPS-2  In-house  tps_in_house  100.00 EUR\n'
        '  TPS-1  Plan  tps_branded  90.00 EUR\n')
    assert run('--country', 'ES', '--tier', 'basic', '--language', 'es') == (
        'ES basic (Iberia): 2 offer(s)\n'
        '  subscription: -  basic  free\n'
        '  TPS-2  In-house  tps_in_house  100.00 EUR\n'  # no Spanish: the English name
        '  TPS-1  Plan (ES)  tps_branded  90.00 EUR\n')
    assert run('--product', OTHER, '--language', 'es') == (
        f'{OTHER}  RET-1  Plan (ES)  retainers  50.00 EUR\n'
        '  status: inactive\n'
        '  valid: - to 2026-11-09 (today in Madrid: 2026-11-10)\n'
        '  offered for new orders: no\n'
        '  | Un plan.\n')
    assert run('--product', TPS) == (
        f'{TPS}  TPS-1  Plan  tps_branded  100.00 EUR\n'
        '  status: active\n'
        '  valid: - to - (today in Madrid: 2026-11-10)\n'
        '  offered for new orders: yes\n'
        '  | A plan.\n'
        '  | In two lines.\n')
    for args in (['--product', WORLD], ['--product', TPS, '--country', 'ES'],
                 ['--country', 'ES'], ['--tier', 'basic'], []):
        with pytest.raises(CommandError):
            run(*args)


# --- Migration 0002 -------------------------------------------------------------------------

def _migrate(targets):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def _migrate_to_latest():
    """The latest schema again: the current models read columns of later migrations."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())


@pytest.fixture
def at_0001(transactional_db):
    """The schema at catalogue 0001 for the test, the latest again afterwards."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    apps = _migrate([('catalogue', '0001_initial')])
    try:
        yield apps
    finally:
        _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_migration_0002_maps_the_former_categories(at_0001):
    OldGroup = at_0001.get_model('catalogue', 'CountryGroup')
    OldProduct = at_0001.get_model('catalogue', 'Product')
    OldSubscription = at_0001.get_model('catalogue', 'Subscription')
    iberia = OldGroup.objects.create(id=IBERIA, name='Iberia', countries=['ES'], active=True)
    OldProduct.objects.create(id=TPS, name='TPS', category='tps',
                              net_amount=Decimal('100.00'), currency='EUR', active=True)
    OldProduct.objects.create(id=OTHER, name='Retainers', category='other',
                              net_amount=Decimal('50.00'), currency='EUR', active=False)
    OldSubscription.objects.create(id=BASIC, tier='basic', country_group=iberia,
                                   tps_discount_percent=Decimal('10.00'), active=True)
    _migrate([('catalogue', '0002_products_core')])
    _migrate_to_latest()
    tps, other = Product.objects.get(pk=TPS), Product.objects.get(pk=OTHER)
    assert tps.category == 'tps_branded' and other.category == 'uncategorised'
    assert tps.texts == {'en': {'name': 'TPS', 'description': ''}}
    assert other.name_in('es') == 'Retainers' and other.active is False
    assert tps.code is None and tps.valid_from is None and tps.offered_on(DAY)
    basic = Subscription.objects.get(pk=BASIC)
    assert basic.tps_in_house_discount_percent == Decimal('0.00')
    assert priced('ES') == [('TPS', Money('90.00', 'EUR'))]
    apps = _migrate([('catalogue', '0001_initial')])
    Old = apps.get_model('catalogue', 'Product')
    assert Old.objects.get(pk=TPS).category == 'tps'
    assert Old.objects.get(pk=OTHER).category == 'other'


@pytest.mark.django_db(transaction=True)
def test_migration_0002_makes_the_first_pull_apply_the_current_version_again(
        at_0001, admin):  # noqa: F811
    """The admin site, deployed first, published the new categories in version 5; the
    previous release applied it and skipped every product."""
    from integration.models import ConfigState
    section = catalogue(products=[
        product(TPS, 'Plan', 'tps_branded', '100.00', texts=BOTH, code='TPS-1'),
        product(IN_HOUSE, 'In-house', 'tps_in_house', '100.00', code='TPS-2')])
    ConfigState.objects.update_or_create(pk=ConfigState.SINGLETON, defaults={'version': 5})
    assert not at_0001.get_model('catalogue', 'Product').objects.exists()
    apps = _migrate([('catalogue', '0002_products_core')])
    assert apps.get_model('integration', 'ConfigState').objects.get().version is None
    _migrate_to_latest()
    assert state().version is None
    assert pull(admin, 5, section) == ('applied', 5)
    assert state().version == 5
    assert priced('ES') == [('In-house', Money('100.00', 'EUR')),
                            ('Plan', Money('90.00', 'EUR'))]
    # Once: the same version again changes nothing.
    assert pull(admin, 5, catalogue(products=[])) == ('unchanged', 5)
    assert Product.objects.count() == 2
