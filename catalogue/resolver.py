"""The only code that prices (epic-catalogue entry 2, AD-6, AD-16): what a doctor of a
country is offered under a subscription tier, from the catalogue the admin site last sent
(catalogue.models).

`offers(country, tier)`:

1. the country's group: the active group that lists `country` (ISO 3166-1 alpha-2), else
   the active catch-all group ("Rest of world"); none: no offers;
2. the active subscription of `tier` in that group; none: no offers;
3. every active product that is within its validity dates today (`valid_from` /
   `valid_to`, both optional and inclusive, in Madrid time whatever the region: `today`),
   by name, with its price (`price`): for a TPS branded product (category `tps_branded`)
   the net price less the subscription's TPS branded discount, for a TPS in-house one
   (`tps_in_house`) the net price less its TPS in-house discount, each
   ``net_price × (1 − discount / 100)`` rounded half up to cents (mytp-core's `Money`);
   for any other category, `uncategorised` included, the net price (epic-catalogue
   entry 5, user decision 2026-11-10).

A product that is inactive or outside its validity dates is not offered for new orders,
but stays readable by its id (`product`), for the orders that refer to it.

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
  is inactive, not offered on the product or chosen twice is `AddOnRefused`. `offers`
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

Prices are net: VAT, vouchers and the billing company come with later entries.
"""
import re
import uuid
import zoneinfo
from decimal import Decimal

from django.utils import timezone

from mytp_core.contracts import (AddOnRefused, QuantityRefused, add_on_applies,
                                 check_quantity, line_price, link_labels, sum_costs)
from mytp_core.money import Money

from .models import AddOn, CountryGroup, Product, ProviderProduct, Subscription

__all__ = ['AddOnRefused', 'QuantityRefused', 'add_on', 'add_ons', 'cost', 'country_group',
           'discount_percent', 'normal_country', 'normal_tier', 'offers', 'price',
           'product', 'subscription', 'today', 'unit_price']

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


def subscription(country, tier):
    """The active subscription of `tier` for `country`, or None."""
    group = country_group(country)
    if group is None:
        return None
    return Subscription.objects.filter(active=True, tier=normal_tier(tier),
                                       country_group=group).first()


def discount_percent(product, subscription):
    """The subscription's discount (a Decimal percentage) on `product`: its TPS branded
    discount on a `tps_branded` product, its TPS in-house discount on a `tps_in_house`
    one, 0 on any other category."""
    if product.category == Product.TPS_BRANDED:
        return subscription.tps_discount_percent
    if product.category == Product.TPS_IN_HOUSE:
        return subscription.tps_in_house_discount_percent
    return Decimal(0)


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
    inactive, not offered on the product or chosen twice (both are ValueErrors)."""
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


def offers(country, tier, day=None):
    """[(product, Money)] for a doctor of `country` under `tier` on `day` (a date in
    Madrid time; today when None); [] when no active subscription of that tier applies to
    the country. The price is `unit_price`: of one unit for a product priced per unit,
    without add-ons."""
    applies = subscription(country, tier)
    if applies is None:
        return []
    day = day or today()
    return [(offered, unit_price(offered, applies))
            for offered in Product.objects.filter(active=True).order_by('name', 'id')
            if offered.offered_on(day)]
