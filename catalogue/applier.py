"""The configuration snapshot's `catalogue` section, applied (registered with
integration.config.register_section_applier by catalogue.apps; run inside the snapshot
apply's transaction, so a failure keeps the previous catalogue).

The tables are replaced by the section's rows, inactive ones too: a deactivated product
stays, marked inactive, readable for the orders that refer to it. A snapshot without the
section (an admin site before mytp-core 0.12.0) empties them. What this release does not
know is skipped, as the contract says readers do: a product of an unknown category or a
currency `Money` does not take, a subscription of an unknown tier.

Products in full (epic-catalogue entry 5, mytp-core 0.20.0): a product's public texts per
language, code and validity dates are stored as sent. A snapshot of an admin site before
0.20.0 has none of them and still applies: the product's name is then its English text,
its former category (`tps`, `other`) is read as the new one (`tps_branded`,
`uncategorised`), and a subscription's absent in-house discount is 0.

The release before entry 5 skipped the products of the new categories, and a snapshot of
the version already applied is never applied again (integration.config), so catalogue
migration 0002 clears the applied version: the first pull of this release applies the
admin site's current snapshot again and stores the products that release skipped."""
from mytp_core.money import CURRENCIES

from .models import CountryGroup, Product, Subscription


def _texts(product):
    """The stored texts map of a contract product: its texts as sent, or its name as the
    English text (an admin site before mytp-core 0.20.0)."""
    if product.texts is None:
        return {Product.ENGLISH: {'name': product.name, 'description': ''}}
    return {language: {'name': text.name, 'description': text.description}
            for language, text in product.texts.items()}


def _category(product):
    return Product.LEGACY_CATEGORIES.get(product.category, product.category)


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
        Product(id=product.id, name=product.name, texts=_texts(product), code=product.code,
                category=_category(product), net_amount=product.net_price.amount,
                currency=product.net_price.currency, valid_from=product.valid_from,
                valid_to=product.valid_to, active=product.active)
        for product in section.products
        if _category(product) in Product.CATEGORIES
        and product.net_price.currency in CURRENCIES)
    Subscription.objects.bulk_create(
        Subscription(id=subscription.id, tier=subscription.tier,
                     country_group_id=subscription.country_group,
                     tps_discount_percent=subscription.tps_discount_percent,
                     tps_in_house_discount_percent=subscription.in_house_discount_percent,
                     active=subscription.active)
        for subscription in section.subscriptions if subscription.tier in Subscription.TIERS)
    return (f'catalogue: {CountryGroup.objects.count()} country groups, '
            f'{Product.objects.count()} products, {Subscription.objects.count()} subscriptions')
