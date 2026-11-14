"""The one function that prices an order (epic-catalogue entry 12, AD-7, AD-16; user
decisions 2026-11-13). Ordering, subscriptions and payments call it through
`catalogue.resolver` (which exposes every name here) and never compute a price, a VAT
amount or a voucher share of their own.

`price_order(billing_country, subscription, lines, *, vies, voucher=None, doctor=None,
day=None)` prices a service order; `price_subscription(billing_country, subscription,
period, *, vies, voucher=None, doctor=None, day=None)` prices the purchase of a
subscription (`monthly` or `upfront`). Both return a `PricedOrder` or raise
`OrderRefused` with a stable `reason` (the `REFUSALS` codes) and the object it names
(`subject`). They read the snapshot tables only: nothing is written or reserved.

Inputs:

- `billing_country`: the fiscal entity's country once it exists, the profile's country
  until then (an alpha-2 code). It decides the country group, so the billing rules, and
  the reverse charge. A country this region does not serve has no group: refused.
- `subscription`: the doctor's subscription (a `Subscription`, standard or manual).
- `lines`: `OrderLine`s (a product, its quantity, its add-ons and an optional ICE credit
  as `Money`).
- `vies`: the VIES status of the doctor's VAT number, one of `VIES_STATUSES`.
- `voucher`: a `Voucher` row, or None; `doctor`: the doctor's client UUID (a voucher
  that names a doctor is refused without it, or for another one); `day`: the date in
  Madrid time (today when None).

Each line, in this order (AD-16):

1. its list amount: net price × quantity with its add-ons (`resolver.price` without the
   discount), and its price after the subscription discount (`resolver.price`); the
   discount amount is the difference;
2. the ICE credit, on the one line the caller names: a planning line only
   (`PLANNING_CATEGORIES`), in the order's currency; what exceeds the line is lost;
3. the voucher, spread over the lines in proportion to each line's amount after the
   discount and the ICE credit: each share rounded down to the cent, the remaining cents
   given one by one to the lines in order, never past a line's amount; what exceeds the
   order is lost;
4. the line's net amount (never below 0.00), and its VAT rate and text from its rule.

VAT (AD-7): the reverse charge applies when VIES is `valid`, the billing country is
another than the billing company's and the rule has a reverse-charge text: rate 0 with
that text. Otherwise the rule's rate is charged (with its exemption text at a rate of 0).
VAT is computed per order: one amount per (rate, text), on the sum of the lines at that
rate, rounded half up to the cent.

One order has one billing company and one currency (refused otherwise, never split). The
gateways are those every line's rule allows and the region profile has, in the first
line's rule order; an order of 0.00 needs none; an order above 0 with no common gateway
is priced and flagged `no_payment_method`.

"Offered" is what `resolver.offers_under(subscription, day, country)` means; with no
billing rule typed at all there is no company, so every order is refused.
"""
import uuid
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings

from mytp_core.contracts import AddOnRefused, QuantityRefused, line_price
from mytp_core.money import CURRENCIES, Money

# catalogue.resolver reads this module's names lazily (its `__getattr__`), so importing it
# here is not circular.
from . import resolver
from .models import Product, Voucher

__all__ = ['OrderLine', 'OrderRefused', 'PLANNING_CATEGORIES', 'PricedLine', 'PricedOrder',
           'REFUSALS', 'SUBSCRIPTION_PERIODS', 'VatGroup', 'VIES_INVALID',
           'VIES_NOT_APPLICABLE', 'VIES_PENDING', 'VIES_STATUSES', 'VIES_VALID',
           'price_order', 'price_subscription', 'MONTHLY', 'UPFRONT',
           # The refusal codes (`OrderRefused.reason`).
           'NO_LINES', 'COUNTRY_NOT_SERVED', 'NO_BILLING_RULES', 'PRODUCT_NOT_OFFERED',
           'QUANTITY_REFUSED', 'ADD_ON_REFUSED', 'TWO_COMPANIES', 'TWO_CURRENCIES',
           'ICE_TWICE', 'ICE_NOT_PLANNING', 'ICE_CURRENCY', 'VOUCHER_NOT_USABLE',
           'VOUCHER_TYPE', 'VOUCHER_CURRENCY', 'VOUCHER_DOCTOR', 'SUBSCRIPTION_NOT_OFFERED',
           'PERIOD_NOT_OFFERED']

VIES_VALID, VIES_INVALID = 'valid', 'invalid'
VIES_PENDING, VIES_NOT_APPLICABLE = 'pending', 'not_applicable'
VIES_STATUSES = (VIES_VALID, VIES_INVALID, VIES_PENDING, VIES_NOT_APPLICABLE)

# The product categories that may take an ICE credit (user decision 2026-11-13).
PLANNING_CATEGORIES = (Product.TPS_BRANDED, Product.TPS_IN_HOUSE, 'virtuaortho_one',
                       'virtuaortho_digitaltwin')

MONTHLY, UPFRONT = 'monthly', 'upfront'
SUBSCRIPTION_PERIODS = (MONTHLY, UPFRONT)

# Why an order is refused: `OrderRefused.reason`.
NO_LINES = 'no_lines'
COUNTRY_NOT_SERVED = 'country_not_served'
NO_BILLING_RULES = 'no_billing_rules'
PRODUCT_NOT_OFFERED = 'product_not_offered'
QUANTITY_REFUSED = 'quantity_refused'
ADD_ON_REFUSED = 'add_on_refused'
TWO_COMPANIES = 'two_companies'
TWO_CURRENCIES = 'two_currencies'
ICE_TWICE = 'ice_twice'
ICE_NOT_PLANNING = 'ice_not_planning'
ICE_CURRENCY = 'ice_currency'
VOUCHER_NOT_USABLE = 'voucher_not_usable'
VOUCHER_TYPE = 'voucher_type'
VOUCHER_CURRENCY = 'voucher_currency'
VOUCHER_DOCTOR = 'voucher_doctor'
SUBSCRIPTION_NOT_OFFERED = 'subscription_not_offered'
PERIOD_NOT_OFFERED = 'period_not_offered'
REFUSALS = (NO_LINES, COUNTRY_NOT_SERVED, NO_BILLING_RULES, PRODUCT_NOT_OFFERED,
            QUANTITY_REFUSED, ADD_ON_REFUSED, TWO_COMPANIES, TWO_CURRENCIES, ICE_TWICE,
            ICE_NOT_PLANNING, ICE_CURRENCY, VOUCHER_NOT_USABLE, VOUCHER_TYPE,
            VOUCHER_CURRENCY, VOUCHER_DOCTOR, SUBSCRIPTION_NOT_OFFERED, PERIOD_NOT_OFFERED)


class OrderRefused(ValueError):
    """An order that cannot be priced: `reason` (one of `REFUSALS`), the object it names
    (`subject`: a product, a voucher, a subscription, a country code, or None) and,
    for `voucher_not_usable`, the voucher's state (`detail`)."""

    def __init__(self, reason, message, subject=None, detail=None):
        super().__init__(message)
        self.reason, self.subject, self.detail = reason, subject, detail


@dataclass(frozen=True)
class OrderLine:
    """One line asked for: a product (a `Product`), its quantity, its add-ons (`AddOn`
    rows) and the ICE credit it takes (`Money`, or None)."""
    product: Product
    quantity: int = 1
    add_ons: tuple = ()
    ice: Money | None = None


@dataclass(frozen=True)
class PricedLine:
    """One priced line: `product` (None for a subscription's own price, then `period`
    says which), its quantity, its list amount (after add-ons and quantity, before the
    discount), the discount percent and amount, the ICE credit and voucher share taken,
    its net amount and its VAT percent and text ('' when none)."""
    product: Product | None
    quantity: int
    list_amount: Money
    discount_percent: Decimal
    discount: Money
    ice: Money
    voucher: Money
    net: Money
    vat_percent: Decimal
    vat_text: str
    period: str | None = None


@dataclass(frozen=True)
class VatGroup:
    """The VAT of the lines at one rate and text: their summed net `base` and `vat`."""
    percent: Decimal
    text: str
    base: Money
    vat: Money


@dataclass(frozen=True)
class PricedOrder:
    """A priced order: its billing `company` (a `BillingCompany`), `currency`, `lines`,
    `vat_groups`, the totals (`net_total` + `vat_total` = `total`; `discount_total`,
    `ice_total`, `voucher_taken`), the `gateways` (in order; empty when nothing is to
    pay), `needs_payment` (total above 0) and `no_payment_method` (it needs payment and
    no gateway is common to its lines and the region)."""
    company: object
    currency: str
    lines: tuple
    vat_groups: tuple
    net_total: Money
    discount_total: Money
    ice_total: Money
    voucher_taken: Money
    vat_total: Money
    total: Money
    gateways: tuple
    needs_payment: bool
    no_payment_method: bool


@dataclass
class _Line:
    """A line during pricing: what the shared arithmetic needs."""
    product: object
    quantity: int
    list_amount: Money
    discount_percent: Decimal
    discounted: Money
    rule: object
    ice: Money | None = None
    period: str | None = None


def _group(billing_country):
    group = resolver.country_group(billing_country)
    if group is None:
        raise OrderRefused(COUNTRY_NOT_SERVED,
                           f'the country {billing_country!r} is not served by this region',
                           subject=billing_country)
    if not resolver.has_billing_rules():
        raise OrderRefused(NO_BILLING_RULES,
                           'the catalogue has no billing rules: no company bills')
    return group


def _check_vies(vies):
    if vies not in VIES_STATUSES:
        raise ValueError(f'vies is one of {", ".join(VIES_STATUSES)}, not {vies!r}')


def price_order(billing_country, subscription, lines, *, vies, voucher=None, doctor=None,
                day=None):
    """The `PricedOrder` of a service order, or `OrderRefused`: see the module
    docstring. A voucher must be of type `try_me`."""
    _check_vies(vies)
    day = day or resolver.today()
    lines = list(lines)
    if not lines:
        raise OrderRefused(NO_LINES, 'the order has no lines')
    group = _group(billing_country)
    offered = {found.pk for found, _price in resolver.offers_under(
        subscription, day, country=billing_country)}
    priced = []
    for asked in lines:
        product = asked.product
        if product.pk not in offered:
            raise OrderRefused(PRODUCT_NOT_OFFERED,
                               f'the product {product.code or product.pk} is not offered '
                               'to this doctor', subject=product)
        add_ons = list(asked.add_ons)
        try:
            discounted = resolver.price(product, subscription, add_ons, asked.quantity)
        except QuantityRefused as refusal:
            raise OrderRefused(QUANTITY_REFUSED, f'the product {product.code or product.pk}:'
                               f' {refusal}', subject=product) from None
        except AddOnRefused as refusal:
            raise OrderRefused(ADD_ON_REFUSED, str(refusal), subject=product) from None
        list_amount = line_price(product.net_price, quantity=asked.quantity,
                                 **resolver.add_on_terms(add_ons))
        priced.append(_Line(product, asked.quantity, list_amount,
                            resolver.discount_percent(product, subscription), discounted,
                            resolver.billing_rule_for(group, product.category),
                            ice=asked.ice))
    return _price(billing_country, priced, vies, voucher, Voucher.TRY_ME, doctor, day)


def price_subscription(billing_country, subscription, period, *, vies, voucher=None,
                       doctor=None, day=None):
    """The `PricedOrder` of buying a subscription for a `period` (`monthly` or
    `upfront`: its monthly price, or a year upfront), or `OrderRefused`. Billed by the
    `subscription` category rule of the billing country's group (company, VAT, gateways
    and currency, as a product's); a voucher must be of type `subscription`. A free
    subscription is priced at 0.00 in the rule's currency."""
    _check_vies(vies)
    if period not in SUBSCRIPTION_PERIODS:
        raise ValueError(f'period is one of {", ".join(SUBSCRIPTION_PERIODS)}, '
                         f'not {period!r}')
    day = day or resolver.today()
    group = _group(billing_country)
    rule = resolver.billing_rule_for(group, resolver.SUBSCRIPTION_CATEGORY)
    name = subscription.code or subscription.pk
    if (not subscription.active or rule is None or not rule.available
            or rule.currency not in CURRENCIES
            or (not subscription.free and subscription.price_currency != rule.currency)):
        raise OrderRefused(SUBSCRIPTION_NOT_OFFERED,
                           f'the subscription {name} is not offered to this doctor',
                           subject=subscription)
    if subscription.free:
        amount = Money.zero(rule.currency)
    else:
        amount = (subscription.monthly_price if period == MONTHLY
                  else subscription.upfront_price)
        if amount is None:
            raise OrderRefused(PERIOD_NOT_OFFERED,
                               f'the subscription {name} is not sold {period}',
                               subject=subscription)
    line = _Line(None, 1, amount, Decimal('0.00'), amount, rule, period=period)
    return _price(billing_country, [line], vies, voucher, Voucher.SUBSCRIPTION, doctor, day)


def _price(billing_country, lines, vies, voucher, voucher_type, doctor, day):
    """The shared arithmetic: one company and currency, the ICE credit, the voucher, VAT
    and the gateways."""
    companies = {line.rule.company_id for line in lines}
    if len(companies) > 1:
        raise OrderRefused(TWO_COMPANIES, 'the lines are billed by two companies')
    currencies = {line.list_amount.currency for line in lines}
    if len(currencies) > 1:
        raise OrderRefused(TWO_CURRENCIES, 'the lines are in two currencies')
    currency = currencies.pop()
    company = lines[0].rule.company
    zero = Money.zero(currency)

    # The ICE credit, on the one line the caller names.
    credited = [line for line in lines if line.ice is not None]
    if len(credited) > 1:
        raise OrderRefused(ICE_TWICE, 'only one line may take the ICE credit')
    bases = []
    for line in lines:
        taken = zero
        if line.ice is not None:
            if line.product is None or line.product.category not in PLANNING_CATEGORIES:
                raise OrderRefused(ICE_NOT_PLANNING, 'only a planning line takes the ICE '
                                   'credit', subject=line.product)
            if line.ice.currency != currency:
                raise OrderRefused(ICE_CURRENCY, f'the ICE credit is in {line.ice.currency}'
                                   f', the order in {currency}', subject=line.product)
            if line.ice.amount < 0:
                raise ValueError('an ICE credit is never negative')
            taken = min(line.ice, line.discounted)
        line.ice = taken
        bases.append(line.discounted - taken)

    shares = [zero] * len(lines)
    if voucher is not None:
        _check_voucher(voucher, voucher_type, currency, doctor, day)
        shares = _spread(min(voucher.money, sum(bases, zero)), bases)

    country = resolver.normal_country(billing_country)
    priced, groups = [], {}
    for line, base, share in zip(lines, bases, shares):
        rule = line.rule
        if (vies == VIES_VALID and country != (rule.company.country or '').upper()
                and rule.reverse_charge_text):
            percent, text = Decimal('0.00'), rule.reverse_charge_text
        else:
            percent = rule.vat_percent
            text = rule.exemption_text if rule.exempt else ''
        net = base - share
        priced.append(PricedLine(
            product=line.product, quantity=line.quantity, list_amount=line.list_amount,
            discount_percent=line.discount_percent,
            discount=line.list_amount - line.discounted, ice=line.ice, voucher=share,
            net=net, vat_percent=percent, vat_text=text, period=line.period))
        groups[(percent, text)] = groups.get((percent, text), zero) + net
    vat_groups = tuple(VatGroup(percent, text, base, base * (percent / Decimal(100)))
                       for (percent, text), base in groups.items())

    net_total = sum((line.net for line in priced), zero)
    vat_total = sum((group.vat for group in vat_groups), zero)
    total = net_total + vat_total
    needs_payment = total > zero
    gateways = ()
    if needs_payment:
        common = set(settings.REGION_PROFILE.gateways)
        for line in lines:
            common &= set(line.rule.known_gateways)
        gateways = tuple(gateway for gateway in lines[0].rule.known_gateways
                         if gateway in common)
    return PricedOrder(
        company=company, currency=currency, lines=tuple(priced), vat_groups=vat_groups,
        net_total=net_total,
        discount_total=sum((line.discount for line in priced), zero),
        ice_total=sum((line.ice for line in priced), zero),
        voucher_taken=sum(shares, zero), vat_total=vat_total, total=total,
        gateways=gateways, needs_payment=needs_payment,
        no_payment_method=needs_payment and not gateways)


def _check_voucher(voucher, voucher_type, currency, doctor, day):
    state = resolver.voucher_state(voucher, day)
    code = voucher.display_code
    if state != resolver.USABLE:
        raise OrderRefused(VOUCHER_NOT_USABLE, f'the voucher {code} is not usable: {state}',
                           subject=voucher, detail=state)
    if voucher.type != voucher_type:
        raise OrderRefused(VOUCHER_TYPE, f'the voucher {code} is a {voucher.type} voucher, '
                           f'not a {voucher_type} one', subject=voucher)
    if voucher.money.currency != currency:
        raise OrderRefused(VOUCHER_CURRENCY, f'the voucher {code} is in '
                           f'{voucher.money.currency}, the order in {currency}',
                           subject=voucher)
    if voucher.client is not None and _uuid(doctor) != voucher.client:
        raise OrderRefused(VOUCHER_DOCTOR, f'the voucher {code} is for another doctor',
                           subject=voucher)


def _uuid(value):
    """`value` (a UUID or its string) as a UUID, or None."""
    try:
        return None if value is None else uuid.UUID(str(value))
    except ValueError:
        return None


def _spread(taken, bases):
    """`taken` (at most the sum of `bases`) in shares proportional to `bases`: each
    rounded down to the cent, the remaining cents one by one to the lines in order, never
    past a line's base."""
    currency = taken.currency
    cents = [int(base.amount * 100) for base in bases]
    whole, total = int(taken.amount * 100), sum(cents)
    if total == 0 or whole <= 0:
        return [Money.zero(currency)] * len(bases)
    shares = [whole * part // total for part in cents]
    left = whole - sum(shares)
    while left > 0:
        for index, part in enumerate(cents):
            if left and shares[index] < part:
                shares[index] += 1
                left -= 1
    return [Money(Decimal(share).scaleb(-2), currency) for share in shares]
