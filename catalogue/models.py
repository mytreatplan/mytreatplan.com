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
    time: see `offered_on`."""

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
    active = models.BooleanField()

    class Meta:
        ordering = ['name', 'id']

    def __str__(self):
        return self.name

    @property
    def net_price(self):
        return Money(self.net_amount, self.currency)

    def _text(self, language, field):
        language = (language or '').lower().split('-')[0]
        for code in (language, self.ENGLISH):
            text = self.texts.get(code)
            value = text.get(field, '') if isinstance(text, dict) else ''
            if value:
                return value
        return ''

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
    """A tier in one country group, with its two discounts (percent): on TPS branded
    products and on TPS in-house ones (0 when the admin site sends none). No other
    category is discounted."""

    TIERS = ('basic', 'advanced', 'premium')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.CharField(max_length=32)
    country_group = models.ForeignKey(CountryGroup, on_delete=models.CASCADE,
                                      related_name='subscriptions')
    tps_discount_percent = models.DecimalField(max_digits=5, decimal_places=2)
    tps_in_house_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.00'), db_default=Decimal('0.00'))
    active = models.BooleanField()

    class Meta:
        ordering = ['tier', 'id']

    def __str__(self):
        return f'{self.tier} ({self.country_group_id})'
