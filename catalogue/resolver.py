"""The only code that prices (epic-catalogue entry 2, AD-6, AD-16): what a doctor of a
country is offered under a subscription tier, from the catalogue the admin site last sent
(catalogue.models).

`offers(country, tier)`:

1. the country's group: the active group that lists `country` (ISO 3166-1 alpha-2), else
   the active catch-all group ("Rest of world"); none: no offers;
2. the active standard subscription of `tier` in that group, the default of that tier
   and group (`subscription`); none: no offers;
3. the products that subscription gives access to (`offers_under`): the active products
   linked to it that are within their validity dates today (`valid_from` / `valid_to`,
   both optional and inclusive, in Madrid time whatever the region: `today`), by name,
   each with its price (`unit_price`): ``net_price × (1 − discount / 100)`` rounded half
   up to cents (mytp-core's `Money`), the discount being the subscription's for the
   product's category (`discount_percent`), 0 for a category it does not discount and
   for `uncategorised`.

A product that is inactive or outside its validity dates is not offered for new orders,
but stays readable by its id (`product`), for the orders that refer to it.

Subscriptions in full (epic-catalogue entry 8, user decisions 2026-11-11):

- A subscription is standard or manual. `subscription(country, tier)` returns the
  standard one only. A manual one (assigned to a doctor by an administrator:
  epic-subscriptions) is never returned for a country and a tier: it is read by its id
  (`subscription_by_id`, whatever its state) and priced with its own products and
  discounts (`offers_under`, `price`).
- A subscription gives access to its linked products only (`linked`). A product linked
  to no subscription is offered to nobody. A subscription from a snapshot without links
  (an admin site before mytp-core 0.22.0) gives access to every product, as before.
- `discount_percent(product, subscription)`: the subscription's discount for the
  product's category; from a snapshot without the map, its TPS branded discount on a
  `tps_branded` product, its TPS in-house discount on a `tps_in_house` one, 0 otherwise.
- `calls(product, subscription)`: the calls a product grants under a subscription: the
  product's calls plus the subscription's extra calls for its category, each of the
  subscription's length when it gives one (3 calls of 25 minutes are 4 of 35 under
  "+1 call, 35 minutes"); None for a product that grants no calls.
- A subscription's own price is on its row (`Subscription.monthly_price`,
  `upfront_price`, `free`): stored and readable here; charging it is epic-subscriptions.

Add-ons, quantities and costs (epic-catalogue entry 6, user decisions 2026-11-10):

- `add_ons(product)`: the active add-ons offered on a product: its category is among
  the add-on's, its size among the add-on's sizes when it lists any (Junior: size
  medium), and a fixed amount is in the product's currency.
- `price(product, subscription, add_ons=(), quantity=1)`: the price of a line,

      net price × quantity × (1 + percent / 100) for each percentage add-on
                × (1 − subscription discount / 100)  +  the fixed add-on amounts

  computed exactly and rounded half up to cents once, at the end (mytp-core's
  `line_price`): a 100.00 TPS in-house with a 10 % discount and one arch (−25 %) is
  67.50; Byte Sync (+120.00) makes any line 120.00 higher, the discount applying to the
  product only. A fixed amount is added once per line, whatever the quantity. The
  quantity is within the product's range when it is priced per unit (Spark on Demand:
  4 to 9 aligners), and 1 otherwise: anything else is `QuantityRefused`. An add-on that
  is inactive, not offered on the product or chosen twice is `AddOnRefused`; a product
  the subscription does not give access to is `ProductRefused` (entry 8). `offers`
  lists a per-unit product at the price of one unit (`unit_price`).
- `cost(product, day)`: what the product costs the company on a date, from its cost
  links and the provider costs the admin site last sent: the sum of the links that are
  not monthly, each the provider product's cost on that date × the quantity. When a
  linked provider product has no cost on that date (a gap, not sent, or another
  currency than the product's) the sum is partial: `complete` is False and `missing`
  names it; show it with its `warning`, never as the cost. The monthly links are apart
  (`monthly`), never in the sum. A product with no link to sum (none, or only monthly
  ones) is `empty`: it has no cost to show, and its total of 0.00 is not a known cost
  of zero. The date applies to the provider costs only: the links are those the admin
  site last sent, the ones in force now, so for a past date an ended link is left out
  and a link added today counts (a later entry that needs a true past cost reads the
  order line's own copy, AD-6).

Billing companies and billing rules (epic-catalogue entry 9, AD-6, AD-7, AD-16; user
decisions 2026-11-11). This module is the only code that decides which company bills,
with what VAT rate or exemption text and through which gateways:

- `billing_rule(country, category)`: the active rule of the country's group and a
  product category, or None: its `company` (a `BillingCompany`), `vat_percent`,
  `exemption_text` (what is shown when the rate is 0), `reverse_charge_text`,
  `known_gateways` (in order of preference; may be empty) and `currency`. ES and PT
  resolve to the rule of Iberia, DE to the one of Rest of EU, US to the one of Rest of
  world.
- Once the catalogue has rules (`has_billing_rules`), `offers_under` and `offers` leave
  out a product whose country group and category have no active rule, whose rule is
  marked unavailable, or whose currency is not the rule's (an ES doctor is offered the
  EUR product, not its USD or AED clone). While the catalogue has no rule at all (an
  admin site before mytp-core 0.23.0, or no rule typed yet), every linked product is
  offered, in every currency, as before, and there is no billing data.
- `subscription_rule(subscription)`: the rule of a subscription's own price: the
  `subscription` category rule of the subscription's country group, or None.
- `price` is unchanged: it prices a line and does not ask whether the product is
  offered. VAT amounts, the VIES decision and the cart come with entry 12, which reads
  the rule from here.

Vouchers (epic-catalogue entry 10, AD-20; user decisions 2026-11-12):

- `voucher_by_code(code)`: the voucher of this region with that code, typed with or
  without the hyphen and in any case (``k7qm2-xh9tr``), whatever its state, or None.
- `voucher_state(voucher, day)`: whether it is usable on a day (Madrid time; today when
  None): `usable`, or why not: `inactive`, `not_in_force` (outside its dates, both
  included), `unknown_type` or `unknown_currency` (what this release cannot honour).
  `voucher_usable` says yes or no. Whether this doctor may use it (its `client`), and
  whether it was used already, are epic-subscriptions'; applying it to a price is
  entry 12's.

Prices are net: no VAT amount is computed here, and no voucher is applied to a price.
"""
import re
import uuid
import zoneinfo
from decimal import Decimal

from django.utils import timezone

from mytp_core.contracts import (AddOnRefused, QuantityRefused, add_on_applies,
                                 billing_offers, calls_under, check_quantity, line_price,
                                 link_labels, normalize_voucher_code,
                                 subscription_discount, subscription_offers, sum_costs)
from mytp_core.money import Money

from .models import (AddOn, BillingCompany, BillingRule, CountryGroup, Product,
                     ProviderProduct, Subscription, Voucher)

__all__ = ['AddOnRefused', 'ProductRefused', 'QuantityRefused', 'add_on', 'add_ons',
           'billing_company', 'billing_rule', 'billing_rule_for', 'calls', 'cost',
           'country_group', 'discount_percent', 'has_billing_rules', 'linked',
           'normal_category', 'normal_country', 'normal_tier', 'offers', 'offers_under',
           'price', 'product', 'subscription', 'subscription_by_code',
           'subscription_by_id', 'subscription_rule', 'today', 'unit_price',
           'voucher_by_code', 'voucher_state', 'voucher_usable', 'VOUCHER_STATES']

# The product category whose rule bills a subscription's own price (user decision
# 2026-11-11).
SUBSCRIPTION_CATEGORY = 'subscription'


class ProductRefused(ValueError):
    """A product the subscription does not give access to: it is not among the
    subscription's linked products."""

_COUNTRY_RE = re.compile(r'[A-Z]{2}')

# The validity dates of a product are days in Madrid time, in every region (user decision
# 2026-11-10).
VALIDITY_TIME_ZONE = zoneinfo.ZoneInfo('Europe/Madrid')


def today(now=None):
    """Today's date in Madrid time (of `now`, an aware datetime, when given)."""
    return (now or timezone.now()).astimezone(VALIDITY_TIME_ZONE).date()


def normal_country(country):
    """`country` stripped and uppercased ("es " is ES)."""
    return (country or '').strip().upper()


def normal_tier(tier):
    """`tier` stripped and lowercased ("Basic" is basic)."""
    return (tier or '').strip().lower()


def country_group(country):
    """The active country group of `country` (an alpha-2 code), or None."""
    country = normal_country(country)
    if not _COUNTRY_RE.fullmatch(country):
        return None
    groups = list(CountryGroup.objects.filter(active=True))
    for group in groups:
        if not group.catch_all and country in group.countries:
            return group
    return next((group for group in groups if group.catch_all), None)


def normal_category(category):
    """`category` stripped and lowercased; a code of an admin site before entry 5 as its
    new one (`tps` is tps_branded)."""
    category = (category or '').strip().lower()
    return Product.LEGACY_CATEGORIES.get(category, category)


def has_billing_rules():
    """Whether the catalogue has any billing rule, in any state: from then on only what
    has an active, available rule is offered. False for a snapshot of an admin site
    before mytp-core 0.23.0, or of one where no rule was typed yet."""
    return BillingRule.objects.exists()


def billing_rule_for(group, category):
    """The active billing rule of a country group (a `CountryGroup` or its id) and a
    product category, with its company loaded, or None."""
    group_id = getattr(group, 'pk', group)
    if group_id is None:
        return None
    return (BillingRule.objects.select_related('company')
            .filter(active=True, country_group_id=group_id,
                    category=normal_category(category)).first())


def billing_rule(country, category):
    """The billing rule for a doctor of `country` (an alpha-2 code) and a product
    `category`: the active rule of the country's group and that category, or None (no
    group for the country, or no rule for the pair). It gives the company that bills
    (`company`), the VAT rate (`vat_percent`) or, for a rate of 0, the exemption text
    (`exemption_text`), the reverse-charge text, the gateways in order
    (`known_gateways`), the currency and whether the category is `available` there."""
    group = country_group(country)
    return None if group is None else billing_rule_for(group, category)


def subscription_rule(subscription):
    """The billing rule of a subscription's own price: the `subscription` category rule
    of the subscription's country group, or None when that pair has no active rule."""
    return billing_rule_for(subscription.country_group_id, SUBSCRIPTION_CATEGORY)


def billing_company(code):
    """The billing company of this code ("es" is ES) whatever its state, or None."""
    code = (code or '').strip().upper()
    if not code:
        return None
    return BillingCompany.objects.filter(code=code).order_by('-active', 'id').first()


def subscription(country, tier):
    """The active standard subscription of `tier` for `country` (the default of that
    tier and the country's group), or None. Never a manual one."""
    group = country_group(country)
    if group is None:
        return None
    return Subscription.objects.filter(active=True, manual=False, tier=normal_tier(tier),
                                       country_group=group).first()


def subscription_by_id(subscription_id):
    """The subscription of this id (a UUID or its string), standard or manual, whatever
    its state, or None: how a manual subscription is read (who has it is
    epic-subscriptions)."""
    try:
        subscription_id = uuid.UUID(str(subscription_id))
    except ValueError:
        return None
    return Subscription.objects.filter(pk=subscription_id).first()


def subscription_by_code(code):
    """The subscription of this code ("adv-ib" is ADV-IB), standard or manual, whatever
    its state, or None."""
    code = (code or '').strip().upper()
    return Subscription.objects.filter(code=code).first() if code else None


def linked(product, subscription):
    """Whether the subscription gives access to `product`: whether it is among its
    linked products; every product for a subscription without links (a snapshot of an
    admin site before mytp-core 0.22.0). It says nothing of either's state."""
    return subscription_offers(product.pk, subscription.products)


def discount_percent(product, subscription):
    """The subscription's discount (a Decimal percentage) on `product`: its discount for
    the product's category (0 for a category it does not discount); for a subscription
    without the map (a snapshot of an admin site before mytp-core 0.22.0), its TPS
    branded discount on a `tps_branded` product, its TPS in-house discount on a
    `tps_in_house` one, 0 on any other category. Always 0 on `uncategorised`."""
    if product.category == Product.UNCATEGORISED:
        return Decimal(0)
    # The stored map holds texts ("12.00"); mytp-core's helper takes Decimals.
    discounts = (None if subscription.discounts is None else
                 {category: Decimal(percent)
                  for category, percent in subscription.discounts.items()})
    return subscription_discount(
        product.category, discounts=discounts,
        tps_branded=subscription.tps_discount_percent,
        tps_in_house=subscription.tps_in_house_discount_percent)


def calls(product, subscription):
    """The calls `product` grants under the subscription, as ``(count, minutes)``: the
    product's calls plus the subscription's extra calls for the product's category, each
    of the subscription's length when it gives one, else of the product's; None for a
    product that grants no calls (the extras add to a product's calls, they grant none
    by themselves)."""
    extras = (subscription.call_extras or {}).get(product.category) or {}
    return calls_under(product.calls, product.call_minutes, extras.get('extra_calls', 0),
                       extras.get('minutes'))


def unit_price(product, subscription):
    """The price (`Money`) of one unit of a product under a subscription, without
    add-ons: what `offers` lists. For a product that is not priced per unit it is its
    price."""
    return line_price(product.net_price,
                      discount_percent=discount_percent(product, subscription))


def _applies(add_on, product):
    return add_on_applies(
        categories=add_on.categories, sizes=add_on.sizes, currency=add_on.currency or None,
        product_category=product.category, product_size=product.size or None,
        product_currency=product.currency)


def add_ons(product):
    """The active add-ons offered on `product`, by code: see the module docstring."""
    return [add_on for add_on in AddOn.objects.filter(active=True).order_by('code', 'id')
            if _applies(add_on, product)]


def add_on(code):
    """The add-on of this code ("byte-sync" is BYTE-SYNC) whatever its state, or None."""
    return AddOn.objects.filter(code=(code or '').strip().upper()).first()


def price(product, subscription, add_ons=(), quantity=1):
    """The price (`Money`) of a line of `product` under a subscription, with the chosen
    `add_ons` (AddOn rows) and `quantity`: see the module docstring. `QuantityRefused`
    for a quantity the product is not sold in; `AddOnRefused` for an add-on that is
    inactive, not offered on the product or chosen twice; `ProductRefused` for a product
    the subscription does not give access to (all are ValueErrors). As before, it does
    not ask whether the product or the subscription is active."""
    if not linked(product, subscription):
        raise ProductRefused(f'the product {product.pk} is not offered under the '
                             f'subscription {subscription.pk}')
    check_quantity(quantity, product.min_quantity, product.max_quantity)
    add_ons = list(add_ons)
    if len({add_on.pk for add_on in add_ons}) != len(add_ons):
        raise AddOnRefused('an add-on is chosen twice')
    for chosen in add_ons:
        if not (chosen.active and _applies(chosen, product)):
            raise AddOnRefused(f'the add-on {chosen.code} is not offered on the product '
                               f'{product.pk}')
    return line_price(
        product.net_price, quantity=quantity,
        discount_percent=discount_percent(product, subscription),
        percents=[chosen.percent for chosen in add_ons if chosen.percent is not None],
        amounts=[chosen.amount_money for chosen in add_ons if chosen.amount is not None])


def cost(product, day=None):
    """What `product` costs on `day` (a date; today in Madrid time when None), as
    mytp-core's `ProductCost`: `total` (`Money`), `complete`, `missing`, `monthly`,
    `warning`, `empty`: see the module docstring. `day` chooses the provider costs only:
    the links are the product's current ones, whatever the date. A link is named by its
    provider product's code (with its id when two links of the product share a code:
    mytp-core's `link_labels`), or by its id when the admin site did not send that
    provider product."""
    day = day or today()
    links = product.cost_links or []
    listed = {str(item.pk): item for item in ProviderProduct.objects.filter(
        pk__in=[link['provider_product'] for link in links]).prefetch_related('costs')}
    items = [listed.get(link['provider_product']) for link in links]
    labels = link_labels([(item and item.code, link['provider_product'])
                          for link, item in zip(links, items)])
    entries = []
    for label, link, item in zip(labels, links, items):
        held = None if item is None else next(
            (range_ for range_ in item.costs.all() if range_.holds(day)), None)
        entries.append((
            label, link['quantity'], link['monthly'],
            None if held is None else Money(held.discounted, item.currency)))
    return sum_costs(entries, product.currency)


def product(product_id):
    """The product of this id (a UUID or its string) whatever its state (inactive,
    outside its validity dates), or None: for reading the product of a past order, never
    for offering it."""
    try:
        product_id = uuid.UUID(str(product_id))
    except ValueError:
        return None
    return Product.objects.filter(pk=product_id).first()


def offers_under(subscription, day=None, country=None):
    """[(product, Money)] a subscription (standard or manual) offers on `day` (a date in
    Madrid time; today when None): the active products linked to it (every active
    product when it has no links: a snapshot of an admin site before mytp-core 0.22.0)
    that are within their validity dates, by name; [] for an inactive subscription. The
    price is `unit_price`: of one unit for a product priced per unit, without add-ons.

    Once the catalogue has billing rules (entry 9), a product is offered only when its
    category has an active, available rule in the doctor's country group and the product
    is in that rule's currency. The group is the one of `country` (the doctor's, an
    alpha-2 code) when given, else the subscription's own (for a standard subscription
    they are the same). Without any rule every product above is offered, as before."""
    if not subscription.active:
        return []
    day = day or today()
    products = Product.objects.filter(active=True).order_by('name', 'id')
    if subscription.products is not None:
        products = products.filter(pk__in=subscription.products)
    products = [offered for offered in products if offered.offered_on(day)]
    if has_billing_rules():
        group = (subscription.country_group_id if country is None
                 else getattr(country_group(country), 'pk', None))
        rules = {rule.category: rule for rule in BillingRule.objects.filter(
            active=True, country_group_id=group)} if group is not None else {}
        products = [
            offered for offered in products
            if billing_offers(True, *_rule_values(rules.get(offered.category)),
                              offered.currency)]
    return [(offered, unit_price(offered, subscription)) for offered in products]


def _rule_values(rule):
    """(available, currency) of a rule, or (None, None) without one: what mytp-core's
    `billing_offers` takes."""
    return (None, None) if rule is None else (rule.available, rule.currency)


def offers(country, tier, day=None):
    """[(product, Money)] for a doctor of `country` under `tier` on `day` (a date in
    Madrid time; today when None): what the standard subscription of that tier and the
    country's group offers (`offers_under`); [] when none applies to the country."""
    applies = subscription(country, tier)
    if applies is None:
        return []
    return offers_under(applies, day, country=country)


# --- Vouchers (epic-catalogue entry 10) ----------------------------------------------------

USABLE, INACTIVE, NOT_IN_FORCE = 'usable', 'inactive', 'not_in_force'
UNKNOWN_TYPE, UNKNOWN_CURRENCY = 'unknown_type', 'unknown_currency'
VOUCHER_STATES = (USABLE, INACTIVE, NOT_IN_FORCE, UNKNOWN_TYPE, UNKNOWN_CURRENCY)


def voucher_by_code(code):
    """The voucher with this code (typed with or without the hyphen, in any case),
    whatever its state, or None (also for text that is not a code's form)."""
    code = normalize_voucher_code(code)
    return Voucher.objects.filter(code=code).first() if code else None


def voucher_state(voucher, day=None):
    """`usable` when the voucher can be used on `day` (a date in Madrid time; today when
    None), else why not: `unknown_type`, `unknown_currency`, `inactive` or
    `not_in_force`, in that order."""
    day = day or today()
    if voucher.type not in Voucher.TYPES:
        return UNKNOWN_TYPE
    if voucher.money is None:
        return UNKNOWN_CURRENCY
    if not voucher.active:
        return INACTIVE
    if not voucher.in_force_on(day):
        return NOT_IN_FORCE
    return USABLE


def voucher_usable(voucher, day=None):
    """Whether the voucher can be used on `day` (`voucher_state` is `usable`)."""
    return voucher_state(voucher, day) == USABLE
