"""Subscriptions in full on the portal (epic-catalogue entry 8, mytp-core 0.22.0): the
standard and the manual subscriptions stored by the snapshot apply (catalogue.applier)
with their code, texts, linked products, discounts per product category, own price and
call extras, and the resolver: only the products of a subscription are offered, at its
discount for the product's category; a manual one is read by its id; the calls of a
product under a subscription; with `price_catalogue`. One test (or more) per row of the
plan's matrix. The admin site is the fake of integration/tests/test_config.py (no
network)."""
import copy
from decimal import Decimal

import pytest
from django.core.management import CommandError

from catalogue import resolver
from catalogue.models import Product, Subscription
from catalogue.tests.test_catalogue import (BASIC, IBERIA, REST_OF_EU, TPS, catalogue,
                                            priced, product, pull, run, subscription)
from integration import config
from integration.tests.test_config import (ADMIN, admin,  # noqa: F401
                                           interface_settings, snapshot, state)
from mytp_core import snapshots
from mytp_core.money import Money

VIRTUA = 'b1111111-1111-4111-8111-111111111111'
TWIN = 'b2222222-2222-4222-8222-222222222222'
RETAINERS = 'b3333333-3333-4333-8333-333333333333'
IN_HOUSE = 'b4444444-4444-4444-8444-444444444444'
ADVANCED = 'd1111111-1111-4111-8111-111111111111'
ADVANCED_EU = 'd2222222-2222-4222-8222-222222222222'
PREMIUM = 'd3333333-3333-4333-8333-333333333333'
ORMCO = 'd4444444-4444-4444-8444-444444444444'
ORMCO_2 = 'd5555555-5555-4555-8555-555555555555'

PRODUCTS = [
    product(TPS, 'TPS branded', 'tps_branded', '100.00', code='TPS-BR',
            calls={'count': 3, 'minutes': 25}),
    product(IN_HOUSE, 'TPS in-house', 'tps_in_house', '100.00', code='TPS-IH'),
    product(VIRTUA, 'VirtuaOrtho One', 'virtuaortho_one', '250.00', code='VO-ONE',
            calls={'count': 3, 'minutes': 25}),
    product(TWIN, 'VirtuaOrtho DigitalTwin', 'virtuaortho_digitaltwin', '400.00',
            code='VO-TWIN', calls={'count': 2, 'minutes': 30}),
    product(RETAINERS, 'Retainers', 'retainers', '50.00', code='RET-1'),
]


def full_subscription(id_, tier, group_id, code, name, products, discounts=None,
                      active=True, **more):
    """A subscription as an admin site on mytp-core 0.22.0 sends it: the two former
    discount fields equal the map's `tps_branded` and `tps_in_house` values."""
    discounts = dict(discounts or {})
    return {'id': id_, 'tier': tier, 'country_group': group_id,
            'tps_discount_percent': discounts.get('tps_branded', '0.00'),
            'tps_in_house_discount_percent': discounts.get('tps_in_house', '0.00'),
            'active': active, 'code': code,
            'texts': {'en': {'name': name, 'description': ''}},
            'products': list(products), 'discounts': discounts, **more}


PREMIUM_EXTRAS = {'virtuaortho_one': {'extra_calls': 1, 'minutes': 35},
                  'virtuaortho_digitaltwin': {'extra_calls': 1, 'minutes': 35}}
STANDARD = [
    full_subscription(BASIC, 'basic', IBERIA, 'BAS-IB', 'Basic', [TPS, IN_HOUSE]),
    full_subscription(ADVANCED, 'advanced', IBERIA, 'ADV-IB', 'Advanced',
                      [TPS, IN_HOUSE, VIRTUA], {'tps_branded': '6.00'},
                      price={'currency': 'EUR', 'monthly': '60.00', 'upfront': '600.00'}),
    full_subscription(ADVANCED_EU, 'advanced', REST_OF_EU, 'ADV-EU', 'Advanced',
                      [TPS, IN_HOUSE], {'tps_branded': '6.00'},
                      price={'currency': 'EUR', 'monthly': '60.00', 'upfront': '600.00'}),
    full_subscription(PREMIUM, 'premium', IBERIA, 'PRE-IB', 'Premium',
                      [TPS, IN_HOUSE, VIRTUA, TWIN],
                      {'tps_branded': '12.00', 'tps_in_house': '12.00'},
                      price={'currency': 'EUR', 'monthly': '120.00', 'upfront': '1200.00'},
                      call_extras=PREMIUM_EXTRAS),
]
MANUAL = [
    full_subscription(ORMCO, 'premium', IBERIA, 'ORMCO-PREMIUM', 'Ormco Premium',
                      [TPS, TWIN], {'tps_branded': '20.00', 'virtuaortho_digitaltwin':
                                    '10.00'}),
]


def full(subscriptions=STANDARD, manual=MANUAL, products=PRODUCTS):
    """A catalogue of an admin site on mytp-core 0.22.0. Iberia: Basic (TPS only, free),
    Advanced (also VirtuaOrtho One; 6 % on TPS branded; 60 a month or 600 upfront),
    Premium (also the DigitalTwin; 12 % on both TPS; +1 call and 35 minutes on the
    VirtuaOrtho categories) and the manual Ormco Premium. Rest of EU: Advanced without
    VirtuaOrtho. Retainers are linked to no subscription."""
    section = catalogue(products=copy.deepcopy(products),
                        subscriptions=copy.deepcopy(subscriptions))
    if manual is not None:
        section['manual_subscriptions'] = copy.deepcopy(manual)
    return section


def names(offers):
    return [(offered.name, price) for offered, price in offers]


# --- Linked products ----------------------------------------------------------------------

@pytest.mark.django_db
def test_a_doctor_is_offered_only_the_products_of_their_subscription(admin):  # noqa: F811
    assert pull(admin, 1, full()) == ('applied', 1)
    # Advanced Iberia is linked to VirtuaOrtho One; Advanced Rest of EU is not.
    assert priced('ES', 'advanced') == [('TPS branded', Money('94.00', 'EUR')),
                                        ('TPS in-house', Money('100.00', 'EUR')),
                                        ('VirtuaOrtho One', Money('250.00', 'EUR'))]
    assert priced('DE', 'advanced') == [('TPS branded', Money('94.00', 'EUR')),
                                        ('TPS in-house', Money('100.00', 'EUR'))]
    assert priced('ES', 'basic') == [('TPS branded', Money('100.00', 'EUR')),
                                     ('TPS in-house', Money('100.00', 'EUR'))]
    # A product linked to no subscription is offered to nobody; it stays readable.
    for country, tier in (('ES', 'basic'), ('ES', 'advanced'), ('ES', 'premium'),
                          ('DE', 'advanced')):
        assert 'Retainers' not in [name for name, _price in priced(country, tier)]
    retainers = resolver.product(RETAINERS)
    assert retainers.active and not any(
        resolver.linked(retainers, row) for row in Subscription.objects.all())
    assert Subscription.objects.get(pk=ADVANCED).products == [TPS, IN_HOUSE, VIRTUA]


@pytest.mark.django_db
def test_a_linked_product_that_is_inactive_or_not_stored_is_not_offered(admin):  # noqa: F811
    products = copy.deepcopy(PRODUCTS)
    products[2]['active'] = False                      # VirtuaOrtho One, deactivated
    products[0]['category'] = 'a_new_category'         # not stored by this release
    pull(admin, 1, full(products=products))
    assert priced('ES', 'advanced') == [('TPS in-house', Money('100.00', 'EUR'))]
    # No link at all: no offers, though the subscription applies.
    subscriptions = copy.deepcopy(STANDARD)
    subscriptions[0]['products'] = []
    pull(admin, 2, full(subscriptions=subscriptions))
    assert resolver.subscription('ES', 'basic') is not None and priced('ES', 'basic') == []


@pytest.mark.django_db
def test_a_line_of_a_product_the_subscription_does_not_offer_is_refused(admin):  # noqa: F811
    pull(admin, 1, full())
    virtua = resolver.product(VIRTUA)
    assert resolver.price(virtua, resolver.subscription('ES', 'advanced')) == (
        Money('250.00', 'EUR'))
    for applies in (resolver.subscription('DE', 'advanced'),
                    resolver.subscription('ES', 'basic')):
        with pytest.raises(resolver.ProductRefused):
            resolver.price(virtua, applies)
    assert issubclass(resolver.ProductRefused, ValueError)


# --- Snapshots of an admin site before mytp-core 0.22.0 ------------------------------------

@pytest.mark.django_db
def test_a_snapshot_without_links_offers_every_product_as_before(admin):  # noqa: F811
    """An admin site before this release: subscriptions with their two TPS discounts
    only, no manual list."""
    old = [subscription(BASIC, 'basic', IBERIA, '10.00', in_house='5.00')]
    assert pull(admin, 1, full(subscriptions=old, manual=None)) == ('applied', 1)
    basic = Subscription.objects.get()
    assert basic.products is None and basic.discounts is None and basic.manual is False
    assert basic.code is None and basic.texts == {} and basic.free
    assert basic.call_extras == {} and basic.name_in('es') == ''
    assert priced('ES') == [('Retainers', Money('50.00', 'EUR')),
                            ('TPS branded', Money('90.00', 'EUR')),
                            ('TPS in-house', Money('95.00', 'EUR')),
                            ('VirtuaOrtho DigitalTwin', Money('400.00', 'EUR')),
                            ('VirtuaOrtho One', Money('250.00', 'EUR'))]
    assert all(resolver.linked(row, basic) for row in Product.objects.all())
    assert resolver.calls(resolver.product(VIRTUA), basic) == (3, 25)
    # Without the in-house field either (before mytp-core 0.20.0): 0.
    pull(admin, 2, full(subscriptions=[subscription(BASIC, 'basic', IBERIA, '10.00')],
                        manual=None))
    assert dict(priced('ES'))['TPS in-house'] == Money('100.00', 'EUR')


@pytest.mark.django_db
def test_the_first_pull_of_this_release_stores_the_new_parts_of_the_applied_version(
        admin, settings):  # noqa: F811
    """The admin site, deployed first, published version 5 with the new parts; the
    previous release applied it without them (as a snapshot without them here). No
    migration clears the applied version: the first pull of another release applies the
    same version again."""
    settings.SENTRY_RELEASE = '6b27169cc0f014905d7dbfe73335ef7aa2123d26'
    old = [subscription(BASIC, 'basic', IBERIA, '0.00', in_house='0.00')]
    assert pull(admin, 5, full(subscriptions=old, manual=None)) == ('applied', 5)
    assert 'Retainers' in dict(priced('ES'))
    assert pull(admin, 5, full()) == ('unchanged', 5)
    settings.SENTRY_RELEASE = 'c0ffee69cc0f014905d7dbfe73335ef7aa2123d2'
    assert pull(admin, 5, full()) == ('applied', 5)
    assert state().version == 5 and Subscription.objects.count() == 5
    assert 'Retainers' not in dict(priced('ES'))
    assert pull(admin, 5, full(manual=[])) == ('unchanged', 5)
    assert Subscription.objects.filter(manual=True).count() == 1


# --- Discounts per product category -------------------------------------------------------

@pytest.mark.django_db
def test_the_discount_is_the_subscriptions_for_the_products_category(admin):  # noqa: F811
    pull(admin, 1, full())
    premium = resolver.subscription('ES', 'premium')
    # 12 % on TPS branded: 100.00 is 88.00; nothing on VirtuaOrtho One: its net price.
    assert names(resolver.offers('ES', 'premium')) == [
        ('TPS branded', Money('88.00', 'EUR')), ('TPS in-house', Money('88.00', 'EUR')),
        ('VirtuaOrtho DigitalTwin', Money('400.00', 'EUR')),
        ('VirtuaOrtho One', Money('250.00', 'EUR'))]
    assert resolver.discount_percent(resolver.product(TPS), premium) == Decimal('12.00')
    assert resolver.discount_percent(resolver.product(VIRTUA), premium) == Decimal('0')
    assert premium.discounts == {'tps_branded': '12.00', 'tps_in_house': '12.00'}
    # Any category can be discounted: the manual one takes 10 % off the DigitalTwin.
    ormco = resolver.subscription_by_id(ORMCO)
    assert resolver.unit_price(resolver.product(TWIN), ormco) == Money('360.00', 'EUR')
    assert resolver.unit_price(resolver.product(TPS), ormco) == Money('80.00', 'EUR')


@pytest.mark.django_db
def test_the_two_former_fields_are_the_tps_discounts(admin):  # noqa: F811
    """A subscription saved before this release at the admin site (10 % branded, 0 %
    in-house), as that site now sends it."""
    saved = full_subscription(BASIC, 'basic', IBERIA, 'BAS-IB', 'Basic', [TPS, IN_HOUSE],
                              {'tps_branded': '10.00'})
    assert (saved['tps_discount_percent'], saved['tps_in_house_discount_percent']) == (
        '10.00', '0.00')
    pull(admin, 1, full(subscriptions=[saved]))
    basic = Subscription.objects.get(pk=BASIC)
    assert basic.tps_discount_percent == Decimal('10.00')
    assert basic.tps_in_house_discount_percent == Decimal('0.00')
    assert resolver.discount_percent(resolver.product(TPS), basic) == Decimal('10.00')
    assert resolver.discount_percent(resolver.product(IN_HOUSE), basic) == Decimal('0')
    assert priced('ES') == [('TPS branded', Money('90.00', 'EUR')),
                            ('TPS in-house', Money('100.00', 'EUR'))]
    # An uncategorised product is never discounted, whatever the map says.
    products = copy.deepcopy(PRODUCTS)
    products[0]['category'] = 'uncategorised'
    saved['discounts']['uncategorised'] = '50.00'
    pull(admin, 2, full(subscriptions=[saved], products=products))
    assert dict(priced('ES'))['TPS branded'] == Money('100.00', 'EUR')


# --- Manual subscriptions -----------------------------------------------------------------

@pytest.mark.django_db
def test_a_manual_subscription_is_read_by_its_id_only(admin):  # noqa: F811
    manual = MANUAL + [full_subscription(ORMCO_2, 'premium', IBERIA, 'ORMCO-PREMIUM-2',
                                         'Ormco Premium II', [TPS])]
    pull(admin, 1, full(manual=manual))
    # Three active premium subscriptions in Iberia: one standard, two manual.
    assert Subscription.objects.filter(tier='premium', active=True).count() == 3
    assert str(resolver.subscription('ES', 'premium').pk) == PREMIUM
    assert names(resolver.offers('ES', 'premium'))[0] == ('TPS branded',
                                                           Money('88.00', 'EUR'))
    ormco = resolver.subscription_by_id(ORMCO)
    assert ormco.manual and ormco.code == 'ORMCO-PREMIUM' and ormco.tier == 'premium'
    assert ormco.name_in('en') == 'Ormco Premium'
    assert resolver.subscription_by_code(' ormco-premium ') == ormco
    # Priced with its own products and discounts.
    assert names(resolver.offers_under(ormco)) == [
        ('TPS branded', Money('80.00', 'EUR')),
        ('VirtuaOrtho DigitalTwin', Money('360.00', 'EUR'))]
    assert resolver.price(resolver.product(TPS), ormco) == Money('80.00', 'EUR')
    with pytest.raises(resolver.ProductRefused):
        resolver.price(resolver.product(VIRTUA), ormco)
    for unknown in (TPS, 'not-a-uuid', '', None):
        assert resolver.subscription_by_id(unknown) is None
    assert resolver.subscription_by_code('NOPE') is None
    assert resolver.subscription_by_code('') is None


@pytest.mark.django_db
def test_without_the_standard_one_there_are_no_offers(admin):  # noqa: F811
    """The standard Premium of Iberia is deactivated: the tier and group have no
    default. The manual ones never stand in for it."""
    subscriptions = copy.deepcopy(STANDARD)
    subscriptions[3]['active'] = False
    assert pull(admin, 1, full(subscriptions=subscriptions)) == ('applied', 1)
    assert resolver.subscription('ES', 'premium') is None
    assert priced('ES', 'premium') == []
    # The deactivated one is kept, readable by its id, and offers nothing.
    premium = resolver.subscription_by_id(PREMIUM)
    assert premium.active is False and resolver.offers_under(premium) == []
    assert resolver.offers_under(resolver.subscription_by_id(ORMCO))
    # Only a manual one for a tier and group: still no default.
    pull(admin, 2, full(subscriptions=[], manual=MANUAL))
    assert priced('ES', 'premium') == [] and Subscription.objects.count() == 1


# --- Own price, call extras, code and texts -----------------------------------------------

@pytest.mark.django_db
def test_the_own_price_is_stored_and_readable(admin):  # noqa: F811
    pull(admin, 1, full())
    advanced = resolver.subscription('ES', 'advanced')
    assert advanced.price_currency == 'EUR' and not advanced.free
    assert advanced.monthly_price == Money('60.00', 'EUR')
    assert advanced.upfront_price == Money('600.00', 'EUR')
    basic = resolver.subscription('ES', 'basic')
    assert basic.free and basic.monthly_price is None and basic.upfront_price is None
    assert basic.price_currency == ''
    # One amount only.
    subscriptions = copy.deepcopy(STANDARD)
    subscriptions[1]['price'] = {'currency': 'EUR', 'upfront': '600.00'}
    pull(admin, 2, full(subscriptions=subscriptions))
    advanced = resolver.subscription('ES', 'advanced')
    assert advanced.monthly_price is None and not advanced.free
    assert advanced.upfront_price == Money('600.00', 'EUR')


@pytest.mark.django_db
def test_a_subscription_priced_in_an_unknown_currency_is_skipped(admin):  # noqa: F811
    subscriptions = copy.deepcopy(STANDARD)
    subscriptions[1]['price'] = {'currency': 'JPY', 'monthly': '9000.00'}
    assert pull(admin, 1, full(subscriptions=subscriptions)) == ('applied', 1)
    # Never stored as a free subscription: not stored at all.
    assert not Subscription.objects.filter(pk=ADVANCED).exists()
    assert priced('ES', 'advanced') == [] and priced('DE', 'advanced')


@pytest.mark.django_db
def test_the_call_extras_are_readable(admin):  # noqa: F811
    pull(admin, 1, full())
    premium, basic = (resolver.subscription('ES', tier) for tier in ('premium', 'basic'))
    assert premium.call_extras == PREMIUM_EXTRAS and basic.call_extras == {}
    virtua, twin, tps, in_house = (resolver.product(id_)
                                   for id_ in (VIRTUA, TWIN, TPS, IN_HOUSE))
    # A VirtuaOrtho product with 3 calls of 25 minutes gives 4 calls of 35 under Premium.
    assert resolver.calls(virtua, premium) == (4, 35)
    assert resolver.calls(twin, premium) == (3, 35)
    # Other categories are unchanged; so is every product under Basic.
    assert resolver.calls(tps, premium) == (3, 25)
    assert resolver.calls(virtua, basic) == (3, 25)
    # A product that grants no calls gets none.
    assert resolver.calls(in_house, premium) is None
    # Extra calls without a length keep the product's.
    subscriptions = copy.deepcopy(STANDARD)
    subscriptions[3]['call_extras'] = {'virtuaortho_one': {'extra_calls': 2}}
    pull(admin, 2, full(subscriptions=subscriptions))
    premium = resolver.subscription('ES', 'premium')
    assert premium.call_extras == {'virtuaortho_one': {'extra_calls': 2}}
    assert resolver.calls(resolver.product(VIRTUA), premium) == (5, 25)
    assert resolver.calls(resolver.product(TWIN), premium) == (2, 30)


@pytest.mark.django_db
def test_the_code_and_texts_are_stored_and_spanish_falls_back(admin):  # noqa: F811
    subscriptions = copy.deepcopy(STANDARD)
    subscriptions[1]['texts'] = {'en': {'name': 'Advanced', 'description': 'More.'}}
    subscriptions[3]['texts'] = {'en': {'name': 'Premium', 'description': 'All.'},
                                 'es': {'name': 'Premium (ES)', 'description': ''}}
    # Internal notes are not in the contract: one that slipped in is never stored.
    subscriptions[1]['notes'] = 'Internal-Only-Note'
    pull(admin, 1, full(subscriptions=subscriptions))
    advanced = resolver.subscription_by_code('adv-ib')
    assert advanced.code == 'ADV-IB' and str(advanced.pk) == ADVANCED
    assert advanced.name_in('es') == advanced.name_in('en') == 'Advanced'
    assert advanced.description_in('es-ES') == 'More.'
    premium = resolver.subscription('ES', 'premium')
    assert premium.name_in('es') == 'Premium (ES)' and premium.description_in('es') == 'All.'
    assert not hasattr(advanced, 'notes')
    assert 'Internal-Only-Note' not in str(list(Subscription.objects.values()))


# --- The contract's rules still refuse a bad snapshot --------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('change', [
    lambda s: s['subscriptions'].append(full_subscription(
        ORMCO_2, 'basic', IBERIA, 'BAS-2', 'Basic II', [TPS])),
    lambda s: s['manual_subscriptions'].append(dict(STANDARD[0])),
    lambda s: s['manual_subscriptions'][0].update(code='ADV-IB'),
    lambda s: s['subscriptions'][3].update(tps_discount_percent='10.00'),
    lambda s: s['subscriptions'][1]['price'].update(monthly='-1.00'),
    lambda s: s['subscriptions'][3]['call_extras']['virtuaortho_one'].update(minutes=0),
    lambda s: s['subscriptions'][3]['discounts'].update(odb='100.01'),
], ids=['second-active-standard', 'id-in-both-lists', 'code-twice',
        'former-field-differs-from-the-map', 'negative-price', 'minutes-below-1',
        'discount-above-100'])
def test_an_invalid_section_is_refused_and_the_applied_one_kept(admin, change):  # noqa: F811
    import jwt
    pull(admin, 1, full())
    section = full()
    change(section)

    def invalid(answer):
        bad = {**snapshot(version=2), 'catalogue': section}
        claims = jwt.decode(answer['signature'], options={'verify_signature': False})
        claims['sha'] = snapshots.snapshot_sha(bad)
        answer['snapshot'] = bad
        answer['signature'] = ADMIN.sign(claims)
    admin.tamper = invalid
    with pytest.raises(config.PullFailed) as error:
        pull(admin, 2, full())
    assert error.value.code == 'invalid_body'
    assert state().version == 1 and Subscription.objects.count() == 5


# --- price_catalogue ----------------------------------------------------------------------

@pytest.mark.django_db
def test_price_catalogue_lists_what_each_subscription_offers(admin):  # noqa: F811
    pull(admin, 1, full())
    assert run('--country', 'ES', '--tier', 'advanced') == (
        'ES advanced (Iberia): 3 offer(s)\n'
        '  subscription: ADV-IB  Advanced  60.00 EUR a month, 600.00 EUR upfront for a '
        'year\n'
        '  TPS-BR  TPS branded  tps_branded  94.00 EUR\n'
        '      calls: 3 of 25 minutes\n'
        '  TPS-IH  TPS in-house  tps_in_house  100.00 EUR\n'
        '  VO-ONE  VirtuaOrtho One  virtuaortho_one  250.00 EUR\n'
        '      calls: 3 of 25 minutes\n')
    # Rest of EU: no VirtuaOrtho.
    assert run('--country', 'DE', '--tier', 'advanced') == (
        'DE advanced (Rest of EU): 2 offer(s)\n'
        '  subscription: ADV-EU  Advanced  60.00 EUR a month, 600.00 EUR upfront for a '
        'year\n'
        '  TPS-BR  TPS branded  tps_branded  94.00 EUR\n'
        '      calls: 3 of 25 minutes\n'
        '  TPS-IH  TPS in-house  tps_in_house  100.00 EUR\n')
    # Premium: the call extras; the manual one is not listed for ES premium.
    out = run('--country', 'ES', '--tier', 'premium')
    assert out.startswith('ES premium (Iberia): 4 offer(s)\n'
                          '  subscription: PRE-IB  Premium  120.00 EUR a month, '
                          '1200.00 EUR upfront for a year\n')
    assert ('  VO-ONE  VirtuaOrtho One  virtuaortho_one  250.00 EUR\n'
            '      calls: 4 of 35 minutes\n') in out
    assert ('  VO-TWIN  VirtuaOrtho DigitalTwin  virtuaortho_digitaltwin  400.00 EUR\n'
            '      calls: 3 of 35 minutes\n') in out
    assert 'ORMCO' not in out and '360.00' not in out
    assert run('--country', 'ES', '--tier', 'basic').startswith(
        'ES basic (Iberia): 2 offer(s)\n  subscription: BAS-IB  Basic  free\n')
    assert run('--country', 'US', '--tier', 'premium') == 'no offers for US premium\n'


@pytest.mark.django_db
def test_price_catalogue_reads_a_subscription_by_its_id_or_code(admin):  # noqa: F811
    pull(admin, 1, full())
    expected = ('ORMCO-PREMIUM premium (Iberia), manual: 2 offer(s)\n'
                '  subscription: ORMCO-PREMIUM  Ormco Premium  free\n'
                '  TPS-BR  TPS branded  tps_branded  80.00 EUR\n'
                '      calls: 3 of 25 minutes\n'
                '  VO-TWIN  VirtuaOrtho DigitalTwin  virtuaortho_digitaltwin  360.00 EUR\n'
                '      calls: 2 of 30 minutes\n')
    assert run('--subscription', ORMCO) == expected
    assert run('--subscription', 'ormco-premium') == expected
    assert run('--subscription', 'PRE-IB').startswith(
        'PRE-IB premium (Iberia), standard: 4 offer(s)\n')
    # One line under it; a product it does not offer is refused.
    assert run('--subscription', ORMCO, '--product', TWIN) == (
        'ORMCO-PREMIUM premium (Iberia), manual: VO-TWIN  VirtuaOrtho DigitalTwin  '
        'virtuaortho_digitaltwin\n'
        '  subscription: ORMCO-PREMIUM  Ormco Premium  free\n'
        '  net price: 400.00 EUR\n'
        '  quantity: 1\n'
        '  subscription discount: 10.00 %\n'
        '  calls: 2 of 30 minutes\n'
        '  line price: 360.00 EUR\n')
    assert '  calls: 4 of 35 minutes\n' in run('--product', VIRTUA, '--country', 'ES',
                                               '--tier', 'premium')
    for args, message in (
            (['--subscription', ORMCO, '--product', VIRTUA], 'is not offered under'),
            (['--country', 'DE', '--tier', 'advanced', '--product', VIRTUA],
             'is not offered under'),
            (['--subscription', 'NOPE'], 'No subscription NOPE.'),
            (['--subscription', TPS], f'No subscription {TPS}.'),
            (['--subscription', ORMCO, '--country', 'ES', '--tier', 'premium'],
             'Give --subscription, or --country and --tier.'),
            (['--subscription', ORMCO, '--quantity', '2'], '--add-on and --quantity go'),
            (['--subscription', ORMCO, '--product', TWIN, '--cost'], '--cost goes with')):
        with pytest.raises(CommandError) as error:
            run(*args)
        assert message in str(error.value), args


@pytest.mark.django_db
def test_price_catalogue_says_when_a_subscription_offers_nothing(admin):  # noqa: F811
    subscriptions = copy.deepcopy(STANDARD)
    subscriptions[3]['active'] = False
    subscriptions[0]['products'] = []
    pull(admin, 1, full(subscriptions=subscriptions))
    assert run('--country', 'ES', '--tier', 'premium') == 'no offers for ES premium\n'
    assert run('--subscription', 'PRE-IB') == (
        'no offers for PRE-IB premium (Iberia), standard, inactive\n'
        '  subscription: PRE-IB  Premium  120.00 EUR a month, 1200.00 EUR upfront for a '
        'year\n')
    assert run('--country', 'ES', '--tier', 'basic') == (
        'no offers for ES basic (Iberia)\n  subscription: BAS-IB  Basic  free\n')
