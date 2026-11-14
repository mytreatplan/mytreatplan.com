"""Add-ons, quantities, calls and costs on the portal (epic-catalogue entry 6): the
add-ons, the new product fields, the cost links and the provider costs stored by the
snapshot apply (catalogue.applier), and the resolver: the add-ons offered on a product,
the price of a line with add-ons and a quantity, and a product's cost at a date; with
`price_catalogue` and migration 0003. One test (or more) per row of the plan's matrix.
The admin site is the fake of integration/tests/snapshot_rows.py (no network)."""
import logging
from datetime import date
from decimal import Decimal

import pytest
from django.core.management import CommandError

from catalogue import applier, resolver
from catalogue.models import AddOn, Product, ProviderCost, ProviderProduct
from catalogue.tests.test_catalogue import (BASIC, TPS, _migrate, _migrate_to_latest,
                                            catalogue, run, subscription)
from integration import config
from integration.tests.snapshot_rows import (ADD_ONS, ADMIN, BYTE_SYNC, CASE_CATEGORIES,
                                             COSTS, DM, IBERIA, PRINT, SCAN, USD_SCAN, add_on,
                                             cost, product, provider_product, pull,
                                             snapshot, state)
from mytp_core import snapshots
from mytp_core.money import Money

ODB = '44444444-4444-4444-8444-444444444444'
SPARK = '77777777-7777-4777-8777-777777777777'
BRANDED = '88888888-8888-4888-8888-888888888888'
SHORT = '99999999-9999-4999-8999-999999999999'
RETAINERS = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
DOLLARS = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
DAY = date(2026, 11, 10)
# Iberia's basic subscription: 10 % on TPS branded and on TPS in-house.
SUBSCRIPTIONS = [subscription(BASIC, 'basic', IBERIA, '10.00', in_house='10.00')]
PRODUCTS = [
    product(TPS, 'TPS in-house M', 'tps_in_house', '100.00', code='TPS-M', size='medium',
            calls={'count': 3, 'minutes': 25}),
    product(BRANDED, 'TPS branded', 'tps_branded', '100.00', code='TPS-BR'),
    product(SHORT, 'TPS in-house S', 'tps_in_house', '100.00', code='TPS-S', size='short'),
    product(ODB, 'ODB', 'odb', '500.00', code='ODB-1'),
    product(RETAINERS, 'Retainers', 'retainers', '50.00', code='RET-1'),
    product(DOLLARS, 'USD TPS', 'tps_branded', '100.00', currency='USD', code='TPS-USD'),
    product(SPARK, 'Spark on Demand one arch', 'spark_on_demand', '50.00', code='SPARK-1',
            per_unit={'min_quantity': 4, 'max_quantity': 9}),
]


def link(provider_product_id, quantity=1, monthly=False):
    return {'provider_product': provider_product_id, 'quantity': quantity,
            'monthly': monthly}


def full(products=PRODUCTS, add_ons=ADD_ONS):
    section = catalogue(products=products, subscriptions=SUBSCRIPTIONS)
    if add_ons is not None:
        section['add_ons'] = add_ons
    return section


def linked(*links):
    """The catalogue with one TPS product carrying these cost links."""
    return full(products=[product(TPS, 'TPS', 'tps_in_house', '100.00', code='TPS-1',
                                  cost_links=list(links))])


def basic():
    return resolver.subscription('ES', 'basic')


def chosen(*codes):
    return [resolver.add_on(code) for code in codes]


def offered_on(product_id):
    return [a.code for a in resolver.add_ons(resolver.product(product_id))]


def line(product_id, *codes, quantity=1):
    return resolver.price(resolver.product(product_id), basic(), chosen(*codes), quantity)


# --- The matrix: add-ons ------------------------------------------------------------------

@pytest.mark.django_db
def test_byte_sync_makes_any_case_line_120_higher(admin):
    pull(admin, 1, full())
    for product_id in (TPS, BRANDED, SHORT, ODB):
        assert 'BYTE-SYNC' in offered_on(product_id)
        assert line(product_id, 'BYTE-SYNC') - line(product_id) == Money('120.00', 'EUR')
    # The subscription discount applies to the product only: 100 × 0.90 + 120.
    assert line(BRANDED) == Money('90.00', 'EUR')
    assert line(BRANDED, 'BYTE-SYNC') == Money('210.00', 'EUR')
    # Not a case category; and a EUR amount is not offered on a USD product.
    for product_id in (RETAINERS, DOLLARS, SPARK):
        assert 'BYTE-SYNC' not in offered_on(product_id)
    for product_id in (RETAINERS, DOLLARS):
        with pytest.raises(resolver.AddOnRefused):
            line(product_id, 'BYTE-SYNC')


@pytest.mark.django_db
def test_odb_digitaltwin_is_320_on_odb_and_offered_nowhere_else(admin):
    pull(admin, 1, full())
    assert offered_on(ODB) == ['BYTE-SYNC', 'ODB-TWIN']
    assert line(ODB) == Money('500.00', 'EUR')
    assert line(ODB, 'ODB-TWIN') == Money('820.00', 'EUR')
    assert line(ODB, 'ODB-TWIN', 'BYTE-SYNC') == Money('940.00', 'EUR')
    for product_id in (TPS, BRANDED, RETAINERS):
        assert 'ODB-TWIN' not in offered_on(product_id)
        with pytest.raises(resolver.AddOnRefused):
            line(product_id, 'ODB-TWIN')


@pytest.mark.django_db
def test_one_arch_and_the_discount_are_applied_together_and_rounded_once(admin):
    pull(admin, 1, full())
    assert line(TPS, 'ONE-ARCH') == Money('67.50', 'EUR')  # 100 × 0.75 × 0.90
    assert line(TPS, 'ONE-ARCH', 'BYTE-SYNC') == Money('187.50', 'EUR')
    # Not offered on TPS branded or any other category.
    assert 'ONE-ARCH' in offered_on(TPS) and 'ONE-ARCH' in offered_on(SHORT)
    for product_id in (BRANDED, ODB, RETAINERS, SPARK):
        assert 'ONE-ARCH' not in offered_on(product_id)
        with pytest.raises(resolver.AddOnRefused):
            resolver.price(resolver.product(product_id), basic(), chosen('ONE-ARCH'),
                           6 if product_id == SPARK else 1)
    # Rounded once, at the end: 0.05 × 0.75 × 0.90 = 0.03375 is 0.03 (rounding the
    # one-arch price to 0.04 first would give 0.04).
    pull(admin, 2, full(products=[product(TPS, 'Tiny', 'tps_in_house', '0.05')]))
    assert line(TPS, 'ONE-ARCH') == Money('0.03', 'EUR')
    pull(admin, 3, full(products=[product(TPS, 'Odd', 'tps_in_house', '33.33')]))
    assert line(TPS, 'ONE-ARCH') == Money('22.50', 'EUR')  # 22.49775


@pytest.mark.django_db
def test_junior_is_offered_on_size_medium_only_and_changes_no_price(admin):
    pull(admin, 1, full())
    assert offered_on(TPS) == ['BYTE-SYNC', 'JUNIOR-1', 'JUNIOR-2', 'ONE-ARCH']
    assert line(TPS, 'JUNIOR-1') == line(TPS, 'JUNIOR-2') == line(TPS) == (
        Money('90.00', 'EUR'))
    assert line(TPS, 'JUNIOR-1', 'JUNIOR-2') == Money('90.00', 'EUR')
    # Size short, or no size: not offered.
    for product_id in (SHORT, BRANDED):
        assert not {'JUNIOR-1', 'JUNIOR-2'} & set(offered_on(product_id))
        with pytest.raises(resolver.AddOnRefused):
            line(product_id, 'JUNIOR-1')
    pull(admin, 2, full(products=[
        product(TPS, 'Long', 'tps_in_house', '100.00', size='long')]))
    assert offered_on(TPS) == ['BYTE-SYNC', 'ONE-ARCH']


@pytest.mark.django_db
def test_an_inactive_or_repeated_add_on_is_refused(admin):
    add_ons = [{**entry, 'active': entry['code'] != 'BYTE-SYNC'} for entry in ADD_ONS]
    pull(admin, 1, full(add_ons=add_ons))
    assert 'BYTE-SYNC' not in offered_on(TPS)
    assert resolver.add_on('byte-sync ').active is False  # kept, readable by code
    with pytest.raises(resolver.AddOnRefused):
        line(TPS, 'BYTE-SYNC')
    with pytest.raises(resolver.AddOnRefused):
        line(TPS, 'ONE-ARCH', 'ONE-ARCH')
    assert resolver.add_on('nope') is None and resolver.add_on(None) is None


# --- The matrix: per unit, calls -------------------------------------------------------------

@pytest.mark.django_db
def test_a_product_per_unit_is_priced_by_its_quantity_within_the_range(admin):
    pull(admin, 1, full())
    spark = resolver.product(SPARK)
    assert spark.per_unit and (spark.min_quantity, spark.max_quantity) == (4, 9)
    assert line(SPARK, quantity=6) == Money('300.00', 'EUR')
    assert line(SPARK, quantity=4) == Money('200.00', 'EUR')
    assert line(SPARK, quantity=9) == Money('450.00', 'EUR')
    for quantity in (3, 10, 1, 0, -6, 6.0, '6', True, None):
        with pytest.raises(resolver.QuantityRefused):
            line(SPARK, quantity=quantity)
    # A quantity other than 1 on a product that is not priced per unit is refused.
    assert not resolver.product(TPS).per_unit
    for quantity in (2, 6, 0):
        with pytest.raises(resolver.QuantityRefused):
            line(TPS, quantity=quantity)
    # The offers list the price of one unit.
    offers = {offered.code: price for offered, price in resolver.offers('ES', 'basic')}
    assert offers['SPARK-1'] == Money('50.00', 'EUR')
    assert offers['TPS-M'] == Money('90.00', 'EUR')
    assert resolver.unit_price(spark, basic()) == Money('50.00', 'EUR')


@pytest.mark.django_db
def test_the_calls_of_a_product_are_readable(admin):
    pull(admin, 1, full())
    tps, branded = resolver.product(TPS), resolver.product(BRANDED)
    assert (tps.calls, tps.call_minutes, tps.size) == (3, 25, 'medium')
    assert (branded.calls, branded.call_minutes, branded.size) == (None, None, '')
    assert '  calls: 3 of 25 minutes\n' in run('--product', TPS)
    assert 'calls:' not in run('--product', BRANDED)


# --- The matrix: costs -----------------------------------------------------------------------

@pytest.mark.django_db
def test_the_cost_at_a_date_is_the_sum_of_the_links(admin):
    pull(admin, 1, linked(link(SCAN), link(PRINT, 2)), provider_costs=COSTS)
    tps = resolver.product(TPS)
    assert tps.cost_links == [link(SCAN), link(PRINT, 2)]
    cost_ = resolver.cost(tps, date(2026, 10, 31))
    assert cost_.total == Money('25.00', 'EUR')  # 10.00 + 2 × 7.50 (the discounted price)
    assert cost_.complete and cost_.missing == () and cost_.monthly == ()
    assert cost_.warning == ''
    assert resolver.cost(tps, date(2026, 6, 30)).total == Money('26.00', 'EUR')
    # No links: nothing to sum, so no cost to show (never a known cost of 0.00).
    pull(admin, 2, full(), provider_costs=COSTS)
    cost_ = resolver.cost(resolver.product(TPS), DAY)
    assert cost_.total == Money.zero('EUR') and cost_.empty
    assert run('--product', TPS, '--cost', '--on', '2026-11-10').endswith(
        '  cost on 2026-11-10: no cost links\n')
    # Only a monthly link: the same, with the monthly cost apart.
    pull(admin, 3, linked(link(DM, 2, monthly=True)), provider_costs=COSTS)
    assert resolver.cost(resolver.product(TPS), DAY).empty
    assert run('--product', TPS, '--cost', '--on', '2026-11-10').endswith(
        '  cost on 2026-11-10: no cost links\n'
        '  monthly, not in the sum: DM x 2: 10.00 EUR a month\n')


@pytest.mark.django_db
def test_a_gap_in_the_costs_gives_the_partial_sum_marked_incomplete(
        admin, monkeypatch):
    monkeypatch.setattr(resolver, 'today', lambda now=None: DAY)
    pull(admin, 1, linked(link(SCAN), link(PRINT, 2)), provider_costs=COSTS)
    cost_ = resolver.cost(resolver.product(TPS))  # today: PRINT ended on 31 October
    assert cost_.total == Money('10.00', 'EUR') and cost_.complete is False
    assert cost_.missing == ('PRINT',)
    assert cost_.warning == 'Incomplete cost: no cost on this date for PRINT.'
    # Before the first range: nothing is known.
    cost_ = resolver.cost(resolver.product(TPS), date(2025, 12, 31))
    assert cost_.total == Money.zero('EUR') and cost_.missing == ('SCAN', 'PRINT')
    # A provider product the admin site did not send, or in another currency than the
    # product: missing too, never summed.
    costs = {'products': [entry for entry in COSTS['products'] if entry['id'] != SCAN]}
    pull(admin, 2, linked(link(SCAN), link(USD_SCAN)), provider_costs=costs)
    cost_ = resolver.cost(resolver.product(TPS), DAY)
    assert cost_.total == Money.zero('EUR') and not cost_.complete
    assert cost_.missing == (SCAN, 'USD-SCAN') and not cost_.empty
    # Two linked provider products with the same code are told apart by their ids.
    costs = {'products': [provider_product(SCAN, 'SCAN', []),
                          provider_product(PRINT, 'SCAN', [cost('2026-01-01', '8.00')]),
                          provider_product(DM, 'DM', [cost('2026-01-01', '5.00')])]}
    pull(admin, 3, linked(link(SCAN), link(PRINT), link(DM)), provider_costs=costs)
    cost_ = resolver.cost(resolver.product(TPS), DAY)
    assert cost_.total == Money('13.00', 'EUR') and cost_.missing == (f'SCAN ({SCAN})',)
    assert cost_.warning == f'Incomplete cost: no cost on this date for SCAN ({SCAN}).'


@pytest.mark.django_db
def test_a_monthly_link_is_listed_apart_and_never_in_the_sum(admin):
    pull(admin, 1, linked(link(SCAN), link(DM, 2, monthly=True),
                          link(PRINT, 1, monthly=True)), provider_costs=COSTS)
    cost_ = resolver.cost(resolver.product(TPS), DAY)
    assert cost_.total == Money('10.00', 'EUR') and cost_.complete
    assert [(m.label, m.quantity, m.cost) for m in cost_.monthly] == [
        ('DM', 2, Money('10.00', 'EUR')), ('PRINT', 1, None)]


# --- The matrix: an old snapshot -------------------------------------------------------------

@pytest.mark.django_db
def test_a_snapshot_of_an_admin_site_before_this_release_still_applies(admin):
    pull(admin, 1, full(), provider_costs=COSTS)
    assert AddOn.objects.count() == 5 and ProviderProduct.objects.count() == 4
    # No add-ons, no new product fields, no provider costs section.
    old = catalogue(products=[product(TPS, 'TPS', 'tps_branded', '100.00')])
    assert pull(admin, 2, old) == ('applied', 2)
    assert not AddOn.objects.exists()
    assert not ProviderProduct.objects.exists() and not ProviderCost.objects.exists()
    tps = resolver.product(TPS)
    assert (tps.size, tps.min_quantity, tps.max_quantity, tps.calls, tps.call_minutes,
            tps.cost_links) == ('', None, None, None, None, [])
    assert resolver.add_ons(tps) == []
    assert [(p.name, price) for p, price in resolver.offers('ES', 'basic')] == [
        ('TPS', Money('90.00', 'EUR'))]
    assert resolver.price(tps, basic()) == Money('90.00', 'EUR')
    cost_ = resolver.cost(tps, DAY)
    assert cost_.total == Money.zero('EUR') and cost_.complete


# --- The applier -------------------------------------------------------------------------------

@pytest.mark.django_db
def test_the_tables_hold_what_the_admin_site_sent(admin, caplog):
    caplog.set_level(logging.INFO, logger='integration.config')
    add_ons = ADD_ONS + [
        add_on('a6666666-6666-4666-8666-666666666666', 'GBP-ONLY', 'Secret-Add-On',
               ['odb'], amount={'amount': '1.00', 'currency': 'GBP'})]
    costs = {'products': COSTS['products'] + [provider_product(
        'c5555555-5555-4555-8555-555555555555', 'SECRET-CODE', [cost('2026-01-01', '1.00')],
        currency='GBP')]}
    pull(admin, 1, full(add_ons=add_ons), provider_costs=costs)
    # A currency this release does not know is skipped, as the contract says.
    assert sorted(AddOn.objects.values_list('code', flat=True)) == [
        'BYTE-SYNC', 'JUNIOR-1', 'JUNIOR-2', 'ODB-TWIN', 'ONE-ARCH']
    byte_sync, one_arch = resolver.add_on('BYTE-SYNC'), resolver.add_on('ONE-ARCH')
    assert str(byte_sync.pk) == BYTE_SYNC and byte_sync.amount_money == Money('120', 'EUR')
    assert (byte_sync.percent, byte_sync.sizes, byte_sync.value_text) == (
        None, None, '+120.00 EUR')
    assert byte_sync.categories == CASE_CATEGORIES and byte_sync.name_in('es') == 'Byte Sync'
    assert (one_arch.amount, one_arch.currency, one_arch.amount_money) == (None, '', None)
    assert one_arch.percent == Decimal('-25.00') and one_arch.value_text == '-25.00 %'
    assert resolver.add_on('JUNIOR-1').sizes == ['medium']
    assert sorted(ProviderProduct.objects.values_list('code', flat=True)) == [
        'DM', 'PRINT', 'SCAN', 'USD-SCAN']
    printing = ProviderProduct.objects.get(pk=PRINT)
    assert [(c.valid_from, c.valid_to, c.face, c.discounted)
            for c in printing.costs.order_by('valid_from')] == [
        (date(2026, 1, 1), date(2026, 6, 30), Decimal('8.00'), Decimal('8.00')),
        (date(2026, 7, 1), date(2026, 10, 31), Decimal('8.00'), Decimal('7.50'))]
    # The log line has counts only.
    assert ('catalogue: 3 country groups, 7 products, 1 subscriptions, 5 add-ons; '
            'provider costs: 4 provider products, 5 costs') in caplog.text
    assert 'Secret-Add-On' not in caplog.text and 'SECRET-CODE' not in caplog.text
    # The next snapshot replaces every row.
    pull(admin, 2, full(add_ons=[]), provider_costs={'products': []})
    assert not AddOn.objects.exists() and not ProviderProduct.objects.exists()
    assert not ProviderCost.objects.exists()


@pytest.mark.django_db
def test_no_write_path_outside_the_snapshot_apply(admin):
    pull(admin, 1, full(), provider_costs=COSTS)
    for row in (AddOn.objects.first(), ProviderProduct.objects.first(),
                ProviderCost.objects.first()):
        with pytest.raises(TypeError):
            row.save()
        with pytest.raises(TypeError):
            row.delete()


@pytest.mark.django_db
def test_a_failing_provider_costs_apply_keeps_the_previous_snapshot(
        admin, monkeypatch):
    pull(admin, 1, linked(link(SCAN)), provider_costs=COSTS)

    def failing(section):
        applier.apply_provider_costs(section)
        raise RuntimeError('disk full')
    monkeypatch.setitem(config._SECTION_APPLIERS, 'provider_costs', failing)
    with pytest.raises(config.PullFailed) as error:
        pull(admin, 2, full(add_ons=[]), provider_costs={'products': []})
    assert error.value.code == 'apply_failed'
    assert state().version == 1 and AddOn.objects.count() == 5
    assert resolver.cost(resolver.product(TPS), DAY).total == Money('10.00', 'EUR')


@pytest.mark.django_db
@pytest.mark.parametrize('change', [
    lambda section: section['add_ons'].append({**ADD_ONS[0]}),
    lambda section: section['add_ons'][0].update(percent='5.00'),
    lambda section: section['add_ons'][2].update(percent='-100.01'),
    lambda section: section['products'][0].update(per_unit={'min_quantity': 9,
                                                            'max_quantity': 4}),
    lambda section: section['products'][0].update(calls={'count': 3}),
    lambda section: section['products'][0].update(cost_links=[link(SCAN), link(SCAN, 2)]),
])
def test_an_invalid_part_is_refused_and_the_applied_catalogue_kept(admin, change):
    import copy

    import jwt
    pull(admin, 1, full())
    section = copy.deepcopy(full())
    change(section)

    def invalid(answer):
        # Signed correctly over a snapshot whose catalogue breaks the contract.
        bad = {**snapshot(version=2), 'catalogue': section}
        claims = jwt.decode(answer['signature'], options={'verify_signature': False})
        claims['sha'] = snapshots.snapshot_sha(bad)
        answer['snapshot'] = bad
        answer['signature'] = ADMIN.sign(claims)
    admin.tamper = invalid
    with pytest.raises(config.PullFailed) as error:
        pull(admin, 2, full())
    assert error.value.code == 'invalid_body'
    assert state().version == 1 and AddOn.objects.count() == 5
    assert line(TPS, 'ONE-ARCH') == Money('67.50', 'EUR')


# --- price_catalogue ----------------------------------------------------------------------------

@pytest.mark.django_db
def test_price_catalogue_shows_add_ons_quantities_and_line_prices(
        admin, monkeypatch):
    monkeypatch.setattr(resolver, 'today', lambda now=None: DAY)
    pull(admin, 1, full(), provider_costs=COSTS)
    assert run('--country', 'ES', '--tier', 'basic') == (
        'ES basic (Iberia): 7 offer(s)\n'
        '  subscription: -  basic  free\n'
        '  ODB-1  ODB  odb  500.00 EUR\n'
        '      + BYTE-SYNC  Byte Sync  +120.00 EUR\n'
        '      + ODB-TWIN  ODB DigitalTwin  +320.00 EUR\n'
        '  RET-1  Retainers  retainers  50.00 EUR\n'
        '  SPARK-1  Spark on Demand one arch  spark_on_demand  50.00 EUR  '
        'per unit, 4 to 9\n'
        '  TPS-BR  TPS branded  tps_branded  90.00 EUR\n'
        '      + BYTE-SYNC  Byte Sync  +120.00 EUR\n'
        '  TPS-M  TPS in-house M  tps_in_house  90.00 EUR\n'
        '      calls: 3 of 25 minutes\n'  # the product's: the subscription adds none
        '      + BYTE-SYNC  Byte Sync  +120.00 EUR\n'
        '      + JUNIOR-1  Junior I  +0.00 EUR\n'
        '      + JUNIOR-2  Junior II  +0.00 EUR\n'
        '      + ONE-ARCH  One arch  -25.00 %\n'
        '  TPS-S  TPS in-house S  tps_in_house  90.00 EUR\n'
        '      + BYTE-SYNC  Byte Sync  +120.00 EUR\n'
        '      + ONE-ARCH  One arch  -25.00 %\n'
        '  TPS-USD  USD TPS  tps_branded  90.00 USD\n')  # no EUR amount on a USD product
    assert run('--product', TPS) == (
        f'{TPS}  TPS-M  TPS in-house M  tps_in_house  100.00 EUR\n'
        '  status: active\n'
        '  valid: - to - (today in Madrid: 2026-11-10)\n'
        '  offered for new orders: yes\n'
        '  size: medium\n'
        '  calls: 3 of 25 minutes\n'
        '  add-on: BYTE-SYNC  Byte Sync  +120.00 EUR\n'
        '  add-on: JUNIOR-1  Junior I  +0.00 EUR\n'
        '  add-on: JUNIOR-2  Junior II  +0.00 EUR\n'
        '  add-on: ONE-ARCH  One arch  -25.00 %\n')
    assert '  per unit: 4 to 9\n' in run('--product', SPARK)
    assert run('--product', TPS, '--country', 'es', '--tier', 'basic', '--add-on',
               'one-arch', '--add-on', 'BYTE-SYNC') == (
        'ES basic (Iberia): TPS-M  TPS in-house M  tps_in_house\n'
        '  subscription: -  basic  free\n'
        '  net price: 100.00 EUR\n'
        '  quantity: 1\n'
        '  subscription discount: 10.00 %\n'
        '  calls: 3 of 25 minutes\n'
        '  add-on: ONE-ARCH  One arch  -25.00 %\n'
        '  add-on: BYTE-SYNC  Byte Sync  +120.00 EUR\n'
        '  line price: 187.50 EUR\n')
    assert run('--product', SPARK, '--country', 'ES', '--tier', 'basic', '--quantity',
               '6') == (
        'ES basic (Iberia): SPARK-1  Spark on Demand one arch  spark_on_demand\n'
        '  subscription: -  basic  free\n'
        '  net price: 50.00 EUR per unit\n'
        '  quantity: 6\n'
        '  subscription discount: 0.00 %\n'
        '  line price: 300.00 EUR\n')
    for args, message in (
            (['--quantity', '3'], 'Refused: the quantity is from 4 to 9.'),
            (['--quantity', '10'], 'Refused: the quantity is from 4 to 9.'),
            ([], 'Refused: the quantity is from 4 to 9.'),
            (['--quantity', '6', '--add-on', 'ONE-ARCH'], 'Refused: the add-on ONE-ARCH'),
            (['--quantity', '6', '--add-on', 'NOPE'], 'No add-on NOPE.')):
        with pytest.raises(CommandError) as error:
            run('--product', SPARK, '--country', 'ES', '--tier', 'basic', *args)
        assert message in str(error.value), args
    with pytest.raises(CommandError) as error:
        run('--product', TPS, '--country', 'ES', '--tier', 'basic', '--quantity', '2')
    assert 'not priced per unit' in str(error.value)
    with pytest.raises(CommandError) as error:
        run('--product', BRANDED, '--country', 'ES', '--tier', 'basic', '--add-on',
            'JUNIOR-1')
    assert 'JUNIOR-1 is not offered' in str(error.value)
    with pytest.raises(CommandError) as error:
        run('--product', TPS, '--country', 'US', '--tier', 'basic')
    assert 'No active basic subscription applies to US.' in str(error.value)
    for args in (['--country', 'ES', '--tier', 'basic', '--add-on', 'BYTE-SYNC'],
                 ['--country', 'ES', '--tier', 'basic', '--quantity', '2'],
                 ['--product', TPS, '--quantity', '1'],
                 ['--product', TPS, '--country', 'ES', '--tier', 'basic', '--cost'],
                 ['--country', 'ES', '--tier', 'basic', '--cost'],
                 ['--product', TPS, '--on', '2026-10-31'], ['--cost']):
        with pytest.raises(CommandError):
            run(*args)


@pytest.mark.django_db
def test_price_catalogue_shows_the_cost_and_the_incomplete_warning(
        admin, monkeypatch):
    monkeypatch.setattr(resolver, 'today', lambda now=None: DAY)
    pull(admin, 1, linked(link(SCAN), link(PRINT, 2), link(DM, 2, monthly=True)),
         provider_costs=COSTS)
    assert run('--product', TPS, '--cost', '--on', '2026-10-31').endswith(
        '  cost on 2026-10-31: 25.00 EUR\n'
        '  monthly, not in the sum: DM x 2: 10.00 EUR a month\n')
    out = run('--product', TPS, '--cost')  # today: PRINT's cost ended on 31 October
    assert out.endswith(
        '  warning: Incomplete cost: no cost on this date for PRINT.\n'
        '  partial sum on 2026-11-10, not the cost: 10.00 EUR\n'
        '  monthly, not in the sum: DM x 2: 10.00 EUR a month\n')
    assert 'cost on 2026-11-10:' not in out
    assert 'cost' not in run('--product', TPS)
    assert run('--product', TPS, '--cost', '--on', '2025-06-01').endswith(
        '  warning: Incomplete cost: no cost on this date for SCAN, PRINT.\n'
        '  partial sum on 2025-06-01, not the cost: 0.00 EUR\n'
        '  monthly, not in the sum: DM x 2: no cost on this date\n')


# --- Another release, the same version ------------------------------------------------------------

@pytest.mark.django_db
def test_another_release_stores_the_add_ons_and_costs_of_the_version_already_applied(
        admin, settings):
    """One release applied version 5 without add-ons and provider costs; another one
    pulls the same version carrying them: the real appliers store them, once."""
    settings.SENTRY_RELEASE = '6b27169cc0f014905d7dbfe73335ef7aa2123d26'
    assert pull(admin, 5, full(add_ons=None)) == ('applied', 5)
    assert Product.objects.exists()
    assert not AddOn.objects.exists() and not ProviderProduct.objects.exists()
    # The same release: unchanged, nothing stored.
    assert pull(admin, 5, full(), provider_costs=COSTS) == ('unchanged', 5)
    assert not AddOn.objects.exists() and not ProviderProduct.objects.exists()
    settings.SENTRY_RELEASE = 'c0ffee69cc0f014905d7dbfe73335ef7aa2123d2'
    assert pull(admin, 5, full(), provider_costs=COSTS) == ('applied', 5)
    assert state().version == 5 and AddOn.objects.count() == 5
    assert ProviderProduct.objects.count() == 4 and ProviderCost.objects.exists()
    assert line(TPS, 'ONE-ARCH') == Money('67.50', 'EUR')
    assert pull(admin, 5, full(add_ons=[])) == ('unchanged', 5)
    assert AddOn.objects.count() == 5 and ProviderProduct.objects.count() == 4


# --- Migration 0003 ------------------------------------------------------------------------------

@pytest.fixture
def at_0002(transactional_db):
    """The schema at catalogue 0002 for the test, the latest again afterwards."""
    apps = _migrate([('catalogue', '0002_products_core')])
    try:
        yield apps
    finally:
        _migrate_to_latest()


@pytest.mark.django_db(transaction=True)
def test_migration_0003_keeps_the_products_and_makes_the_first_pull_apply_again(
        at_0002, admin):
    """The admin site, deployed first, published add-ons and costs in version 5; the
    previous release applied it without them."""
    OldProduct = at_0002.get_model('catalogue', 'Product')
    OldProduct.objects.create(
        id=TPS, name='TPS', texts={'en': {'name': 'TPS', 'description': ''}},
        category='tps_in_house', net_amount=Decimal('100.00'), currency='EUR', active=True)
    ConfigState = at_0002.get_model('integration', 'ConfigState')
    ConfigState.objects.update_or_create(pk=1, defaults={'version': 5})
    _migrate_to_latest()
    tps = Product.objects.get(pk=TPS)
    assert (tps.size, tps.min_quantity, tps.calls, tps.cost_links) == ('', None, None, [])
    assert state().version is None
    assert pull(admin, 5, full(), provider_costs=COSTS) == ('applied', 5)
    assert state().version == 5 and AddOn.objects.count() == 5
    assert ProviderProduct.objects.count() == 4
    assert line(TPS, 'ONE-ARCH') == Money('67.50', 'EUR')
    # Once: the same version again changes nothing.
    assert pull(admin, 5, full(add_ons=[])) == ('unchanged', 5)
    assert AddOn.objects.count() == 5
    # And back: the tables and columns go, the products stay.
    apps = _migrate([('catalogue', '0002_products_core')])
    assert apps.get_model('catalogue', 'Product').objects.filter(pk=TPS).exists()
