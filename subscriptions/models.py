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
    and UUID), why (`reason`, a short code: `signup`, `backfill`, `manual_assigned`,
    `purchase`, ...), the order that made it (`order_id`: a purchase's, else None), the
    business fields before (`{}` on creation) and after, and the record's version after
    it. Append-only, as far as the ORM guards it: an instance `save()` of an
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
    # The order that made the change (epic-subscriptions entry 5: a `purchase`; 8.6: a
    # `renewal`), else None. A plain UUID, no database link (orders are another app's); at
    # most one change per order, so a repeated `paid` hook changes nothing.
    order_id = models.UUIDField(null=True, blank=True)

    objects = SubscriptionChangeQuerySet.as_manager()

    class Meta:
        ordering = ['at', 'version', 'id']
        constraints = [models.UniqueConstraint(
            fields=['order_id'], condition=models.Q(order_id__isnull=False),
            name='subscription_change_one_per_order')]

    def __str__(self):
        return f'{self.reason} (version {self.version})'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnly('the subscription changes are append-only')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnly('the subscription changes are append-only')


class Renewal(models.Model):
    """The renewal of one annual term of a record (epic-subscriptions 8.6; user decisions
    2026-11-27): one row per record and term (`term_ends_on`: the `ends_on` of the term
    being renewed), so a rerun of the daily job never makes a second order or charge.
    `order_id` is the renewal order (a plain UUID, no database link: orders are another
    app's), None when none could be made (the held subscription is no longer sold); an
    abandoned order made by hand ("Renew now") is replaced by the next one. `outcome`: see
    OUTCOMES. Written only by `subscriptions.service`."""

    CROSSES_SITES = True  # AD-5: its UUID is its global id

    PENDING, PAID, DECLINED, NO_CARD, UNKNOWN, NOT_SOLD = (
        'pending', 'paid', 'declined', 'no_card', 'unknown', 'not_sold')
    OUTCOMES = [(PENDING, 'Order made, not charged or paid yet'), (PAID, 'Paid'),
                (DECLINED, 'Card declined: payment link sent'),
                (NO_CARD, 'No saved card: payment link sent'),
                (UNKNOWN, 'Charge outcome unknown: staff decide'),
                (NOT_SOLD, 'Subscription no longer sold: no order')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    record = models.ForeignKey(DoctorSubscription, on_delete=models.CASCADE,
                               related_name='renewals')
    term_ends_on = models.DateField()
    order_id = models.UUIDField(null=True, blank=True, unique=True)
    outcome = models.CharField(max_length=10, choices=OUTCOMES, default=PENDING)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['term_ends_on', 'id']
        constraints = [models.UniqueConstraint(fields=['record', 'term_ends_on'],
                                               name='renewal_one_per_term')]

    def __str__(self):
        return f'Renewal {self.pk} ({self.outcome})'


class VoucherRedemption(models.Model):
    """One use of a voucher by one order (epic-subscriptions entry 4, AD-20, S5; user
    decisions 2026-11-20). It follows the order through the `payments.hooks` receivers of
    `subscriptions.vouchers`: `reserved` when the order is created, `consumed` when it is
    paid, `released` when it is cancelled or expires before payment. A released row stays
    as history and never counts as a use; a consumed one is never released (a refund is
    epic-payments' 7.7).

    The limits are database constraints on the live states (reserved, consumed): a
    subscription voucher has one live redemption in total, a try-me voucher one per
    doctor, and an order has one redemption. `voucher_id` and `doctor_uid` are plain
    UUIDs, with no database link: the catalogue's rows are re-created on every snapshot
    apply (catalogue.applier), and the doctor's account may go. `version` is the version
    of the `voucher.redeemed` / `voucher.released` event last recorded for it (reserved 1,
    consumed or released 2); `amount` is what the voucher took off the order.
    """

    CROSSES_SITES = True  # AD-5: its UUID is the events' aggregate id

    RESERVED, CONSUMED, RELEASED = 'reserved', 'consumed', 'released'
    STATES = [(RESERVED, 'Reserved'), (CONSUMED, 'Consumed'), (RELEASED, 'Released')]
    LIVE_STATES = (RESERVED, CONSUMED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    voucher_id = models.UUIDField()
    voucher_type = models.CharField(max_length=32)
    doctor_uid = models.UUIDField()
    order_id = models.UUIDField()
    state = models.CharField(max_length=10, choices=STATES)
    amount = models.DecimalField(max_digits=17, decimal_places=2)
    currency = models.CharField(max_length=3)
    version = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [models.Index(fields=['voucher_id', 'state'],
                                name='voucher_red_voucher_state')]
        constraints = [
            models.UniqueConstraint(fields=['order_id'], name='voucher_red_one_per_order'),
            models.UniqueConstraint(
                fields=['voucher_id', 'doctor_uid'],
                condition=models.Q(voucher_type='try_me', state__in=['reserved', 'consumed']),
                name='voucher_red_try_me_live_per_doctor'),
            models.UniqueConstraint(
                fields=['voucher_id'],
                condition=models.Q(voucher_type='subscription',
                                   state__in=['reserved', 'consumed']),
                name='voucher_red_subscription_live_once'),
            models.CheckConstraint(
                condition=models.Q(state__in=['reserved', 'consumed', 'released']),
                name='voucher_red_state'),
        ]

    def __str__(self):
        return f'Voucher redemption {self.pk} ({self.state})'


class CaseGrant(models.Model):
    """Included cases given to a doctor by one source (epic-subscriptions 13; user decisions
    2026-11-27). Today the only source is a subscription term (`subscription_term`,
    `source_id` the purchase order's UUID): `count` is the catalogue's included cases of
    `category` (`tps_branded`) for the bought subscription, frozen when it was activated (a
    later catalogue edit never touches it). `accrual`: `upfront` (all of `count` earned at
    once) or `monthly` (`count × months paid ÷ 12`, rounded down). `starts_on` / `ends_on`:
    the term (Madrid dates, both included). One grant per source (a repeated `paid` creates
    nothing). `doctor_uid` and `source_id` are plain UUIDs, with no database link (as
    `VoucherRedemption`). Written and read only through `subscriptions.included`."""

    CROSSES_SITES = True  # AD-5: its UUID is its global id

    SUBSCRIPTION_TERM = 'subscription_term'
    SOURCES = [(SUBSCRIPTION_TERM, 'Subscription term')]
    UPFRONT = DoctorSubscription.UPFRONT
    MONTHLY = DoctorSubscription.MONTHLY
    ACCRUALS = [(UPFRONT, 'All at once'), (MONTHLY, 'Earned month by month')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    doctor_uid = models.UUIDField()
    source = models.CharField(max_length=20, choices=SOURCES)
    source_id = models.UUIDField()
    category = models.CharField(max_length=40)
    count = models.PositiveIntegerField()
    accrual = models.CharField(max_length=10, choices=ACCRUALS)
    starts_on = models.DateField()
    ends_on = models.DateField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['ends_on', 'starts_on', 'id']
        indexes = [models.Index(fields=['doctor_uid', 'ends_on'],
                                name='case_grant_doctor_ends')]
        constraints = [
            models.UniqueConstraint(fields=['source', 'source_id'],
                                    name='case_grant_one_per_source'),
            models.CheckConstraint(condition=models.Q(accrual__in=['upfront', 'monthly']),
                                   name='case_grant_accrual'),
            models.CheckConstraint(condition=models.Q(ends_on__gte=models.F('starts_on')),
                                   name='case_grant_term'),
        ]

    def __str__(self):
        return f'Case grant {self.pk} ({self.count} {self.category})'


class CaseUse(models.Model):
    """One included case used by one order line (epic-subscriptions 13), taken from one
    grant. It follows its order through the `payments.hooks` receivers of
    `subscriptions.included`: `reserved` when the order is created, `consumed` when it is
    paid, `released` when it is cancelled or expires before payment, or when a paid order is
    refunded while its grant's term still runs. A released row stays as history and never
    counts. One use per order line (`line_id` unique: two hooks racing cannot take two).
    `doctor_uid`, `order_id` and `line_id` are plain UUIDs, with no database link. `version`
    is raised by one on every change of state."""

    CROSSES_SITES = True  # AD-5

    RESERVED, CONSUMED, RELEASED = 'reserved', 'consumed', 'released'
    STATES = [(RESERVED, 'Reserved'), (CONSUMED, 'Consumed'), (RELEASED, 'Released')]
    LIVE_STATES = (RESERVED, CONSUMED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    grant = models.ForeignKey(CaseGrant, on_delete=models.PROTECT, related_name='uses')
    doctor_uid = models.UUIDField()
    order_id = models.UUIDField()
    line_id = models.UUIDField(unique=True)
    state = models.CharField(max_length=10, choices=STATES)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [models.Index(fields=['order_id'], name='case_use_order'),
                   models.Index(fields=['grant', 'state'], name='case_use_grant_state')]
        constraints = [models.CheckConstraint(
            condition=models.Q(state__in=['reserved', 'consumed', 'released']),
            name='case_use_state')]

    def __str__(self):
        return f'Case use {self.pk} ({self.state})'
