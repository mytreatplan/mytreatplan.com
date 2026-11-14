"""The catalogue as the admin site last sent it (epic-catalogue entry 2, AD-3, AD-6, AD-16):
country groups, products and subscriptions, read-only copies of the configuration
snapshot's `catalogue` section. Filled only by the snapshot apply (catalogue.applier,
through integration.config); there is no other write path: since epic-catalogue entry 11
every ORM write (an instance's `save()` and `delete()`, a queryset's `update()`,
`delete()`, `bulk_create()` and `bulk_update()`) raises outside the snapshot apply
(integration.snapshot_tables), and the Django admin does not list them. Prices are net:
the VAT is added by the pricing function (catalogue.pricing, epic-catalogue entry 12),
through `catalogue.resolver`, the only code that prices.

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

Billing companies and billing rules (epic-catalogue entry 9, mytp-core 0.23.0): the
companies that bill (`BillingCompany`) and, per country group and product category, the
rule (`BillingRule`): the company, the VAT rate or its exemption text, the payment
gateways in order and the currency the pair is sold in. `catalogue.resolver` is the only
code that reads them to decide company, VAT and gateway (AD-16); no VAT amount is
computed here (entry 12 does).

Vouchers (epic-catalogue entry 10, mytp-core 0.24.0): the voucher definitions of this
region (`Voucher`), read by their code (`catalogue.resolver.voucher_by_code`). Nothing
about redeeming them is here (epic-subscriptions).

Per region (epic-catalogue entry 16, mytp-core 0.25.0): the admin site sends this region
only its own country groups, billing companies and products, with the subscriptions and
rules of its groups (add-ons and provider costs are shared); the applier skips anything
of another region all the same. No region is stored: every row here is this region's.
`CatalogueState` keeps what the section said beyond its rows: whether it had a billing
rules list (an empty one too: rules exist on the admin site, none of them this region's).
"""
import uuid
from decimal import Decimal

from django.db import models
from django.utils import translation

from integration.snapshot_tables import SnapshotFedModel
from mytp_core.money import Money


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


class CountryGroup(SnapshotFedModel):
    """Countries (ISO 3166-1 alpha-2) a subscription applies to; `catch_all`: every country
    in no other active group ("Rest of world")."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    countries = models.JSONField(default=list)
    catch_all = models.BooleanField(default=False)
    active = models.BooleanField()

    class Meta:
        base_manager_name = 'objects'  # guarded too (integration.snapshot_tables)
        ordering = ['name', 'id']

    def __str__(self):
        return self.name


class Product(SnapshotFedModel):
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
        base_manager_name = 'objects'  # guarded too (integration.snapshot_tables)
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


class Subscription(SnapshotFedModel):
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
        base_manager_name = 'objects'  # guarded too (integration.snapshot_tables)
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


class AddOn(SnapshotFedModel):
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
        base_manager_name = 'objects'  # guarded too (integration.snapshot_tables)
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


class ProviderProduct(SnapshotFedModel):
    """A provider product of the snapshot's `provider_costs` section (epic-catalogue
    entry 6): its provider's code for it (not unique: read it by id), its currency and
    whether it is still active at the admin site, with its dated costs (`costs`). Nothing
    of the provider itself ever arrives here."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64)
    currency = models.CharField(max_length=3)
    active = models.BooleanField()

    class Meta:
        base_manager_name = 'objects'  # guarded too (integration.snapshot_tables)
        ordering = ['code', 'id']

    def __str__(self):
        return self.code


class ProviderCost(SnapshotFedModel):
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
        base_manager_name = 'objects'  # guarded too (integration.snapshot_tables)
        ordering = ['product', 'valid_from']

    def __str__(self):
        return f'{self.product_id} from {self.valid_from}'

    def holds(self, day):
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)


class BillingCompany(SnapshotFedModel):
    """A company that bills (epic-catalogue entry 9): its short `code` (ES, IE, AE), legal
    name, address (plain text, `\\n` line breaks), country (ISO 3166-1 alpha-2), legal
    time zone (an IANA name) and its identifiers, each '' when it has none: the company
    number or license, the VAT registration and the TAX registration. A deactivated
    company stays, readable for what refers to it. No bank details and no invoice series
    (epic-payments)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=16)
    legal_name = models.CharField(max_length=200)
    address = models.TextField()
    country = models.CharField(max_length=2)
    time_zone = models.CharField(max_length=64)
    company_number = models.CharField(max_length=64, default='')
    vat_number = models.CharField(max_length=64, default='')
    tax_number = models.CharField(max_length=64, default='')
    active = models.BooleanField()

    class Meta:
        base_manager_name = 'objects'  # guarded too (integration.snapshot_tables)
        ordering = ['code', 'id']

    def __str__(self):
        return self.code

    @property
    def identifiers(self):
        """[(label, value)] of the identifiers the company has."""
        return [(label, value) for label, value in (
            ('company number', self.company_number), ('VAT', self.vat_number),
            ('TAX', self.tax_number)) if value]


class BillingRule(SnapshotFedModel):
    """The billing rule of one country group and product category (epic-catalogue entry
    9), as sent: the `company` that bills, the VAT rate (`vat_percent`), the
    `exemption_text` of a rate of 0 ('' otherwise allowed), the `reverse_charge_text`
    ('' for none: entry 12 uses it, at a rate of 0, for a doctor with a valid VIES number
    in another country than the company's), the payment `gateways` in order of
    preference (codes; read the ones this release knows with `known_gateways`), the
    `currency` the pair is sold in and whether the category is `available` in the group.
    At most one active rule per group and category (the snapshot's contract).

    Every rule is stored, also one of a category or in a currency this release does not
    know: such a rule offers nothing (no product has that category or currency), which
    is what a rule this release cannot honour must do. Read rules through
    `catalogue.resolver` (`billing_rule`, `subscription_rule`), never directly.

    `country_group` has no database constraint: the release before this entry replaces
    the country groups without knowing this table, and must still be able to apply a
    snapshot during a deploy or after a rollback."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    country_group = models.ForeignKey(CountryGroup, on_delete=models.CASCADE,
                                      related_name='billing_rules', db_constraint=False)
    category = models.CharField(max_length=32)
    company = models.ForeignKey(BillingCompany, on_delete=models.CASCADE,
                                related_name='rules')
    vat_percent = models.DecimalField(max_digits=5, decimal_places=2)
    exemption_text = models.TextField(default='')
    reverse_charge_text = models.TextField(default='')
    gateways = models.JSONField(default=list)
    currency = models.CharField(max_length=3)
    available = models.BooleanField()
    active = models.BooleanField()

    class Meta:
        base_manager_name = 'objects'  # guarded too (integration.snapshot_tables)
        ordering = ['category', 'id']

    def __str__(self):
        return f'{self.country_group_id} {self.category}'

    @property
    def exempt(self):
        """Whether the rate is 0: the exemption text is then what an invoice shows."""
        return self.vat_percent == 0

    @property
    def known_gateways(self):
        """The gateways this release knows, in the rule's order of preference."""
        from mytp_core.contracts import known_gateways
        return known_gateways(self.gateways)


class CatalogueState(SnapshotFedModel):
    """What the applied catalogue section said beyond its rows (epic-catalogue entry 16,
    mytp-core 0.25.0): one row per applied section, none without a section (written by
    catalogue.applier; read through `catalogue.resolver.has_billing_rules`).

    `billing_rules_listed`: whether the section had a `billing_rules` list, an empty one
    too. The admin site leaves the list out while no rule exists anywhere (every pair is
    offered, as before rules) and sends it, empty, to a region whose rules are all another
    region's: rules exist, so nothing without a rule is offered. A table with no rule rows
    cannot tell the two apart; this row does."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    billing_rules_listed = models.BooleanField(default=False)

    class Meta:
        base_manager_name = 'objects'  # guarded too (integration.snapshot_tables)

    def __str__(self):
        return f'billing rules listed: {self.billing_rules_listed}'


class Voucher(SnapshotFedModel):
    """A voucher definition of this region (epic-catalogue entry 10, mytp-core 0.24.0,
    AD-20), as sent in the snapshot's top-level `vouchers` list: its generated `code`
    (stored without the hyphen; shown `XXXXX-XXXXX`), its `type` (`subscription` or
    `try_me`; one this release does not know is kept as text and is not usable), a fixed
    `amount` in `currency`, the `client` UUID of the one doctor it is for (None: any
    doctor), its validity dates (both included, Madrid time; `valid_to` None: no end) and
    whether it is `active`. A deactivated voucher stays, readable for the orders that
    refer to it, and is not usable.

    No use count, reservation or "already used" state: redeeming is epic-subscriptions'
    (AD-20). Read vouchers through `catalogue.resolver` (`voucher_by_code`,
    `voucher_state`), never directly."""

    SUBSCRIPTION, TRY_ME = 'subscription', 'try_me'
    TYPES = (SUBSCRIPTION, TRY_ME)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=32, unique=True)
    type = models.CharField(max_length=32)
    amount = models.DecimalField(max_digits=17, decimal_places=2)
    currency = models.CharField(max_length=3)
    client = models.UUIDField(null=True)
    valid_from = models.DateField()
    valid_to = models.DateField(null=True)
    active = models.BooleanField()

    class Meta:
        base_manager_name = 'objects'  # guarded too (integration.snapshot_tables)
        ordering = ['code']

    def __str__(self):
        return self.display_code

    @property
    def display_code(self):
        from mytp_core.contracts import display_voucher_code
        return display_voucher_code(self.code)

    def in_force_on(self, day):
        """Whether `day` (a date) is inside the validity dates, both included."""
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)

    @property
    def money(self):
        """The amount as `Money`, or None for a currency this release does not take."""
        try:
            return Money(self.amount, self.currency)
        except ValueError:
            return None
