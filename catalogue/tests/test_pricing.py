"""Pricing an order on the portal (epic-catalogue entry 12, AD-7, AD-16): `price_order` and
`price_subscription` through catalogue.resolver, and `price_catalogue --order` / `--buy`.
One test (or more) per row of the plan's matrix, on the documented prices (TPS branded
199/249/359 EUR, AED 1045; in-house 170; VirtuaOrtho One 1200; ICE 80; Advanced 6 % and
Premium 12 % on TPS). The admin site is the fake of integration/tests/snapshot_rows.py
(no network)."""
import random
from datetime import date
from decimal import Decimal

import pytest
from django.core.management import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from catalogue import pricing, resolver
from catalogue.tests.test_catalogue import run
from integration.tests.snapshot_rows import (COMPANIES, EMIRATES, EXEMPT, IRELAND, REVERSE,
                                             SPAIN, add_on, full_subscription, group,
                                             product, pull, rule, voucher)
from mytp_core.money import Money

IBERIA = 'a1000001-0000-4000-8000-000000000001'
EU = 'a1000002-0000-4000-8000-000000000002'
UAE = 'a1000003-0000-4000-8000-000000000003'
WORLD = 'a1000004-0000-4000-8000-000000000004'

TPS_S = 'b1000001-0000-4000-8000-000000000001'
TPS_M = 'b1000002-0000-4000-8000-000000000002'
TPS_L = 'b1000003-0000-4000-8000-000000000003'
IN_HOUSE = 'b1000004-0000-4000-8000-000000000004'
VO_ONE = 'b1000005-0000-4000-8000-000000000005'
RETAINER = 'b1000006-0000-4000-8000-000000000006'
RETAINER_EU = 'b1000007-0000-4000-8000-000000000007'
EDUCATION = 'b1000008-0000-4000-8000-000000000008'
EDUCATION_EU = 'b1000009-0000-4000-8000-000000000009'
SPARK_USD = 'b1000010-0000-4000-8000-000000000010'
ODB = 'b1000011-0000-4000-8000-000000000011'
TPS_AED = 'b1000012-0000-4000-8000-000000000012'
OLD = 'b1000013-0000-4000-8000-000000000013'
INACTIVE = 'b1000014-0000-4000-8000-000000000014'
UNLINKED = 'b1000015-0000-4000-8000-000000000015'
NO_RULE = 'b1000016-0000-4000-8000-000000000016'

BASIC_IB = 'e1000001-0000-4000-8000-000000000001'
ADVANCED_IB = 'e1000002-0000-4000-8000-000000000002'
PREMIUM_IB = 'e1000003-0000-4000-8000-000000000003'
BASIC_EU = 'e1000004-0000-4000-8000-000000000004'
BASIC_UAE = 'e1000005-0000-4000-8000-000000000005'

TRY_ME = 'f1000001-0000-4000-8000-000000000001'
SUBSCRIPTION_VOUCHER = 'f1000002-0000-4000-8000-000000000002'
AED_VOUCHER = 'f1000003-0000-4000-8000-000000000003'
FOR_ANA = 'f1000004-0000-4000-8000-000000000004'
INACTIVE_VOUCHER = 'f1000005-0000-4000-8000-000000000005'
BIG = 'f1000006-0000-4000-8000-000000000006'
TEN = 'f1000007-0000-4000-8000-000000000007'
ANA = '9a000000-0000-4000-8000-000000000001'
BOB = '9a000000-0000-4000-8000-000000000002'
ARCH = 'c1000001-0000-4000-8000-000000000001'
BYTE = 'c1000002-0000-4000-8000-000000000002'

DAY = date(2026, 11, 13)
EDU_EXEMPT = 'Exempt education (art. 132 Directive 2006/112/EC).'
ESPANA = ['redsys', 'bank_transfer']

IBERIA_PRODUCTS = [TPS_S, TPS_M, TPS_L, IN_HOUSE, VO_ONE, RETAINER, EDUCATION, SPARK_USD,
                   ODB, TPS_AED, OLD, INACTIVE, NO_RULE]


def catalogue_section(rules_=True):
    """Iberia (ES company, exempt; education billed by the Irish company; Spark sold in
    USD; the ODB at 21 % through Revolut only), Rest of EU (the Irish company, 23 % with
    a reverse-charge text; education exempt), the UAE (5 %, AED, no gateway)."""
    section = {
        'country_groups': [group(IBERIA, 'Iberia', ['ES', 'PT']),
                           group(EU, 'Rest of EU', ['IE', 'DE']),
                           group(UAE, 'UAE', ['AE']),
                           group(WORLD, 'Rest of world', [], catch_all=True)],
        'products': [
            product(TPS_S, 'TPS branded Small', 'tps_branded', '199.00', code='TPS-S'),
            product(TPS_M, 'TPS branded Medium', 'tps_branded', '249.00', code='TPS-M'),
            product(TPS_L, 'TPS branded Large', 'tps_branded', '359.00', code='TPS-L'),
            product(IN_HOUSE, 'TPS in-house Medium', 'tps_in_house', '170.00',
                    code='TPS-IH-M'),
            product(VO_ONE, 'VirtuaOrtho One', 'virtuaortho_one', '1200.00', code='VO-1'),
            product(RETAINER, 'Retainers', 'retainers', '100.00', code='RET'),
            product(RETAINER_EU, 'Retainers EU', 'retainers', '10.02', code='RET-EU'),
            product(EDUCATION, 'Course', 'education', '90.00', code='EDU'),
            product(EDUCATION_EU, 'Course EU', 'education', '90.00', code='EDU-EU'),
            product(SPARK_USD, 'Spark', 'spark_on_demand', '40.00', 'USD', code='SPARK'),
            product(ODB, 'ODB', 'odb', '80.00', code='ODB'),
            product(TPS_AED, 'TPS branded Medium AED', 'tps_branded', '1045.00', 'AED',
                    code='TPS-M-AED'),
            product(OLD, 'TPS old', 'tps_branded', '249.00', code='TPS-OLD',
                    valid_to='2026-01-01'),
            product(INACTIVE, 'TPS gone', 'tps_branded', '249.00', code='TPS-GONE',
                    active=False),
            product(UNLINKED, 'TPS unlinked', 'tps_branded', '249.00', code='TPS-UNL'),
            product(NO_RULE, 'Case evaluation', 'initial_case_evaluation', '30.00',
                    code='ICE-EVAL'),
        ],
        'subscriptions': [
            full_subscription(BASIC_IB, 'basic', IBERIA, 'BAS-IB', 'Basic', IBERIA_PRODUCTS),
            full_subscription(ADVANCED_IB, 'advanced', IBERIA, 'ADV-IB', 'Advanced',
                              IBERIA_PRODUCTS, {'tps_branded': '6.00'},
                              price={'currency': 'EUR', 'monthly': '60.00',
                                     'upfront': '600.00'}),
            full_subscription(PREMIUM_IB, 'premium', IBERIA, 'PRE-IB', 'Premium',
                              IBERIA_PRODUCTS,
                              {'tps_branded': '12.00', 'tps_in_house': '12.00'},
                              price={'currency': 'EUR', 'monthly': '120.00'}),
            full_subscription(BASIC_EU, 'basic', EU, 'BAS-EU', 'Basic',
                              [TPS_M, RETAINER_EU, EDUCATION_EU],
                              price={'currency': 'EUR', 'monthly': '60.00'}),
            full_subscription(BASIC_UAE, 'basic', UAE, 'BAS-AE', 'Basic', [TPS_AED],
                              price={'currency': 'AED', 'monthly': '250.00'}),
        ],
        'add_ons': [add_on(ARCH, 'ONE-ARCH', 'One arch', ['tps_in_house'], percent='-25.00'),
                    add_on(BYTE, 'BYTE-SYNC', 'Byte Sync', ['tps_branded'],
                           amount={'amount': '120.00', 'currency': 'EUR'})],
        'billing_companies': COMPANIES,
    }
    if rules_:
        section['billing_rules'] = [
            *(rule(index, IBERIA, category, SPAIN, '0.00', ESPANA, exemption_text=EXEMPT)
              for index, category in enumerate(
                  ('tps_branded', 'tps_in_house', 'virtuaortho_one', 'retainers'), 1)),
            rule(5, IBERIA, 'education', IRELAND, '23.00', ['revolut']),
            rule(6, IBERIA, 'spark_on_demand', SPAIN, '0.00', ['redsys'], 'USD',
                 exemption_text=EXEMPT),
            rule(7, IBERIA, 'odb', SPAIN, '21.00', ['revolut']),
            rule(8, IBERIA, 'subscription', SPAIN, '21.00', ['redsys']),
            rule(11, EU, 'tps_branded', IRELAND, '23.00', ['revolut', 'bank_transfer'],
                 reverse_charge_text=REVERSE),
            rule(12, EU, 'retainers', IRELAND, '23.00', ['revolut', 'bank_transfer'],
                 reverse_charge_text=REVERSE),
            rule(13, EU, 'education', IRELAND, '0.00', ['bank_transfer', 'revolut'],
                 exemption_text=EDU_EXEMPT),
            rule(14, EU, 'subscription', IRELAND, '23.00', ['revolut'],
                 reverse_charge_text=REVERSE),
            rule(21, UAE, 'tps_branded', EMIRATES, '5.00', [], 'AED'),
            rule(22, UAE, 'subscription', EMIRATES, '5.00', [], 'AED'),
        ]
    return section


VOUCHERS = [
    voucher(TRY_ME, 'TRYME23456', amount='50.00'),
    voucher(SUBSCRIPTION_VOUCHER, 'SUBSC23456', type_='subscription', amount='100.00'),
    voucher(AED_VOUCHER, 'AEDVO23456', amount='50.00', currency='AED'),
    voucher(FOR_ANA, 'ANAVO23456', amount='50.00', client=ANA),
    voucher(INACTIVE_VOUCHER, 'OFFVO23456', amount='50.00', active=False),
    voucher(BIG, 'BIGVO23456', amount='500.00'),
    voucher(TEN, 'TENVO23456', amount='10.00'),
]


@pytest.fixture
def priced_catalogue(admin):
    assert pull(admin, 1, catalogue_section(), vouchers=VOUCHERS) == ('applied', 1)


def eur(amount):
    return Money(amount, 'EUR')


def line(product_id, quantity=1, add_ons=(), ice=None):
    return resolver.OrderLine(resolver.product(product_id), quantity, tuple(add_ons), ice)


def order(country, tier, *lines, vies=resolver.VIES_NOT_APPLICABLE, voucher=None,
          doctor=None, day=DAY):
    return resolver.price_order(
        country, resolver.subscription(country, tier), list(lines), vies=vies,
        voucher=resolver.voucher_by_code(voucher) if voucher else None, doctor=doctor,
        day=day)


def buy(country, subscription_code, period, vies=resolver.VIES_NOT_APPLICABLE,
        voucher=None, doctor=None, day=DAY):
    return resolver.price_subscription(
        country, resolver.subscription_by_code(subscription_code), period, vies=vies,
        voucher=resolver.voucher_by_code(voucher) if voucher else None, doctor=doctor,
        day=day)


def refused(reason, call, *args, **kwargs):
    with pytest.raises(resolver.OrderRefused) as caught:
        call(*args, **kwargs)
    assert caught.value.reason == reason
    return caught.value


def holds(priced):
    """The acceptance criteria every priced order meets."""
    zero = Money.zero(priced.currency)
    assert priced.net_total + priced.vat_total == priced.total
    assert sum((line.net for line in priced.lines), zero) == priced.net_total
    assert sum((line.voucher for line in priced.lines), zero) == priced.voucher_taken
    assert sum((group.base for group in priced.vat_groups), zero) == priced.net_total
    assert all(line.net >= zero for line in priced.lines)
    for line in priced.lines:
        assert line.list_amount - line.discount - line.ice - line.voucher == line.net
    return priced


# --- The I/O matrix -----------------------------------------------------------------------

@pytest.mark.django_db
def test_an_es_doctor_basic_tps_branded_medium(priced_catalogue):
    priced = holds(order('ES', 'basic', line(TPS_M)))
    (only,) = priced.lines
    assert (only.list_amount, only.discount_percent, only.discount, only.net) == (
        eur('249.00'), Decimal('0.00'), eur('0.00'), eur('249.00'))
    assert (only.vat_percent, only.vat_text) == (Decimal('0.00'), EXEMPT)
    assert priced.company.code == 'ES' and priced.currency == 'EUR'
    assert [(g.percent, g.text, g.base, g.vat) for g in priced.vat_groups] == [
        (Decimal('0.00'), EXEMPT, eur('249.00'), eur('0.00'))]
    assert (priced.net_total, priced.vat_total, priced.total) == (
        eur('249.00'), eur('0.00'), eur('249.00'))
    assert priced.gateways == ('redsys', 'bank_transfer')
    assert priced.needs_payment and not priced.no_payment_method


@pytest.mark.django_db
def test_an_advanced_doctor_gets_the_tps_discount(priced_catalogue):
    (only,) = holds(order('ES', 'advanced', line(TPS_M))).lines
    assert (only.discount_percent, only.discount, only.net) == (
        Decimal('6.00'), eur('14.94'), eur('234.06'))
    (premium,) = holds(order('ES', 'premium', line(TPS_M))).lines
    assert (premium.discount, premium.net) == (eur('29.88'), eur('219.12'))


@pytest.mark.django_db
def test_virtuaortho_under_advanced_is_left_to_the_data(priced_catalogue):
    (only,) = holds(order('ES', 'advanced', line(VO_ONE))).lines
    assert (only.discount_percent, only.discount, only.net) == (
        Decimal('0.00'), eur('0.00'), eur('1200.00'))


@pytest.mark.django_db
def test_an_ie_doctor_pays_23_percent_on_the_order_net(priced_catalogue):
    priced = holds(order('IE', 'basic', line(TPS_M)))
    assert priced.company.code == 'IE'
    assert (priced.lines[0].vat_percent, priced.lines[0].vat_text) == (Decimal('23.00'), '')
    assert (priced.vat_total, priced.total) == (eur('57.27'), eur('306.27'))
    assert priced.gateways == ('revolut', 'bank_transfer')


@pytest.mark.django_db
def test_a_de_doctor_with_a_valid_vies_number_is_reverse_charged(priced_catalogue):
    priced = holds(order('DE', 'basic', line(TPS_M), vies=resolver.VIES_VALID))
    assert (priced.lines[0].vat_percent, priced.lines[0].vat_text) == (
        Decimal('0.00'), REVERSE)
    assert (priced.vat_total, priced.total) == (eur('0.00'), eur('249.00'))


@pytest.mark.django_db
@pytest.mark.parametrize('vies', [resolver.VIES_INVALID, resolver.VIES_PENDING,
                                  resolver.VIES_NOT_APPLICABLE])
def test_a_de_doctor_without_a_valid_vies_number_pays_the_rate(priced_catalogue, vies):
    priced = holds(order('DE', 'basic', line(TPS_M), vies=vies))
    assert priced.lines[0].vat_percent == Decimal('23.00')
    assert priced.vat_total == eur('57.27')


@pytest.mark.django_db
def test_an_ie_doctor_with_a_valid_vies_number_pays_the_rate(priced_catalogue):
    # The company's own country: no reverse charge.
    priced = holds(order('IE', 'basic', line(TPS_M), vies=resolver.VIES_VALID))
    assert priced.vat_total == eur('57.27')


@pytest.mark.django_db
def test_a_valid_vies_number_without_a_reverse_charge_text_pays_the_rate(priced_catalogue):
    # Iberia's education rule (Irish company, 23 %) has no reverse-charge text.
    priced = holds(order('ES', 'basic', line(EDUCATION), vies=resolver.VIES_VALID))
    assert priced.lines[0].vat_percent == Decimal('23.00')
    assert priced.vat_total == eur('20.70')


@pytest.mark.django_db
def test_the_ice_credit_comes_off_its_planning_line(priced_catalogue):
    priced = holds(order('ES', 'basic', line(TPS_M, ice=eur('80.00'))))
    (only,) = priced.lines
    assert (only.ice, only.net) == (eur('80.00'), eur('169.00'))
    assert priced.ice_total == eur('80.00') and priced.total == eur('169.00')
    # After the discount: 234.06 - 80.00.
    (advanced,) = holds(order('ES', 'advanced', line(TPS_M, ice=eur('80.00')))).lines
    assert advanced.net == eur('154.06')
    for planning in (IN_HOUSE, VO_ONE):
        assert holds(order('ES', 'basic', line(planning, ice=eur('80.00')))).ice_total == \
            eur('80.00')


@pytest.mark.django_db
def test_an_ice_credit_above_the_line_is_lost(priced_catalogue):
    priced = holds(order('ES', 'basic', line(TPS_S, ice=eur('300.00')), line(RETAINER)))
    assert [(found.ice, found.net) for found in priced.lines] == [
        (eur('199.00'), eur('0.00')), (eur('0.00'), eur('100.00'))]
    assert priced.ice_total == eur('199.00') and priced.total == eur('100.00')


@pytest.mark.django_db
def test_an_ice_credit_on_another_line_or_currency_is_refused(priced_catalogue):
    refusal = refused(resolver.ICE_NOT_PLANNING, order, 'ES', 'basic',
                      line(RETAINER, ice=eur('80.00')))
    assert refusal.subject.pk == resolver.product(RETAINER).pk
    refused(resolver.ICE_CURRENCY, order, 'ES', 'basic',
            line(TPS_M, ice=Money('80.00', 'AED')))
    refused(resolver.ICE_TWICE, order, 'ES', 'basic', line(TPS_M, ice=eur('80.00')),
            line(TPS_S, ice=eur('80.00')))


@pytest.mark.django_db
def test_a_voucher_is_spread_in_proportion(priced_catalogue):
    # 100.00 + (359.00 - 59.00 ICE) = 400.00: the 50.00 splits 12.50 / 37.50.
    priced = holds(order('ES', 'basic', line(RETAINER), line(TPS_L, ice=eur('59.00')),
                         voucher='TRYME-23456'))
    assert [found.voucher for found in priced.lines] == [eur('12.50'), eur('37.50')]
    assert [found.net for found in priced.lines] == [eur('87.50'), eur('262.50')]
    assert priced.voucher_taken == eur('50.00') and priced.total == eur('350.00')


@pytest.mark.django_db
def test_odd_voucher_cents_go_to_the_lines_in_order(priced_catalogue):
    priced = holds(order('ES', 'basic', line(RETAINER), line(RETAINER), line(RETAINER),
                         voucher='TENVO23456'))
    assert [found.voucher for found in priced.lines] == [
        eur('3.34'), eur('3.33'), eur('3.33')]
    assert priced.voucher_taken == eur('10.00')


@pytest.mark.django_db
def test_voucher_shares_never_pass_a_line_and_sum_exactly():
    generator = random.Random(12)
    for _ in range(500):
        bases = [eur(Decimal(generator.randint(0, 3000)).scaleb(-2))
                 for _ in range(generator.randint(1, 5))]
        total = sum(bases, eur(0))
        taken = min(total, eur(Decimal(generator.randint(0, 9000)).scaleb(-2)))
        shares = pricing._spread(taken, bases)
        assert sum(shares, eur(0)) == taken
        assert all(eur(0) <= share <= base for share, base in zip(shares, bases))
    assert pricing._spread(eur('0.02'), [eur('0.01')] * 3) == [
        eur('0.01'), eur('0.01'), eur('0.00')]


@pytest.mark.django_db
def test_a_voucher_above_the_order_leaves_nothing_to_pay(priced_catalogue):
    priced = holds(order('ES', 'basic', line(RETAINER), line(TPS_L, ice=eur('59.00')),
                         voucher='BIGVO23456'))
    assert all(found.net == eur('0.00') for found in priced.lines)
    assert priced.voucher_taken == eur('400.00') and priced.total == eur('0.00')
    assert not priced.needs_payment and priced.gateways == ()
    assert not priced.no_payment_method


@pytest.mark.django_db
def test_two_vat_rates_are_each_computed_on_their_own_sum(priced_catalogue):
    priced = holds(order('IE', 'basic', line(TPS_M), line(EDUCATION_EU)))
    assert [(g.percent, g.text, g.base, g.vat) for g in priced.vat_groups] == [
        (Decimal('23.00'), '', eur('249.00'), eur('57.27')),
        (Decimal('0.00'), EDU_EXEMPT, eur('90.00'), eur('0.00'))]
    assert priced.total == eur('396.27')
    # The first line's rule decides the order of the common gateways.
    assert priced.gateways == ('revolut', 'bank_transfer')
    assert holds(order('IE', 'basic', line(EDUCATION_EU), line(TPS_M))).gateways == (
        'bank_transfer', 'revolut')


@pytest.mark.django_db
def test_vat_is_per_rate_not_per_line(priced_catalogue):
    # 10.02 × 23 % is 2.3046 (2.30) per line; on the sum 20.04 it is 4.6092: 4.61.
    priced = holds(order('IE', 'basic', line(RETAINER_EU), line(RETAINER_EU)))
    assert [(g.base, g.vat) for g in priced.vat_groups] == [(eur('20.04'), eur('4.61'))]
    assert priced.total == eur('24.65')


@pytest.mark.django_db
def test_a_voucher_in_another_currency_is_refused(priced_catalogue):
    refused(resolver.VOUCHER_CURRENCY, order, 'ES', 'basic', line(TPS_M),
            voucher='AEDVO23456')


@pytest.mark.django_db
def test_a_voucher_not_usable_for_another_doctor_or_of_the_wrong_type(priced_catalogue):
    refusal = refused(resolver.VOUCHER_NOT_USABLE, order, 'ES', 'basic', line(TPS_M),
                      voucher='OFFVO23456')
    assert refusal.detail == resolver.INACTIVE
    refusal = refused(resolver.VOUCHER_NOT_USABLE, order, 'ES', 'basic', line(TPS_M),
                      voucher='TRYME23456', day=date(2026, 11, 11))
    assert refusal.detail == resolver.NOT_IN_FORCE
    refused(resolver.VOUCHER_DOCTOR, order, 'ES', 'basic', line(TPS_M), voucher='ANAVO23456')
    refused(resolver.VOUCHER_DOCTOR, order, 'ES', 'basic', line(TPS_M), voucher='ANAVO23456',
            doctor=BOB)
    assert holds(order('ES', 'basic', line(TPS_M), voucher='ANAVO23456',
                       doctor=ANA.upper())).voucher_taken == eur('50.00')
    refused(resolver.VOUCHER_TYPE, order, 'ES', 'basic', line(TPS_M), voucher='SUBSC23456')
    refused(resolver.VOUCHER_TYPE, buy, 'ES', 'ADV-IB', 'upfront', voucher='TRYME23456')


@pytest.mark.django_db
def test_lines_of_two_companies_or_currencies_are_refused(priced_catalogue):
    refused(resolver.TWO_COMPANIES, order, 'ES', 'basic', line(TPS_M), line(EDUCATION))
    refused(resolver.TWO_CURRENCIES, order, 'ES', 'basic', line(TPS_M), line(SPARK_USD))


@pytest.mark.django_db
@pytest.mark.parametrize('product_id', [UNLINKED, NO_RULE, TPS_AED, OLD, INACTIVE])
def test_a_product_not_offered_is_refused_by_name(priced_catalogue, product_id):
    refusal = refused(resolver.PRODUCT_NOT_OFFERED, order, 'ES', 'basic', line(TPS_M),
                      line(product_id))
    assert refusal.subject.pk == resolver.product(product_id).pk
    assert resolver.product(product_id).code in str(refusal)


@pytest.mark.django_db
def test_add_ons_are_in_the_list_amount_and_the_discount_on_the_product_only(
        priced_catalogue):
    arch, byte = resolver.add_on('ONE-ARCH'), resolver.add_on('BYTE-SYNC')
    in_house, tps = holds(order('ES', 'premium', line(IN_HOUSE, add_ons=[arch]),
                                line(TPS_M, add_ons=[byte]))).lines
    # 170.00 × 0.75 = 127.50, less 12 %: 112.20.
    assert (in_house.list_amount, in_house.discount, in_house.net) == (
        eur('127.50'), eur('15.30'), eur('112.20'))
    # 249.00 + 120.00, the 12 % on the product only.
    assert (tps.list_amount, tps.discount, tps.net) == (
        eur('369.00'), eur('29.88'), eur('339.12'))


@pytest.mark.django_db
def test_a_quantity_or_add_on_refused_by_the_line_price(priced_catalogue):
    refused(resolver.QUANTITY_REFUSED, order, 'ES', 'basic', line(TPS_M, quantity=2))
    refusal = refused(resolver.ADD_ON_REFUSED, order, 'ES', 'basic',
                      line(TPS_M, add_ons=[resolver.add_on('ONE-ARCH')]))
    assert refusal.subject.pk == resolver.product(TPS_M).pk


@pytest.mark.django_db
def test_a_country_not_served_or_no_lines_is_refused(priced_catalogue):
    basic = resolver.subscription('ES', 'basic')
    refusal = refused(resolver.COUNTRY_NOT_SERVED, resolver.price_order, 'AE', basic,
                      [line(TPS_M)], vies=resolver.VIES_NOT_APPLICABLE, day=DAY)
    assert refusal.subject == 'AE'
    refused(resolver.NO_LINES, order, 'ES', 'basic')
    with pytest.raises(ValueError):
        order('ES', 'basic', line(TPS_M), vies='maybe')


@pytest.mark.django_db
def test_without_any_billing_rule_every_order_is_refused(admin):
    pull(admin, 1, catalogue_section(rules_=False))
    refused(resolver.NO_BILLING_RULES, order, 'ES', 'basic', line(TPS_M))


@pytest.mark.django_db
def test_an_order_with_no_common_gateway_is_flagged(priced_catalogue):
    priced = holds(order('ES', 'basic', line(TPS_M), line(ODB)))
    assert priced.gateways == () and priced.needs_payment and priced.no_payment_method
    assert [(g.percent, g.vat) for g in priced.vat_groups] == [
        (Decimal('0.00'), eur('0.00')), (Decimal('21.00'), eur('16.80'))]


@pytest.mark.django_db
def test_a_uae_order_today_has_no_payment_method(admin, ae_portal):
    pull(admin, 1, catalogue_section())
    priced = holds(order('AE', 'basic', line(TPS_AED, ice=Money('80.00', 'AED'))))
    assert priced.company.code == 'AE' and priced.currency == 'AED'
    assert (priced.net_total, priced.vat_total, priced.total) == (
        Money('965.00', 'AED'), Money('48.25', 'AED'), Money('1013.25', 'AED'))
    assert priced.gateways == () and priced.no_payment_method


@pytest.mark.django_db
def test_buying_a_subscription_upfront_with_a_subscription_voucher(priced_catalogue):
    priced = holds(buy('ES', 'ADV-IB', 'upfront', voucher='SUBSC23456'))
    (only,) = priced.lines
    assert only.product is None and only.period == 'upfront'
    assert (only.list_amount, only.voucher, only.net) == (
        eur('600.00'), eur('100.00'), eur('500.00'))
    assert priced.company.code == 'ES' and (priced.vat_total, priced.total) == (
        eur('105.00'), eur('605.00'))
    assert priced.gateways == ('redsys',)
    monthly = holds(buy('ES', 'ADV-IB', 'monthly'))
    assert monthly.total == eur('72.60')
    # Rest of EU: a DE doctor with a valid VIES number is reverse charged.
    reverse = holds(buy('DE', 'BAS-EU', 'monthly', vies=resolver.VIES_VALID))
    assert reverse.lines[0].vat_text == REVERSE and reverse.total == eur('60.00')


@pytest.mark.django_db
def test_a_free_subscription_costs_nothing(priced_catalogue):
    priced = holds(buy('ES', 'BAS-IB', 'monthly'))
    assert priced.total == eur('0.00') and not priced.needs_payment
    assert priced.gateways == () and not priced.no_payment_method


@pytest.mark.django_db
def test_a_subscription_priced_0_for_the_period_costs_nothing(admin):
    section = catalogue_section()
    section['subscriptions'][0]['price'] = {'currency': 'EUR', 'monthly': '0.00'}
    pull(admin, 1, section)
    assert not resolver.subscription_by_code('BAS-IB').free
    priced = holds(buy('ES', 'BAS-IB', 'monthly'))
    assert priced.lines[0].list_amount == eur('0.00') and priced.total == eur('0.00')
    assert not priced.needs_payment and priced.gateways == ()
    assert not priced.no_payment_method


@pytest.mark.django_db
def test_a_free_subscription_billed_in_an_unknown_currency_is_refused(admin):
    section = catalogue_section()
    next(found for found in section['billing_rules']
         if found['country_group'] == IBERIA
         and found['category'] == 'subscription')['currency'] = 'GBP'
    pull(admin, 1, section)
    refused(resolver.SUBSCRIPTION_NOT_OFFERED, buy, 'ES', 'BAS-IB', 'monthly')


@pytest.mark.django_db
def test_a_subscription_not_sold_for_the_period_or_country_is_refused(priced_catalogue):
    refused(resolver.PERIOD_NOT_OFFERED, buy, 'ES', 'PRE-IB', 'upfront')
    # The UAE's subscription is in AED; the Iberian rule bills EUR.
    refused(resolver.SUBSCRIPTION_NOT_OFFERED, buy, 'ES', 'BAS-AE', 'monthly')
    refused(resolver.COUNTRY_NOT_SERVED, buy, 'AE', 'BAS-AE', 'monthly')


# --- Acceptance -------------------------------------------------------------------------------

@pytest.mark.django_db
def test_the_same_inputs_give_the_same_result(priced_catalogue):
    lines = (line(RETAINER), line(TPS_L, ice=eur('59.00')), line(RETAINER))
    first = order('ES', 'advanced', *lines, voucher='TENVO23456')
    assert first == order('ES', 'advanced', *lines, voucher='TENVO23456')


@pytest.mark.django_db
def test_pricing_writes_nothing(priced_catalogue):
    with CaptureQueriesContext(connection) as queries:
        order('ES', 'advanced', line(TPS_M, ice=eur('80.00')), line(RETAINER),
              voucher='TRYME23456')
        buy('ES', 'ADV-IB', 'upfront', voucher='SUBSC23456')
    assert queries.captured_queries
    assert not [query for query in queries.captured_queries
                if query['sql'].lstrip().split()[0].upper() in (
                    'INSERT', 'UPDATE', 'DELETE', 'REPLACE')]


@pytest.mark.django_db
def test_the_resolver_gives_every_pricing_name():
    for name in pricing.__all__:
        assert getattr(resolver, name) is getattr(pricing, name)
        assert name in resolver.__all__


# --- price_catalogue --order / --buy -----------------------------------------------------------

@pytest.mark.django_db
def test_price_catalogue_prices_an_order(priced_catalogue):
    out = run('--order', '--country', 'ES', '--tier', 'advanced', '--product', TPS_M,
              '--ice', '80.00', '--product', RETAINER, '--voucher', 'TRYME23456',
              '--on', '2026-11-13')
    assert out == (
        'order for ES advanced (Iberia): billing country ES, VIES not_applicable, on '
        '2026-11-13\n'
        '  company: ES  MyTPDSO Spain S.L.\n'
        '  currency: EUR\n'
        '  1. TPS-M  TPS branded Medium  tps_branded  x 1\n'
        '      list 249.00 EUR  discount 6.00 % -14.94 EUR  ICE -80.00 EUR  voucher '
        '-30.32 EUR  net 123.74 EUR\n'
        f'      VAT 0.00 %: {EXEMPT}\n'
        '  2. RET  Retainers  retainers  x 1\n'
        '      list 100.00 EUR  discount 0.00 % -0.00 EUR  ICE -0.00 EUR  voucher '
        '-19.68 EUR  net 80.32 EUR\n'
        f'      VAT 0.00 %: {EXEMPT}\n'
        f'  VAT 0.00 %: {EXEMPT}: on 204.06 EUR: 0.00 EUR\n'
        '  net total: 204.06 EUR\n'
        '  discount: 14.94 EUR\n'
        '  ICE credit: 80.00 EUR\n'
        '  voucher: 50.00 EUR (TRYME-23456)\n'
        '  VAT total: 0.00 EUR\n'
        '  total: 204.06 EUR\n'
        '  gateways: redsys, bank_transfer\n')


@pytest.mark.django_db
def test_price_catalogue_order_flags_and_vies(priced_catalogue):
    out = run('--order', '--country', 'ES', '--tier', 'basic', '--product', TPS_M,
              '--product', ODB, '--on', '2026-11-13')
    assert out.endswith('  gateways: none: no payment method\n')
    assert '  VAT 21.00 %: on 80.00 EUR: 16.80 EUR\n' in out
    out = run('--order', '--country', 'DE', '--subscription', 'BAS-EU', '--product', TPS_M,
              '--vies', 'valid', '--on', '2026-11-13')
    assert f'      VAT 0.00 %: {REVERSE}\n' in out and '  total: 249.00 EUR\n' in out


@pytest.mark.django_db
def test_price_catalogue_buys_a_subscription(priced_catalogue):
    out = run('--buy', 'upfront', '--country', 'ES', '--tier', 'advanced', '--voucher',
              'SUBSC-23456', '--on', '2026-11-13')
    assert out.startswith('buying upfront for ES advanced (Iberia): billing country ES')
    assert '  1. subscription ADV-IB  Advanced  upfront\n' in out
    assert '  VAT 21.00 %: on 500.00 EUR: 105.00 EUR\n' in out
    assert out.endswith('  total: 605.00 EUR\n  gateways: redsys\n')
    out = run('--buy', 'monthly', '--country', 'ES', '--tier', 'basic', '--on', '2026-11-13')
    assert out.endswith('  total: 0.00 EUR\n  gateways: nothing to pay\n')


@pytest.mark.django_db
def test_price_catalogue_order_refusals_are_errors(priced_catalogue):
    with pytest.raises(CommandError, match=r'Refused \(product_not_offered\): the product '
                                           'TPS-UNL'):
        run('--order', '--country', 'ES', '--tier', 'basic', '--product', UNLINKED)
    with pytest.raises(CommandError, match=r'Refused \(ice_not_planning\)'):
        run('--order', '--country', 'ES', '--tier', 'basic', '--product', RETAINER,
            '--ice', '80')
    with pytest.raises(CommandError, match=r'Refused \(no_lines\)'):
        run('--order', '--country', 'ES', '--tier', 'basic')
    with pytest.raises(CommandError, match='follow a --product'):
        run('--order', '--country', 'ES', '--tier', 'basic', '--quantity', '2',
            '--product', TPS_M)
    with pytest.raises(CommandError, match='--buy takes no --product'):
        run('--buy', 'monthly', '--country', 'ES', '--tier', 'basic', '--product', TPS_M)
    with pytest.raises(CommandError, match='--country'):
        run('--order', '--tier', 'basic', '--product', TPS_M)
    with pytest.raises(CommandError, match='go with --order or --buy'):
        run('--country', 'ES', '--tier', 'basic', '--vies', 'valid')
    with pytest.raises(CommandError, match='several go with --order'):
        run('--country', 'ES', '--tier', 'basic', '--product', TPS_M, '--product', ODB)
    with pytest.raises(CommandError, match='never negative'):
        run('--order', '--country', 'ES', '--tier', 'basic', '--product', TPS_M,
            '--ice', '-80')
    with pytest.raises(CommandError, match='Not an ICE credit'):
        run('--order', '--country', 'ES', '--tier', 'basic', '--product', TPS_M,
            '--ice', 'lots')


@pytest.mark.django_db
def test_price_catalogue_one_line_is_unchanged(priced_catalogue):
    out = run('--product', TPS_M, '--country', 'ES', '--tier', 'advanced')
    assert out.endswith('  line price: 234.06 EUR\n')
