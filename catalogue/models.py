"""The catalogue as the admin site last sent it (epic-catalogue entry 2, AD-3, AD-6, AD-16):
country groups, products and subscriptions, read-only copies of the configuration
snapshot's `catalogue` section. Filled only by the snapshot apply (catalogue.applier,
through integration.config); there is no other write path: `save()` and `delete()` raise,
and the Django admin does not list them. Prices are net (VAT comes at checkout, a later
entry); `catalogue.resolver` is the only code that prices.
"""
import uuid

from django.db import models

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
    """A product at its net price."""

    TPS = 'tps'
    OTHER = 'other'
    CATEGORIES = (TPS, OTHER)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=32)
    net_amount = models.DecimalField(max_digits=17, decimal_places=2)
    currency = models.CharField(max_length=3)
    active = models.BooleanField()

    class Meta:
        ordering = ['name', 'id']

    def __str__(self):
        return self.name

    @property
    def net_price(self):
        return Money(self.net_amount, self.currency)


class Subscription(ReadOnlyModel):
    """A tier in one country group, with its discount (percent) on plain TPS."""

    TIERS = ('basic', 'advanced', 'premium')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.CharField(max_length=32)
    country_group = models.ForeignKey(CountryGroup, on_delete=models.CASCADE,
                                      related_name='subscriptions')
    tps_discount_percent = models.DecimalField(max_digits=5, decimal_places=2)
    active = models.BooleanField()

    class Meta:
        ordering = ['tier', 'id']

    def __str__(self):
        return f'{self.tier} ({self.country_group_id})'
