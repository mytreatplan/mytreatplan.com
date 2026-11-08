"""The catalogue from the configuration snapshot (epic-catalogue entry 2): its tables,
filled only by the snapshot apply (integration.config with catalogue.applier), the
resolver that prices (catalogue.resolver) and `price_catalogue`. The admin site is the
fake of integration/tests/test_config.py (fixed, non-secret test seeds; no network)."""
import io
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


def product(id_, name, category, amount, currency='EUR', active=True):
    return {'id': id_, 'name': name, 'category': category,
            'net_price': {'amount': amount, 'currency': currency}, 'active': active}


def subscription(id_, tier, group_id, percent, active=True):
    return {'id': id_, 'tier': tier, 'country_group': group_id,
            'tps_discount_percent': percent, 'active': active}


def catalogue(products=None, subscriptions=None, groups=None):
    """Iberia (ES, PT) with a basic subscription at 10 %, a TPS product at 100.00 EUR;
    Rest of EU and the catch-all Rest of world without a subscription."""
    return {
        'country_groups': groups if groups is not None else [
            group(IBERIA, 'Iberia', ['ES', 'PT']),
            group(REST_OF_EU, 'Rest of EU', ['FR', 'DE']),
            group(WORLD, 'Rest of world', [], catch_all=True)],
        'products': products if products is not None else [
            product(TPS, 'TPS', 'tps', '100.00')],
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
    pull(admin, 1, catalogue(products=[product(TPS, 'TPS', 'tps', '100.00'),
                                       product(OTHER, 'Retainers', 'other', '50.00')]))
    assert priced('ES') == [('Retainers', Money('50.00', 'EUR')),
                            ('TPS', Money('90.00', 'EUR'))]


@pytest.mark.django_db
@pytest.mark.parametrize('change', ['product', 'subscription'])
def test_a_deactivated_row_is_gone_after_the_next_snapshot(admin, change):  # noqa: F811
    pull(admin, 1, catalogue())
    assert priced('ES')
    if change == 'product':
        section = catalogue(products=[product(TPS, 'TPS', 'tps', '100.00', active=False)])
    else:
        section = catalogue(subscriptions=[subscription(BASIC, 'basic', IBERIA, '10.00',
                                                        active=False)])
    assert pull(admin, 2, section) == ('applied', 2)
    assert priced('ES') == []
    # Inactive rows are kept (the snapshot lists them).
    assert Product.objects.count() == 1 and Subscription.objects.count() == 1


@pytest.mark.django_db
def test_rounding_is_half_up_to_cents(admin):  # noqa: F811
    pull(admin, 1, catalogue(products=[product(TPS, 'TPS', 'tps', '33.33')],
                             subscriptions=[subscription(BASIC, 'basic', IBERIA, '15.00')]))
    assert priced('ES') == [('TPS', Money('28.33', 'EUR'))]  # 28.3305
    pull(admin, 2, catalogue(products=[product(TPS, 'TPS', 'tps', '0.10')],
                             subscriptions=[subscription(BASIC, 'basic', IBERIA, '50.00')]))
    assert priced('ES') == [('TPS', Money('0.05', 'EUR'))]
    pull(admin, 3, catalogue(products=[product(TPS, 'TPS', 'tps', '0.15')],
                             subscriptions=[subscription(BASIC, 'basic', IBERIA, '50.00')]))
    assert priced('ES') == [('TPS', Money('0.08', 'EUR'))]  # 0.075 half up
    pull(admin, 4, catalogue(products=[product(TPS, 'TPS', 'tps', '80.00', 'AED')],
                             subscriptions=[subscription(BASIC, 'basic', IBERIA, '100.00')]))
    assert priced('ES') == [('TPS', Money('0.00', 'AED'))]


@pytest.mark.django_db
@pytest.mark.parametrize('section', [
    catalogue(subscriptions=[subscription(BASIC, 'basic', IBERIA, '120.00')]),
    catalogue(products=[product(TPS, 'TPS', 'tps', '-1.00')]),
    catalogue(subscriptions=[subscription(BASIC, 'basic', IBERIA, '10.00'),
                             subscription(WORLD_BASIC, 'basic', IBERIA, '5.00')]),
    catalogue(products=[{**product(TPS, 'TPS', 'tps', '1.00'), 'net_price': 1.0}]),
    catalogue(products=[{**product(TPS, 'TPS', 'tps', '1.00'),
                         'net_price': {'amount': 1.0, 'currency': 'EUR'}}]),
], ids=['discount-120', 'negative-price', 'second-active-subscription', 'float',
        'float-amount'])
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
    pull(admin, 1, catalogue(products=[product(TPS, 'TPS', 'tps', '100.00'),
                                       product(OTHER, 'Old', 'other', '5.00', active=False)]))
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
        Product.objects.create(name='X', category='tps', net_amount=Decimal('1'),
                               currency='EUR', active=True)
    from django.contrib import admin as django_admin
    for model in (CountryGroup, Product, Subscription):
        assert not django_admin.site.is_registered(model)


@pytest.mark.django_db
def test_unknown_codes_are_skipped(admin):  # noqa: F811
    pull(admin, 1, catalogue(
        products=[product(TPS, 'TPS', 'tps', '100.00'),
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
    for name in ('vouchers', 'version', 'admin_directory'):
        with pytest.raises(ValueError):
            config.register_section_applier(name, lambda section: None)
    # The regions app's country map (epic-catalogue entry 3) is the other section.
    assert config.section_applier_names() == ('country_map', 'catalogue')


@pytest.mark.django_db
def test_the_applied_log_line_has_the_catalogue_counts_only(admin, caplog):  # noqa: F811
    import logging
    caplog.set_level(logging.INFO, logger='integration.config')
    pull(admin, 1, catalogue(products=[product(TPS, 'Secret-Product-Name', 'tps', '1.00')]))
    assert ('config snapshot v1 applied (2 admin directory entries); catalogue: '
            '3 country groups, 1 products, 1 subscriptions') in caplog.text
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
    assert out.getvalue() == 'ES basic (Iberia): 1 offer(s)\n  TPS  tps  90.00 EUR\n'
    out = io.StringIO()
    call_command('price_catalogue', '--country', 'US', '--tier', 'basic', stdout=out)
    assert out.getvalue() == 'no offers for US basic\n'
    out = io.StringIO()
    call_command('price_catalogue', '--country', ' es ', '--tier', 'Basic', stdout=out)
    assert out.getvalue().startswith('ES basic (Iberia): 1 offer(s)')
    with pytest.raises(CommandError):
        call_command('price_catalogue', '--country', 'ES', '--tier', 'gold')
