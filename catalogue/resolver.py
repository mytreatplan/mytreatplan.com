"""The only code that prices (epic-catalogue entry 2, AD-6, AD-16): what a doctor of a
country is offered under a subscription tier, from the catalogue the admin site last sent
(catalogue.models).

`offers(country, tier)`:

1. the country's group: the active group that lists `country` (ISO 3166-1 alpha-2), else
   the active catch-all group ("Rest of world"); none: no offers;
2. the active subscription of `tier` in that group; none: no offers;
3. every active product, by name, with its price: for plain TPS (category `tps`) the net
   price less the subscription's discount, ``net_price × (1 − discount / 100)`` rounded
   half up to cents (mytp-core's `Money`); for any other category the net price.

Prices are net: VAT, vouchers and the billing company come with later entries.
"""
import re
from decimal import Decimal

from .models import CountryGroup, Product, Subscription

_COUNTRY_RE = re.compile(r'[A-Z]{2}')


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


def price(product, subscription):
    """A product's price (`Money`) under a subscription: see the module docstring."""
    if product.category == Product.TPS:
        factor = Decimal(1) - subscription.tps_discount_percent.scaleb(-2)
        return product.net_price * factor
    return product.net_price


def offers(country, tier):
    """[(product, Money)] for a doctor of `country` under `tier`; [] when no active
    subscription of that tier applies to the country."""
    applies = subscription(country, tier)
    if applies is None:
        return []
    return [(product, price(product, applies))
            for product in Product.objects.filter(active=True).order_by('name', 'id')]
