"""The configuration snapshot's `catalogue` section, applied (registered with
integration.config.register_section_applier by catalogue.apps; run inside the snapshot
apply's transaction, so a failure keeps the previous catalogue).

The tables are replaced by the section's rows, inactive ones too. A snapshot without the
section (an admin site before mytp-core 0.12.0) empties them. What this release does not
know is skipped, as the contract says readers do: a product of an unknown category or a
currency `Money` does not take, a subscription of an unknown tier."""
from mytp_core.money import CURRENCIES

from .models import CountryGroup, Product, Subscription


def apply(section):
    """Replace the catalogue with `section` (mytp-core's CatalogueSection, or None).
    Returns the log line's summary: the stored row counts, never the contents."""
    Subscription.objects.all().delete()
    Product.objects.all().delete()
    CountryGroup.objects.all().delete()
    if section is None:
        return 'no catalogue section'
    CountryGroup.objects.bulk_create(
        CountryGroup(id=group.id, name=group.name, countries=sorted(group.countries),
                     catch_all=group.catch_all, active=group.active)
        for group in section.country_groups)
    Product.objects.bulk_create(
        Product(id=product.id, name=product.name, category=product.category,
                net_amount=product.net_price.amount, currency=product.net_price.currency,
                active=product.active)
        for product in section.products
        if product.category in Product.CATEGORIES and product.net_price.currency in CURRENCIES)
    Subscription.objects.bulk_create(
        Subscription(id=subscription.id, tier=subscription.tier,
                     country_group_id=subscription.country_group,
                     tps_discount_percent=subscription.tps_discount_percent,
                     active=subscription.active)
        for subscription in section.subscriptions if subscription.tier in Subscription.TIERS)
    return (f'catalogue: {CountryGroup.objects.count()} country groups, '
            f'{Product.objects.count()} products, {Subscription.objects.count()} subscriptions')
