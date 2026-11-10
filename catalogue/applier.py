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

The release before entry 5 skipped the products of the new categories. Every release
applies the admin site's current snapshot again at its first pull, whatever its version
(integration.config: the state row records which release applied it), so what a previous
release skipped or did not store is stored then.

Add-ons, quantities, calls and costs (epic-catalogue entry 6, mytp-core 0.21.0): the
section's add-ons replace the stored ones (none in a snapshot of an admin site before
0.21.0; one whose fixed amount is in a currency `Money` does not take is skipped), and a
product's size, per-unit quantity range, calls and cost links are stored as sent (absent:
no size, one per line, no calls, no links). The snapshot's `provider_costs` section has
its own applier, `apply_provider_costs`: the provider products and their dated costs
replace the stored ones (a snapshot without the section empties them: no costs; a
provider product in a currency `Money` does not take is skipped). The release before
entry 6 stored none of this; the first pull of the next release does (as above).

Subscriptions in full (epic-catalogue entry 8, mytp-core 0.22.0): the section's
`subscriptions` (the standard ones) and `manual_subscriptions` (none in a snapshot of an
admin site before 0.22.0) are stored in one table, the manual ones marked. A
subscription's code, texts, linked products, discounts per product category, own price
and call extras are stored as sent. A snapshot of an admin site before 0.22.0 has none
of them and still applies: the linked products and the discounts map are then None, so
every product is offered under each subscription and the two TPS discounts apply, as
before. A subscription whose own price is in a currency `Money` does not take is
skipped (never stored as free). The release before entry 8 stored none of this; the
first pull of the next release does (as above): no migration clears the applied
version."""
from mytp_core.money import CURRENCIES

from .models import (AddOn, CountryGroup, Product, ProviderCost, ProviderProduct,
                     Subscription)


def _texts(product):
    """The stored texts map of a contract product: its texts as sent, or its name as the
    English text (an admin site before mytp-core 0.20.0)."""
    if product.texts is None:
        return {Product.ENGLISH: {'name': product.name, 'description': ''}}
    return _texts_of(product.texts)


def _category(product):
    return Product.LEGACY_CATEGORIES.get(product.category, product.category)


def _texts_of(texts):
    return {language: {'name': text.name, 'description': text.description}
            for language, text in texts.items()}


def _links(product):
    return [{'provider_product': str(link.provider_product), 'quantity': link.quantity,
             'monthly': link.monthly} for link in product.cost_links or ()]


def _percent(value):
    return format(value, 'f')


def _subscription(subscription, manual):
    """The row of a contract subscription, standard or `manual`: what it carries as
    sent; what is absent (an admin site before mytp-core 0.22.0) as None for the linked
    products and the discounts map (every product, the two former discounts), no code,
    no texts, no price, no call extras."""
    price = subscription.price
    return Subscription(
        id=subscription.id, tier=subscription.tier,
        country_group_id=subscription.country_group,
        tps_discount_percent=subscription.tps_discount_percent,
        tps_in_house_discount_percent=subscription.in_house_discount_percent,
        manual=manual, code=subscription.code,
        texts={} if subscription.texts is None else _texts_of(subscription.texts),
        products=(None if subscription.products is None
                  else [str(product) for product in subscription.products]),
        discounts=(None if subscription.discounts is None
                   else {category: _percent(percent)
                         for category, percent in subscription.discounts.items()}),
        price_currency=price.currency if price else '',
        monthly_amount=price and price.monthly, upfront_amount=price and price.upfront,
        call_extras={
            category: ({'extra_calls': extras.extra_calls} if extras.minutes is None
                       else {'extra_calls': extras.extra_calls, 'minutes': extras.minutes})
            for category, extras in (subscription.call_extras or {}).items()},
        active=subscription.active)


def apply(section):
    """Replace the catalogue with `section` (mytp-core's CatalogueSection, or None).
    Returns the log line's summary: the stored row counts, never the contents."""
    Subscription.objects.all().delete()
    Product.objects.all().delete()
    CountryGroup.objects.all().delete()
    AddOn.objects.all().delete()
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
                valid_to=product.valid_to, size=product.size or '',
                min_quantity=product.per_unit and product.per_unit.min_quantity,
                max_quantity=product.per_unit and product.per_unit.max_quantity,
                calls=product.calls and product.calls.count,
                call_minutes=product.calls and product.calls.minutes,
                cost_links=_links(product), active=product.active)
        for product in section.products
        if _category(product) in Product.CATEGORIES
        and product.net_price.currency in CURRENCIES)
    Subscription.objects.bulk_create(
        _subscription(subscription, manual)
        for manual, subscriptions in ((False, section.subscriptions),
                                      (True, section.manual_subscriptions or ()))
        for subscription in subscriptions
        if subscription.tier in Subscription.TIERS
        and (subscription.price is None or subscription.price.currency in CURRENCIES))
    AddOn.objects.bulk_create(
        AddOn(id=add_on.id, code=add_on.code, texts=_texts_of(add_on.texts),
              amount=add_on.amount and add_on.amount.amount,
              currency=add_on.amount.currency if add_on.amount else '',
              percent=add_on.percent, categories=list(add_on.categories),
              sizes=None if add_on.sizes is None else list(add_on.sizes),
              active=add_on.active)
        for add_on in section.add_ons or ()
        if add_on.amount is None or add_on.amount.currency in CURRENCIES)
    return (f'catalogue: {CountryGroup.objects.count()} country groups, '
            f'{Product.objects.count()} products, {Subscription.objects.count()} '
            f'subscriptions, {AddOn.objects.count()} add-ons')


def apply_provider_costs(section):
    """Replace the provider products and their costs with `section` (mytp-core's
    ProviderCostsSection, or None: an admin site before 0.19.0, so no costs). Returns
    the log line's summary: counts only."""
    ProviderCost.objects.all().delete()
    ProviderProduct.objects.all().delete()
    if section is None:
        return 'no provider costs section'
    known = [product for product in section.products if product.currency in CURRENCIES]
    ProviderProduct.objects.bulk_create(
        ProviderProduct(id=product.id, code=product.code, currency=product.currency,
                        active=product.active)
        for product in known)
    ProviderCost.objects.bulk_create(
        ProviderCost(product_id=product.id, valid_from=cost.valid_from,
                     valid_to=cost.valid_to, face=cost.face, discounted=cost.discounted)
        for product in known for cost in product.costs)
    return (f'provider costs: {ProviderProduct.objects.count()} provider products, '
            f'{ProviderCost.objects.count()} costs')
