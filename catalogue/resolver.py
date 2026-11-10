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

Prices are net: VAT, vouchers and the billing company come with later entries.
"""
import re
import uuid
import zoneinfo
from decimal import Decimal

from django.utils import timezone

from .models import CountryGroup, Product, Subscription

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


def price(product, subscription):
    """A product's price (`Money`) under a subscription: see the module docstring."""
    discount = discount_percent(product, subscription)
    if not discount:
        return product.net_price
    return product.net_price * (Decimal(1) - discount.scaleb(-2))


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
    the country."""
    applies = subscription(country, tier)
    if applies is None:
        return []
    day = day or today()
    return [(offered, price(offered, applies))
            for offered in Product.objects.filter(active=True).order_by('name', 'id')
            if offered.offered_on(day)]
