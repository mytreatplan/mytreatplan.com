"""The catalogue as the admin site last sent it (epic-catalogue entry 2, AD-3, AD-6, AD-16):
country groups, products and subscriptions, read-only copies of the configuration
snapshot's `catalogue` section. Filled only by the snapshot apply (catalogue.applier,
through integration.config); there is no other write path: `save()` and `delete()` raise,
and the Django admin does not list them. Prices are net (VAT comes at checkout, a later
entry); `catalogue.resolver` is the only code that prices.

Products in full (epic-catalogue entry 5, mytp-core 0.20.0): a product also has its
public texts per language (English always; any other language falls back to English), its
internal code, its validity dates and one of the admin site's categories. A deactivated
product, or one outside its validity dates, is kept: it is not offered for new orders
(catalogue.resolver) but stays readable by id, for the orders that refer to it. The admin
site's internal notes never arrive here.

Add-ons, quantities, calls and costs (epic-catalogue entry 6, mytp-core 0.21.0): the
add-ons a buyer may choose next to a product (`AddOn`); a product's size, per-unit
quantity range, calls and cost links; and the snapshot's `provider_costs` section
(`ProviderProduct`, `ProviderCost`: what each provider product costs over time, without
any provider detail), from which `catalogue.resolver.cost` gives a product's cost at a
date.

Subscriptions in full (epic-catalogue entry 8, mytp-core 0.22.0): a subscription is
standard (the default of its tier and country group) or manual (assigned by hand: read by
its id, never the answer for a country and a tier), and has its code, public texts, the
products it gives access to, a discount and call extras per product category and its own
price. The admin site's internal notes of a subscription never arrive here.
"""
import uuid
from decimal import Decimal

from django.db import models
from django.utils import translation

from mytp_core.money import Money

READ_ONLY_MESSAGE = ('The catalogue is read-only here: it changes only when a '
                     'configuration snapshot is applied (catalogue.applier).')


class ReadOnlyModel(models.Model):
    """Never saved or deleted one by one: the applier writes with bulk_create and
    queryset deletes."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        raise TypeError(READ_ONLY_MESSAGE)

    def delete(self, *args, **kwargs):
        raise TypeError(READ_ONLY_MESSAGE)


ENGLISH = 'en'


def _text_in(texts, language, field):
    """The text `field` of a `texts` map in `language` ("es", "es-ES"), else in English,
    else ''."""
    language = (language or '').lower().split('-')[0]
    for code in (language, ENGLISH):
        text = texts.get(code)
        value = text.get(field, '') if isinstance(text, dict) else ''
        if value:
            return value
    return ''


class CountryGroup(ReadOnlyModel):
    """Countries (ISO 3166-1 alpha-2) a subscription applies to; `catch_all`: every country
    in no other active group ("Rest of world")."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    countries = models.JSONField(default=list)
    catch_all = models.BooleanField(default=False)
    active = models.BooleanField()

    class Meta:
        ordering = ['name', 'id']

    def __str__(self):
        return self.name


class Product(ReadOnlyModel):
    """A product at its net price, in one currency.

    `category` is one of CATEGORIES (the admin site's fixed list, epic-catalogue entry 5;
    a product of another one is not stored). `uncategorised` is a product the admin site
    has not given a category yet: never discounted. LEGACY_CATEGORIES are the codes of an
    admin site before entry 5, read as their new ones.

    `texts` holds the public texts per language, `{"en": {"name": ..., "description":
    ...}, "es": {...}}`: read them with `name_in` / `description_in` (or `public_name` /
    `description`, in the current language), which fall back to English. `name` is the
    English public name. `code` is the admin site's internal code (None for a product
    that has none yet). `valid_from` / `valid_to` are optional, inclusive dates in Madrid
    time: see `offered_on`.

    Entry 6: `size` ('' for none; TPS products only) decides which add-ons are offered;
    `min_quantity` / `max_quantity`, both or neither, make the product priced per unit
    (`net_amount` is the price of one unit, sold in a whole quantity within the range);
    `calls` / `call_minutes`, both or neither, are the calls the product grants and the
    minutes of each; `cost_links` lists the provider products it is made of, each as
    `{"provider_product": "<uuid>", "quantity": 2, "monthly": false}`
    (catalogue.resolver.cost)."""

    TPS_BRANDED = 'tps_branded'
    TPS_IN_HOUSE = 'tps_in_house'
    UNCATEGORISED = 'uncategorised'
    CATEGORIES = (TPS_BRANDED, TPS_IN_HOUSE, 'initial_case_evaluation', 'virtuaortho_one',
                  'virtuaortho_digitaltwin', 'odb', 'retainers', 'spark_on_demand',
                  'subscription', 'education', UNCATEGORISED)
    LEGACY_CATEGORIES = {'tps': TPS_BRANDED, 'other': UNCATEGORISED}
    ENGLISH = 'en'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    # The database defaults (here and on Subscription) let the previous release, which
    # does not know the columns, still apply a snapshot during a deploy.
    texts = models.JSONField(default=dict, db_default=models.Value({}, models.JSONField()))
    code = models.CharField(max_length=64, null=True)
    category = models.CharField(max_length=32)
    net_amount = models.DecimalField(max_digits=17, decimal_places=2)
    currency = models.CharField(max_length=3)
    valid_from = models.DateField(null=True)
    valid_to = models.DateField(null=True)
    # Entry 6. Nullable or with database defaults, as above.
    size = models.CharField(max_length=32, default='', db_default='')
    min_quantity = models.PositiveIntegerField(null=True)
    max_quantity = models.PositiveIntegerField(null=True)
    calls = models.PositiveIntegerField(null=True)
    call_minutes = models.PositiveIntegerField(null=True)
    cost_links = models.JSONField(default=list,
                                  db_default=models.Value([], models.JSONField()))
    active = models.BooleanField()

    class Meta:
        ordering = ['name', 'id']

    def __str__(self):
        return self.name

    @property
    def net_price(self):
        return Money(self.net_amount, self.currency)

    @property
    def per_unit(self):
        """Whether the net price is the price of one unit (a quantity range is set)."""
        return self.min_quantity is not None and self.max_quantity is not None

    def _text(self, language, field):
        return _text_in(self.texts, language, field)

    def name_in(self, language):
        """The public name in `language` ("es", "es-ES"), else in English."""
        return self._text(language, 'name') or self.name

    def description_in(self, language):
        """The short description (plain text, `\\n` line breaks) in `language`, else in
        English, else ''."""
        return self._text(language, 'description')

    @property
    def public_name(self):
        """The public name in the current language, else in English."""
        return self.name_in(translation.get_language())

    @property
    def description(self):
        """The short description in the current language, else in English."""
        return self.description_in(translation.get_language())

    def offered_on(self, day):
        """Whether `day` (a date in Madrid time: catalogue.resolver.today) is within the
        validity dates, both inclusive; a missing date does not limit. It says nothing of
        `active`."""
        return ((self.valid_from is None or self.valid_from <= day)
                and (self.valid_to is None or day <= self.valid_to))


class Subscription(ReadOnlyModel):
    """A tier in one country group (epic-catalogue entry 8, user decisions 2026-11-11).

    `manual`: False for a standard subscription, the default of its tier and country
    group (at most one active: what `catalogue.resolver.subscription(country, tier)`
    returns); True for one an administrator assigns to a doctor by hand (any number per
    tier and group; read by its id, `catalogue.resolver.subscription_by_id`).

    `code` (None for one that has none yet) and `texts` (as a product's; `{}` when the
    admin site sent none: `name_in` is then '').

    `products`: the ids (strings) of the products it gives access to; None when the
    admin site sent no list (one before mytp-core 0.22.0): every product is then
    offered, as before.

    `discounts`: `{category: "12.00"}`, the discount per product category; None when
    the admin site sent no map: the two former fields are then the discounts.
    `tps_discount_percent` and `tps_in_house_discount_percent` always hold the discounts
    on TPS branded and TPS in-house products. Read a discount with
    `catalogue.resolver.discount_percent`.

    Its own price: `price_currency` ('' for none) with `monthly_amount` and
    `upfront_amount` (a year upfront), each optional; without either it is `free`.

    `call_extras`: `{category: {"extra_calls": 1, "minutes": 35}}` (`minutes` optional):
    what it adds to the calls of a product of that category
    (`catalogue.resolver.calls`)."""

    TIERS = ('basic', 'advanced', 'premium')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.CharField(max_length=32)
    country_group = models.ForeignKey(CountryGroup, on_delete=models.CASCADE,
                                      related_name='subscriptions')
    tps_discount_percent = models.DecimalField(max_digits=5, decimal_places=2)
    tps_in_house_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.00'), db_default=Decimal('0.00'))
    # Entry 8. Nullable or with database defaults, as on Product: the previous release,
    # which does not know the columns, still applies a snapshot during a deploy.
    manual = models.BooleanField(default=False, db_default=False)
    code = models.CharField(max_length=64, null=True)
    texts = models.JSONField(default=dict, db_default=models.Value({}, models.JSONField()))
    products = models.JSONField(null=True)
    discounts = models.JSONField(null=True)
    price_currency = models.CharField(max_length=3, default='', db_default='')
    monthly_amount = models.DecimalField(max_digits=17, decimal_places=2, null=True)
    upfront_amount = models.DecimalField(max_digits=17, decimal_places=2, null=True)
    call_extras = models.JSONField(default=dict,
                                   db_default=models.Value({}, models.JSONField()))
    active = models.BooleanField()

    class Meta:
        ordering = ['tier', 'id']

    def __str__(self):
        return f'{self.code or self.tier} ({self.country_group_id})'

    def name_in(self, language):
        """The public name in `language` ("es", "es-ES"), else in English, else ''."""
        return _text_in(self.texts, language, 'name')

    def description_in(self, language):
        """The short description in `language`, else in English, else ''."""
        return _text_in(self.texts, language, 'description')

    @property
    def public_name(self):
        """The public name in the current language, else in English."""
        return self.name_in(translation.get_language())

    @property
    def description(self):
        """The short description in the current language, else in English."""
        return self.description_in(translation.get_language())

    @property
    def free(self):
        """Whether it has no price of its own."""
        return self.monthly_amount is None and self.upfront_amount is None

    @property
    def monthly_price(self):
        """The monthly price as `Money`, or None."""
        return (None if self.monthly_amount is None
                else Money(self.monthly_amount, self.price_currency))

    @property
    def upfront_price(self):
        """The upfront price of a year as `Money`, or None."""
        return (None if self.upfront_amount is None
                else Money(self.upfront_amount, self.price_currency))


class AddOn(ReadOnlyModel):
    """What a buyer may choose next to a product (epic-catalogue entry 6): either a fixed
    net amount in one currency (`amount`, `currency`; 0.00 for an add-on that changes no
    price) or a percentage of the product's net price (`percent`, signed: -25.00 takes a
    quarter off), never both. `categories`: the product categories it is offered on;
    `sizes`: the product sizes (None: the size does not matter). A fixed amount is
    offered only on products of its currency. `catalogue.resolver` decides what is
    offered and prices it. `texts` as a product's."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64)
    texts = models.JSONField(default=dict)
    amount = models.DecimalField(max_digits=17, decimal_places=2, null=True)
    currency = models.CharField(max_length=3, default='')
    percent = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    categories = models.JSONField(default=list)
    sizes = models.JSONField(null=True)
    active = models.BooleanField()

    class Meta:
        ordering = ['code', 'id']

    def __str__(self):
        return self.code

    def name_in(self, language):
        """The public name in `language`, else in English."""
        return _text_in(self.texts, language, 'name')

    def description_in(self, language):
        """The short description in `language`, else in English, else ''."""
        return _text_in(self.texts, language, 'description')

    @property
    def public_name(self):
        """The public name in the current language, else in English."""
        return self.name_in(translation.get_language())

    @property
    def amount_money(self):
        """The fixed amount as `Money`, or None for a percentage."""
        return None if self.amount is None else Money(self.amount, self.currency)

    @property
    def value_text(self):
        """"+120.00 EUR" or "-25.00 %": what the add-on does to a line."""
        if self.amount is not None:
            return f'+{self.amount_money}'
        return f'{self.percent:+f} %'


class ProviderProduct(ReadOnlyModel):
    """A provider product of the snapshot's `provider_costs` section (epic-catalogue
    entry 6): its provider's code for it (not unique: read it by id), its currency and
    whether it is still active at the admin site, with its dated costs (`costs`). Nothing
    of the provider itself ever arrives here."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64)
    currency = models.CharField(max_length=3)
    active = models.BooleanField()

    class Meta:
        ordering = ['code', 'id']

    def __str__(self):
        return self.code


class ProviderCost(ReadOnlyModel):
    """What a provider product costs from `valid_from` to `valid_to` (inclusive; None:
    open-ended): the face price and the discounted one, which is the cost, in the
    provider product's currency. The ranges of one product never overlap; gaps are
    allowed (no cost on those dates)."""

    product = models.ForeignKey(ProviderProduct, on_delete=models.CASCADE,
                                related_name='costs')
    valid_from = models.DateField()
    valid_to = models.DateField(null=True)
    face = models.DecimalField(max_digits=17, decimal_places=2)
    discounted = models.DecimalField(max_digits=17, decimal_places=2)

    class Meta:
        ordering = ['product', 'valid_from']

    def __str__(self):
        return f'{self.product_id} from {self.valid_from}'

    def holds(self, day):
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)
