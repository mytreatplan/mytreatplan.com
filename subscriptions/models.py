"""The subscription a doctor holds (epic-subscriptions entry 1, AD-15, AD-22; user
decisions 2026-11-14): one current record per doctor (`DoctorSubscription`) and one row per
change of it (`SubscriptionChange`).

Written only by `subscriptions.service` (and the backfill migration 0002): every change
checks the record's version, raises it by one, stores who made it and adds a change row.
Read the effective tier through `subscriptions.service.effective`, never from these fields
alone (a cancelled record past its end date, an expired one, a doctor without a record
all count as Basic).

The record names the catalogue subscription by its UUID only (`subscription_id`), with no
database link: every snapshot apply deletes and re-creates the catalogue rows
(catalogue.applier), and it never deletes, blocks on or changes a doctor's record. A Basic
record that is not manual holds the level only (`subscription_id` None): the doctor's
subscription is whichever active standard Basic their country group has today.

Neither model is fed by the snapshot nor listed in the Django admin. Both go with the
user (CASCADE: the purge of erased accounts deletes users through a queryset).
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from catalogue.models import Subscription as CatalogueSubscription


class AppendOnly(RuntimeError):
    """A change row was to be updated or deleted one by one: the change rows are
    append-only (they go only with their record, by CASCADE)."""


# Who made a change (AD-22): a doctor (their `uid`), an admin user (the admin site's
# UUID) or the system (no UUID).
DOCTOR = 'doctor'
ADMIN = 'admin'
SYSTEM = 'system'
ACTOR_TYPES = [(DOCTOR, 'Doctor'), (ADMIN, 'Admin user'), (SYSTEM, 'System')]


class DoctorSubscription(models.Model):
    """A doctor's current subscription record (one per doctor; staff and superusers have
    none).

    `tier`: one of the catalogue's tiers (`catalogue.models.Subscription.TIERS`).
    `subscription_id`: the catalogue subscription held (a standard paid one, or a manual
    one), None for a standard Basic. `started_on` / `ends_on`: dates in Madrid time
    (`catalogue.resolver.today`); `ends_on` None for none. `billing_period`: '' (nothing
    billed), `monthly` or `upfront`. `status`: see STATUSES. `manual`: assigned by an
    administrator; a doctor cannot change it. `version`: raised by one on every change
    (the remote write's version check). `actor_type` / `actor_uuid`: who made the last
    change."""

    # AD-5: crosses sites (the admin site's assignment, entry 10); its UUID primary key
    # is its global id.
    CROSSES_SITES = True

    BASIC = 'basic'
    TIERS = CatalogueSubscription.TIERS
    ACTIVE = 'active'
    CANCELLED = 'cancelled'
    PAST_DUE = 'past_due'
    EXPIRED = 'expired'
    STATUSES = [(ACTIVE, 'Active'), (CANCELLED, 'Cancelled (until its end date)'),
                (PAST_DUE, 'Past due (grace)'), (EXPIRED, 'Expired')]
    MONTHLY = 'monthly'
    UPFRONT = 'upfront'
    BILLING_PERIODS = [('', 'None'), (MONTHLY, 'Monthly'), (UPFRONT, 'A year upfront')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    doctor = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name='subscription_record')
    tier = models.CharField(max_length=32, choices=[(tier, tier) for tier in TIERS])
    # The catalogue subscription's id: no database link (the apply re-creates the rows).
    subscription_id = models.UUIDField(null=True, blank=True)
    started_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    billing_period = models.CharField(max_length=10, choices=BILLING_PERIODS, blank=True,
                                      default='')
    auto_renew = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=STATUSES, default=ACTIVE)
    manual = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1, db_default=1)
    actor_type = models.CharField(max_length=10, choices=ACTOR_TYPES)
    # The actor's global id (a doctor's `uid`, an admin user's UUID); None for the system.
    actor_uuid = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    # The fields a change row copies (`before` / `after`): the record's business fields.
    BUSINESS_FIELDS = ('tier', 'subscription_id', 'started_on', 'ends_on', 'billing_period',
                       'auto_renew', 'status', 'manual')

    class Meta:
        ordering = ['created_at', 'id']

    def __str__(self):
        return f'{self.tier} ({self.status}) of user {self.doctor_id}'

    def business_values(self):
        """The business fields, JSON-ready (UUID and dates as text): a change row's
        `before` or `after`."""
        values = {}
        for name in self.BUSINESS_FIELDS:
            value = getattr(self, name)
            if isinstance(value, uuid.UUID):
                value = str(value)
            elif hasattr(value, 'isoformat'):
                value = value.isoformat()
            values[name] = value
        return values


class SubscriptionChangeQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnly('the subscription changes are append-only')


class SubscriptionChange(models.Model):
    """One change of a doctor's record (user decision 2026-11-14): when, who (actor type
    and UUID), why (`reason`, a short code: `signup`, `backfill`, `manual_assigned`, ...),
    the business fields before (`{}` on creation) and after, and the record's version
    after it. Append-only, as far as the ORM guards it: an instance `save()` of an
    existing row, an instance `delete()` and the default manager's queryset `update()`
    raise. Not guarded: the deletion collector's cascade (the rows go with their record,
    and so with the user, by CASCADE), `_base_manager`, a queryset `delete()`, raw SQL
    and the migrations' historical models."""

    CROSSES_SITES = True  # AD-5, as its record

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    record = models.ForeignKey(DoctorSubscription, on_delete=models.CASCADE,
                               related_name='changes')
    at = models.DateTimeField(default=timezone.now)
    actor_type = models.CharField(max_length=10, choices=ACTOR_TYPES)
    actor_uuid = models.UUIDField(null=True, blank=True)
    reason = models.CharField(max_length=40)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict)
    version = models.PositiveIntegerField()

    objects = SubscriptionChangeQuerySet.as_manager()

    class Meta:
        ordering = ['at', 'version', 'id']

    def __str__(self):
        return f'{self.reason} (version {self.version})'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnly('the subscription changes are append-only')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnly('the subscription changes are append-only')
