"""The one service of a doctor's subscription (epic-subscriptions entry 1, AD-15, AD-16,
AD-22; user decisions 2026-11-14). Every caller (signup, the Django admin, the commands,
the later screens, purchases, renewals and the admin site's assignment) goes through it;
it is the only writer of `subscriptions.models` (with the backfill migration 0002).

- `ensure_basic(user, actor)`: Basic for a doctor without a record (signup, the Django
  admin's add, `assign_basic`), with its change row; nothing for staff, superusers or a
  doctor who has one. Reads no catalogue table: signup never fails because of the
  catalogue.
- `effective(user, day=None)`: the one rule of what the doctor holds today (`Effective`):

  - no record: Basic;
  - `active` and `past_due` (grace): the record's tier;
  - `cancelled`: the record's tier up to and including `ends_on`, Basic after it;
  - `expired`: Basic.

  An active or past due record whose end date has passed still counts as its tier (moving
  the status belongs to the renewals and expiry entries). Basic is the active standard
  Basic of the doctor's country group today (`catalogue.resolver.subscription`, from the
  profile's country until fiscal entities exist): None when the country has no group or
  the group no Basic (another region's country, a blank one, no snapshot yet); the doctor
  is then on Basic and offered nothing. Another tier is the record's own catalogue row
  (`held`), whatever its state: a deactivated row is no longer sold, its holder keeps it
  until its end date (user decision 2026-11-14). A row the snapshot does not have is
  logged as an error and the doctor is priced with the group's Basic; so is a row whose
  tier or kind (standard or manual) the admin site changed under the same id.
  `can_self_switch` is False for a manual record while it counts as its own tier (an
  expired one, or a cancelled one past its end date, is Basic and the doctor may
  switch). A user who is not a client (staff, a superuser) has no record here: one kept
  from before the account was made staff is ignored, never deleted.
- `offers_for(user, day=None)` and `price_order_for(user, lines, *, vies, voucher=None,
  day=None)`: the doctor's offers and an order's price, through
  `catalogue.resolver.offers_under` and `price_order` with the profile's country, the
  effective subscription, `held` and the doctor's uid. No arithmetic here (AD-16).
- `change(user, *, expected_version, actor, reason, **fields)` and `assign_manual(user,
  subscription, *, expected_version, actor, ends_on=None)`: the versioned writes. Each
  locks the record, refuses a different version (`StaleVersion` with the current one,
  nothing written; `RecordGone` for a doctor without a record, or a staff account),
  refuses a doctor's change of a manual record that still counts (`ManualLocked`),
  checks the values (`ChangeRefused`), applies, raises the version by one, stores the
  actor and adds a change row. A doctor may never make a record manual; a doctor's
  change of the tier or subscription of a manual record that no longer counts makes it
  standard.
- `record_of(user)` (the record, or None) and `doctors_without_a_record()` (for
  `assign_basic`): reads.
- `upgrade_options(user, day=None)` (epic-subscriptions entry 2, user decisions
  2026-11-14): what the upgrade screen shows (`UpgradeOptions`): the doctor's current
  level and the levels of their country group in tier order, each the group's active
  standard row (`catalogue.resolver.subscription`) listed only when it can be bought
  (`resolver.subscription_sold` under the group's `subscription` billing rule: active,
  available, in the row's currency) and, for a paid level, has a price; with its net
  monthly and upfront prices as typed on the row (Basic is free); a button on a higher level only
  while the doctor may switch on their own (never on a manual subscription that counts).
  Nothing is on offer (`on_offer` False: the screen is skipped) for a staff account, a
  doctor without a country group, or a group that sells neither Advanced nor Premium.
- `reminder(user, day=None)` (epic-subscriptions entry 3, user decisions 2026-11-15): the
  upgrade reminder of the doctor header and home (`Reminder`: the current tier and its
  catalogue row), or None. Built on `upgrade_options` (one rule for the screen and the
  reminder): only while something is on offer, the doctor may switch on their own and
  some listed level is one they can upgrade to. A Premium, manual or staff account, or a
  group with nothing higher to sell, gets None. It does not log a drifted held row
  (`log_drift=False` down to `effective`): the other callers do.

- `monthly_possible(user)` and `activate(order)` (epic-subscriptions entry 5, user
  decisions 2026-11-26): buying Advanced or Premium. Monthly billing (a 12-month
  commitment) is possible only when the doctor's `subscription` billing rule (their
  country's group, the gateways it allows for their country) lists a gateway of the
  region that can save cards (`payments.gateways.can_save_cards`); the fake gateway never
  counts. `activate` is the `paid` receiver (`subscriptions_activate`): a paid
  SUBSCRIPTION order moves the doctor's record to the bought tier (the line's catalogue
  row and period, started the payment day in Madrid time, ending 12 months later minus
  one day, auto-renew on, active), through `change` with the doctor as the actor and the
  reason `purchase`, the change row naming the order (one per order: a repeated `paid`
  changes nothing). A record that cannot take it (a manual one that counts, a paid tier
  that counts, gone, refused) is left as it is, with a Sentry error by order UUID; the
  payment is kept (staff decide). After commit it queues the confirmation email
  (`subscriptions.tasks.send_purchase_email`).

- `counts_as_paid(user, day)`, `included_left(user, day=None)`, `grant_missing_term(pk)` and
  `records_missing_a_grant()` (epic-subscriptions 13, user decisions 2026-11-27): the
  included cases. `activate` also gives the doctor the term's grant
  (`subscriptions.included.grant_term`: the catalogue's `tps_branded` included cases of the
  bought row, frozen; upfront or monthly from the line's period; the term's dates), in the
  same transaction. A grant counts only while the record counts as a standard paid tier
  (`counts_as_paid`). `included_left` gives `(left, earned)` for the home, or None.
  `records_missing_a_grant` / `grant_missing_term` are the backfill command's
  (`grant_included_cases`): a counting paid record whose purchase order has no grant gets
  one from today's catalogue row.

Actors (AD-22): `doctor_actor(user)` (the user's uid), `admin_actor(uuid)` (an admin
user of the admin site), `SYSTEM_ACTOR` (no UUID).
"""
import logging
from uuid import UUID
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.client_sync import is_client
from catalogue import resolver
from integration.remote import RecordGone, StaleVersion
from payments import gateways

from . import included
from .models import (ADMIN, DOCTOR, SYSTEM, DoctorSubscription, SubscriptionChange)

logger = logging.getLogger(__name__)

__all__ = ['Actor', 'ChangeRefused', 'Effective', 'Level', 'ManualLocked', 'RecordGone',
           'Reminder', 'StaleVersion', 'SYSTEM_ACTOR', 'TIERS', 'UpgradeOptions',
           'activate', 'admin_actor', 'assign_manual', 'change', 'counts_as_paid',
           'doctor_actor', 'doctors_without_a_record', 'effective', 'ensure_basic',
           'grant_missing_term', 'included_left', 'monthly_possible',
           'records_missing_a_grant',
           'offers_for', 'price_order_for', 'purchase_values', 'record_of', 'reminder',
           'reset_to_basic', 'term_of',
           'upgrade_options']

BASIC = DoctorSubscription.BASIC
# The levels in order, lowest first (the catalogue's tiers).
TIERS = DoctorSubscription.TIERS
# The levels a doctor can buy: the upgrade screen is shown only when the group sells one.
PAID_TIERS = TIERS[1:]

# The reasons this entry writes (a change row's `reason`; later entries add theirs).
SIGNUP = 'signup'
BACKFILL = 'backfill'
MANUAL_ASSIGNED = 'manual_assigned'
# Epic-subscriptions entry 5: a paid subscription order (the change row names the order).
PURCHASE = 'purchase'
# A staging site's test doctor put back on Basic by `reset_to_basic` (never production).
TEST_RESET = 'test_reset'
# The billing periods of a bought subscription.
MONTHLY, UPFRONT = DoctorSubscription.MONTHLY, DoctorSubscription.UPFRONT
# How long a bought subscription runs (a monthly one is a 12-month commitment).
TERM = relativedelta(months=12)


class ManualLocked(Exception):
    """A doctor asked to change a manual record: only an admin user or the system may.
    Nothing was changed."""


class ChangeRefused(ValueError):
    """A change the record cannot take (an unknown field or value, a subscription that is
    not in the catalogue or not of the tier, an end date before the start, a doctor
    assigning a manual subscription). Nothing was changed."""


class ServiceError(Exception):
    """`ensure_basic` was called outside a transaction."""


@dataclass(frozen=True)
class Actor:
    """Who makes a change (AD-22): `type` (doctor, admin or system) and `uuid` (None for
    the system)."""
    type: str
    uuid: UUID | None = None


SYSTEM_ACTOR = Actor(SYSTEM)


def doctor_actor(user):
    """The doctor `user` as the actor."""
    return Actor(DOCTOR, user.uid)


def admin_actor(admin_uuid):
    """An admin user of the admin site (their UUID, or its text) as the actor."""
    return Actor(ADMIN, UUID(str(admin_uuid)))


def _check_actor(actor):
    if not isinstance(actor, Actor) or actor.type not in (DOCTOR, ADMIN, SYSTEM):
        raise ChangeRefused(f'not an actor: {actor!r}')
    if (actor.type == SYSTEM) != (actor.uuid is None):
        raise ChangeRefused('the system has no UUID; a doctor or an admin user has one')


@dataclass(frozen=True)
class Effective:
    """What a doctor holds on a day: the `tier`, the catalogue `subscription` it is priced
    with (a `catalogue.models.Subscription`, or None: offered nothing), whether that is
    the record's own row (`held`) rather than the group's Basic, whether the record is
    `manual`, whether the doctor may switch on their own (`can_self_switch`) and the
    `record` (None for a doctor without one)."""
    tier: str
    subscription: object
    held: bool
    manual: bool
    can_self_switch: bool
    record: object


def _country(user):
    """The doctor's billing country: the profile's (fiscal entities do not exist yet)."""
    return user.country.code if user.country else ''


def record_of(user):
    """The doctor's current record, or None. None too for a user who is not a client
    (staff or a superuser): a record kept from before the account was made staff is
    ignored, never deleted. Reads only."""
    if not is_client(user):
        return None
    return DoctorSubscription.objects.filter(doctor_id=user.pk).first()


def doctors_without_a_record():
    """The pks of the doctors (neither staff nor superusers) without a record, in order:
    those the previous release created during a deploy (`assign_basic`). Reads only."""
    return list(get_user_model().objects.filter(
        is_staff=False, is_superuser=False, subscription_record__isnull=True)
        .order_by('pk').values_list('pk', flat=True))


def _counts(record, day):
    """Whether the record counts as its own tier on `day` (the module docstring's rule);
    when not, the doctor is on Basic."""
    if record is None or record.status == DoctorSubscription.EXPIRED:
        return False
    if record.status == DoctorSubscription.CANCELLED:
        return record.ends_on is not None and day <= record.ends_on
    return True  # active, past due


def counts_as_paid(user, day=None):
    """Whether the doctor's record counts on `day` (today when None) as a standard paid
    tier (Advanced or Premium, not manual): when an included-case grant counts. Reads
    only."""
    day = day or resolver.today()
    record = record_of(user)
    return bool(_counts(record, day) and record.tier in PAID_TIERS and not record.manual)


def effective(user, day=None, *, log_drift=True):
    """The doctor's `Effective` on `day` (a date in Madrid time; today when None): see
    the module docstring. A held row missing from the catalogue or changed under the same
    id is logged as an error unless `log_drift` is False (the reminder, which runs on
    every doctor page, leaves the logging to the screen, offers and pricing). Reads
    only."""
    day = day or resolver.today()
    record = record_of(user)
    counts = _counts(record, day)
    tier = record.tier if counts else BASIC
    manual = bool(record and record.manual)
    held_row = None
    if counts and record.subscription_id is not None:
        held_row = resolver.subscription_by_id(record.subscription_id)
        if held_row is None:
            if log_drift:
                logger.error('subscription record %s: its subscription %s is not in the '
                             'catalogue; priced with the Basic of its country group',
                             record.pk, record.subscription_id)
        elif held_row.tier != record.tier or held_row.manual != record.manual:
            # Edited on the admin site under the same id: never priced as the new row.
            if log_drift:
                logger.error('subscription record %s: its subscription %s is now %s %s, '
                             'the record %s %s; priced with the Basic of its country '
                             'group', record.pk, record.subscription_id,
                             'manual' if held_row.manual else 'standard', held_row.tier,
                             'manual' if record.manual else 'standard', record.tier)
            held_row = None
    subscription = held_row or resolver.subscription(_country(user), BASIC)
    return Effective(tier=tier, subscription=subscription, held=held_row is not None,
                     manual=manual, can_self_switch=not (manual and counts), record=record)


def offers_for(user, day=None):
    """[(product, Money)] the doctor is offered on `day` (today when None): the offers of
    their effective subscription for their country (`resolver.offers_under`); [] when
    none applies."""
    day = day or resolver.today()
    held = effective(user, day)
    if held.subscription is None:
        return []
    return resolver.offers_under(held.subscription, day, country=_country(user),
                                 held=held.held)


def price_order_for(user, lines, *, vies, voucher=None, day=None):
    """The `PricedOrder` of the doctor's service order (`resolver.price_order` with their
    country, effective subscription and uid), or `resolver.OrderRefused`. Without a
    subscription (no group, or no Basic in it) it is refused as the pricing function
    refuses, in its order: `no_lines`, `country_not_served` for a country without a
    group, `no_billing_rules`, else `product_not_offered` (nothing is offered)."""
    day = day or resolver.today()
    country = _country(user)
    held = effective(user, day)
    if held.subscription is None:
        lines = list(lines)
        if vies not in resolver.VIES_STATUSES:
            raise ValueError(f'vies is one of {", ".join(resolver.VIES_STATUSES)}, '
                             f'not {vies!r}')
        if not lines:
            raise resolver.OrderRefused(resolver.NO_LINES, 'the order has no lines')
        if resolver.country_group(country) is None:
            raise resolver.OrderRefused(
                resolver.COUNTRY_NOT_SERVED,
                f'the country {country!r} is not served by this region', subject=country)
        if not resolver.has_billing_rules():
            raise resolver.OrderRefused(
                resolver.NO_BILLING_RULES,
                'the catalogue has no billing rules: no company bills')
        product = lines[0].product
        raise resolver.OrderRefused(
            resolver.PRODUCT_NOT_OFFERED,
            f'the product {product.code or product.pk} is not offered to this doctor',
            subject=product)
    return resolver.price_order(country, held.subscription, lines, vies=vies,
                                voucher=voucher, doctor=user.uid, day=day, held=held.held)


@dataclass(frozen=True)
class Level:
    """A level of the upgrade screen: its `tier`, the catalogue `subscription` row its
    name and description come from (`name_in`, `description_in`), whether it is `free`
    (Basic only), its net `monthly` price (with a 12-month commitment) and `upfront`
    price for a year (`Money`, or None when the row has none), whether it is the
    doctor's `current` level and whether the doctor `can_upgrade` to it."""
    tier: str
    subscription: object
    free: bool
    monthly: object
    upfront: object
    current: bool
    can_upgrade: bool


@dataclass(frozen=True)
class UpgradeOptions:
    """What the upgrade screen shows a doctor: their current `tier`, the catalogue row of
    it (`subscription`: the manual one for a manual record, else the standard row; None
    without one), whether that is a `manual` subscription that counts (then `can_switch`
    is False: no buttons), the listed `levels` in tier order and whether anything is
    `on_offer` (else the screen is skipped)."""
    tier: str
    subscription: object
    manual: bool
    can_switch: bool
    levels: tuple
    on_offer: bool

    def level(self, tier):
        """The listed level of `tier`, or None."""
        return next((level for level in self.levels if level.tier == tier), None)


_NOTHING = UpgradeOptions(tier=BASIC, subscription=None, manual=False, can_switch=False,
                          levels=(), on_offer=False)


def upgrade_options(user, day=None, *, log_drift=True):
    """The doctor's `UpgradeOptions` on `day` (today when None): see the module
    docstring. Nothing on offer for a user who is not a client (staff, a superuser).
    `log_drift` is passed to `effective` (False from `reminder` only). Reads only; no
    arithmetic (AD-16): the prices are the row's own."""
    if not is_client(user):
        return _NOTHING
    day = day or resolver.today()
    country = _country(user)
    if resolver.country_group(country) is None:
        return _NOTHING
    held = effective(user, day, log_drift=log_drift)
    manual = held.manual and not held.can_self_switch
    rank = TIERS.index(held.tier)
    levels = []
    for index, tier in enumerate(TIERS):
        row = resolver.subscription(country, tier)
        # Listed only when it can be bought (catalogue.pricing's own condition), and a
        # paid level only with a price (`period_not_offered` otherwise).
        if (row is None or (tier != BASIC and row.free)
                or not resolver.subscription_sold(row, resolver.subscription_rule(row))):
            continue
        levels.append(Level(
            tier=tier, subscription=row, free=tier == BASIC,
            monthly=None if tier == BASIC else row.monthly_price,
            upfront=None if tier == BASIC else row.upfront_price,
            current=not manual and index == rank,
            can_upgrade=held.can_self_switch and index > rank))
    on_offer = any(level.tier in PAID_TIERS for level in levels)
    if not on_offer:
        return _NOTHING
    if held.held:
        current = held.subscription
    elif manual:  # its row is gone or changed (logged by `effective`): named by its tier
        current = None
    else:
        current = next((level.subscription for level in levels
                        if level.tier == held.tier), None)
    return UpgradeOptions(tier=held.tier, subscription=current, manual=manual,
                          can_switch=held.can_self_switch, levels=tuple(levels),
                          on_offer=True)


@dataclass(frozen=True)
class Reminder:
    """The upgrade reminder: the doctor's current `tier` and its catalogue row
    (`subscription`, named as on the upgrade screen; None when the group lists no row of
    the tier)."""
    tier: str
    subscription: object


def reminder(user, day=None):
    """The doctor's `Reminder` on `day` (today when None), or None: see the module
    docstring. Quiet about a drifted held row (`log_drift=False`): it runs on every
    doctor page, and the upgrade screen, offers and pricing already log it. Reads only."""
    options = upgrade_options(user, day, log_drift=False)
    if not (options.on_offer and options.can_switch
            and any(level.can_upgrade for level in options.levels)):
        return None
    return Reminder(tier=options.tier, subscription=options.subscription)


# --- Writes -------------------------------------------------------------------------------

def _madrid_day(moment):
    """The Madrid date of an aware datetime."""
    return resolver.today(moment)


def _has_record(user):
    """Whether the user has a record (the check before `ensure_basic` creates one)."""
    return DoctorSubscription.objects.filter(doctor_id=user.pk).exists()


def ensure_basic(user, actor, *, reason=SIGNUP, started_on=None):
    """Give the doctor `user` a Basic record (active, no subscription id, no end date,
    no period, auto-renew off, not manual, version 1, started `started_on` or today) and
    its change row, made by `actor` for `reason`. Returns the new record, or None for a
    staff or superuser account or a doctor who has one. Reads no catalogue table. Must run
    inside `transaction.atomic()` (the caller's: signup's, the Django admin's); raises
    ServiceError otherwise."""
    if not transaction.get_connection().in_atomic_block:
        raise ServiceError('subscriptions.service.ensure_basic must run inside '
                           'transaction.atomic()')
    _check_actor(actor)
    if not is_client(user) or _has_record(user):
        return None
    now = timezone.now()
    try:
        with transaction.atomic():  # a savepoint: losing a race leaves the caller's intact
            record = DoctorSubscription.objects.create(
                doctor_id=user.pk, tier=BASIC, subscription_id=None,
                started_on=started_on or _madrid_day(now), ends_on=None, billing_period='',
                auto_renew=False, status=DoctorSubscription.ACTIVE, manual=False,
                version=1, actor_type=actor.type, actor_uuid=actor.uuid, created_at=now,
                updated_at=now)
            SubscriptionChange.objects.create(
                record=record, at=now, actor_type=actor.type, actor_uuid=actor.uuid,
                reason=reason, before={}, after=record.business_values(), version=1)
    except IntegrityError:
        if DoctorSubscription.objects.filter(doctor_id=user.pk).exists():
            return None  # another transaction gave it one meanwhile
        raise
    return record


_STATUSES = {value for value, _label in DoctorSubscription.STATUSES}
_PERIODS = {value for value, _label in DoctorSubscription.BILLING_PERIODS}
_BOOLEANS = ('auto_renew', 'manual')


def _check(record, fields, actor):
    """Check the values `fields` would give the record; raise ChangeRefused."""
    unknown = set(fields) - set(DoctorSubscription.BUSINESS_FIELDS)
    if unknown:
        raise ChangeRefused(f'not a field of the record: {", ".join(sorted(unknown))}')
    if fields.get('manual') is True and actor.type == DOCTOR:
        raise ChangeRefused('a doctor cannot make a record manual')
    values = {name: getattr(record, name) for name in DoctorSubscription.BUSINESS_FIELDS}
    values.update(fields)
    if (actor.type == DOCTOR and values['manual']
            and {'tier', 'subscription_id'} & set(fields)):
        # A doctor leaving a manual record that no longer counts (_write's lock) moves to
        # a standard subscription: the record is no longer manual.
        values['manual'] = False
    if values['tier'] not in DoctorSubscription.TIERS:
        raise ChangeRefused(f'tier is one of {", ".join(DoctorSubscription.TIERS)}')
    if values['status'] not in _STATUSES:
        raise ChangeRefused(f'status is one of {", ".join(sorted(_STATUSES))}')
    if values['billing_period'] not in _PERIODS:
        raise ChangeRefused('billing_period is "", monthly or upfront')
    for name in _BOOLEANS:
        if not isinstance(values[name], bool):
            raise ChangeRefused(f'{name} is True or False')
    for name in ('started_on', 'ends_on'):
        value = values[name]
        if (value is None and name == 'started_on') or (
                value is not None and (not isinstance(value, date)
                                       or isinstance(value, datetime))):
            raise ChangeRefused(f'{name} is a date')
    if values['ends_on'] is not None and values['ends_on'] < values['started_on']:
        raise ChangeRefused('the end date is before the start date')
    if values['status'] == DoctorSubscription.CANCELLED and values['ends_on'] is None:
        raise ChangeRefused('a cancelled record has an end date')
    subscription_id = values['subscription_id']
    if subscription_id is None:
        if values['tier'] != BASIC or values['manual']:
            raise ChangeRefused('a paid or manual record names its catalogue subscription')
        return values
    if values['tier'] == BASIC and not values['manual']:
        raise ChangeRefused('a standard Basic record holds the level only (no subscription)')
    row = resolver.subscription_by_id(subscription_id)
    if row is None:
        raise ChangeRefused(f'the subscription {subscription_id} is not in the catalogue')
    if row.tier != values['tier']:
        raise ChangeRefused(f'the subscription {row.code or row.pk} is {row.tier}, not '
                            f'{values["tier"]}')
    if row.manual != values['manual']:
        raise ChangeRefused(f'the subscription {row.code or row.pk} is '
                            f'{"manual" if row.manual else "standard"}; the record '
                            f'{"is" if values["manual"] else "is not"} manual')
    values['subscription_id'] = row.pk
    return values


def _write(user, expected_version, actor, reason, fields, order_id=None):
    """Lock, check the version and the manual lock, check the values, apply, version + 1,
    change row (naming `order_id`, a purchase's order, when given); in a transaction (the
    caller's, when there is one)."""
    _check_actor(actor)
    if not reason or len(reason) > SubscriptionChange._meta.get_field('reason').max_length:
        raise ChangeRefused('reason is a short code')
    with transaction.atomic():
        account = get_user_model().objects.filter(pk=user.pk).first()
        record = (DoctorSubscription.objects.select_for_update()
                  .filter(doctor_id=user.pk).first())
        if record is None or account is None or not is_client(account):
            raise RecordGone(user.pk)  # a staff account's record is ignored
        if record.version != expected_version:
            raise StaleVersion(record.version)
        if (record.manual and actor.type == DOCTOR
                and _counts(record, resolver.today())):
            raise ManualLocked('a manual subscription is changed by an admin user only')
        values = _check(record, fields, actor)
        before = record.business_values()
        for name, value in values.items():
            setattr(record, name, value)
        record.version += 1
        record.actor_type, record.actor_uuid = actor.type, actor.uuid
        record.updated_at = timezone.now()
        record.save()
        SubscriptionChange.objects.create(
            record=record, at=record.updated_at, actor_type=actor.type,
            actor_uuid=actor.uuid, reason=reason, before=before,
            after=record.business_values(), version=record.version, order_id=order_id)
        return record


def change(user, *, expected_version, actor, reason, **fields):
    """Change the doctor's record (`fields`: any of its business fields), made by `actor`
    for `reason` (a short code), when it is still at `expected_version`: see the module
    docstring. Returns the record."""
    return _write(user, expected_version, actor, reason, fields)


def reset_to_basic(user):
    """Put the doctor back on a standard Basic (active, no subscription, no period or end
    date, auto-renew off, not manual, started today), by the system with reason
    `TEST_RESET`, so a test doctor on a staging site can buy again; a doctor without a
    record gets one. For the `reset_to_basic` command only, which refuses outside the
    staging sites. Returns the record. RecordGone for a staff or superuser account."""
    with transaction.atomic():
        record = record_of(user)
        if record is None:
            if not is_client(user):
                raise RecordGone(user.pk)
            return ensure_basic(user, SYSTEM_ACTOR, reason=TEST_RESET)
        return _write(user, record.version, SYSTEM_ACTOR, TEST_RESET, {
            'tier': BASIC, 'subscription_id': None, 'manual': False, 'auto_renew': False,
            'status': DoctorSubscription.ACTIVE, 'billing_period': '',
            'started_on': resolver.today(), 'ends_on': None})


def assign_manual(user, subscription, *, expected_version, actor, ends_on=None):
    """Assign the manual catalogue subscription `subscription` (a
    `catalogue.models.Subscription`) to the doctor, by an admin user or the system: the
    record takes its tier and id, manual on, auto-renew off, active, nothing billed,
    started today, ending `ends_on` (None for none). A doctor actor, a standard row or a
    deactivated one is ChangeRefused. Returns the record."""
    _check_actor(actor)
    if actor.type == DOCTOR:
        raise ChangeRefused('a doctor cannot assign a manual subscription')
    if not subscription.manual:
        raise ChangeRefused(f'the subscription {subscription.code or subscription.pk} is '
                            'standard, not manual')
    if not subscription.active:
        raise ChangeRefused(f'the subscription {subscription.code or subscription.pk} is '
                            'deactivated')
    return _write(user, expected_version, actor, MANUAL_ASSIGNED, {
        'tier': subscription.tier, 'subscription_id': subscription.pk, 'manual': True,
        'auto_renew': False, 'status': DoctorSubscription.ACTIVE, 'billing_period': '',
        'started_on': resolver.today(), 'ends_on': ends_on})


# --- Buying Advanced or Premium (epic-subscriptions entry 5) ----------------------------------

def monthly_possible(user):
    """Whether the doctor may be billed monthly (a 12-month commitment, renewed by charging
    a saved card): their `subscription` billing rule (the profile country's group) lists,
    for their country, a gateway of this region that can save cards now
    (`payments.gateways.can_save_cards`). The fake gateway never counts. Reads only."""
    if not is_client(user):
        return False
    country = _country(user)
    rule = resolver.billing_rule(country, resolver.SUBSCRIPTION_CATEGORY)
    if rule is None:
        return False
    region = set(settings.REGION_PROFILE.gateways)
    return any(name != gateways.FAKE and name in region and gateways.can_save_cards(name)
               for name in rule.gateways_for(country))


def term_of(day):
    """(started_on, ends_on) of a subscription bought on `day` (a Madrid date): 12 months,
    the last day included."""
    return day, day + TERM - timedelta(days=1)


def purchase_values(order):
    """The record's business values (`business_values`: JSON-ready) right after the
    purchase `order` activated it, or None when it changed no record. Reads only."""
    change = SubscriptionChange.objects.filter(order_id=order.pk).first()
    return None if change is None else dict(change.after)


def _report(order_id, what):
    """An error to Sentry (and the log) naming the order by its UUID only (AD-23)."""
    import sentry_sdk
    message = f'subscription purchase {what}: order {order_id}'
    logger.warning(message)  # not error: the one explicit capture below reaches Sentry
    with sentry_sdk.new_scope() as scope:
        scope.set_tag('subscriptions.purchase', what)
        sentry_sdk.capture_message(message, level='error')


def activate(order):
    """The `paid` receiver of a SUBSCRIPTION order (`subscriptions_activate`): move the
    buyer's record to the bought tier (see the module docstring), inside the transition's
    transaction. Idempotent per order (the change row names it). Never raises for a
    record that cannot take it: the payment is kept and Sentry hears of it. Returns the
    record when it changed it now, else None."""
    from payments.models import Order

    if order.kind != Order.SUBSCRIPTION or order.user_id is None:
        return None
    if SubscriptionChange.objects.filter(order_id=order.pk).exists():
        return None  # activated by an earlier `paid`
    line = order.lines.order_by('position').first()
    row = resolver.subscription_by_id(line.subscription_id) if line is not None else None
    if row is None or row.tier == BASIC or row.manual:
        _report(order.pk, 'not a paid standard subscription')
        return None
    user = get_user_model().objects.filter(pk=order.user_id).first()
    if user is None or not is_client(user):
        _report(order.pk, 'not a doctor')
        return None
    ensure_basic(user, SYSTEM_ACTOR, reason=BACKFILL)  # a doctor the previous release left without one
    record = (DoctorSubscription.objects.select_for_update()
              .filter(doctor_id=user.pk).first())
    day = resolver.today(order.paid_at)
    if record is None or (_counts(record, day) and (record.manual or record.tier != BASIC)):
        # Moved since the order was made (a manual subscription, another purchase).
        _report(order.pk, 'record cannot take it')
        return None
    started_on, ends_on = term_of(day)
    try:
        record = _write(user, record.version, doctor_actor(user), PURCHASE, {
            'tier': row.tier, 'subscription_id': row.pk, 'billing_period': line.period,
            'started_on': started_on, 'ends_on': ends_on, 'auto_renew': True,
            'status': DoctorSubscription.ACTIVE}, order_id=order.pk)
    except (ChangeRefused, ManualLocked, RecordGone, StaleVersion) as refused:
        _report(order.pk, f'refused ({type(refused).__name__})')
        return None
    # Epic-subscriptions 13: the term's included cases, frozen from the catalogue now. A
    # fault here never undoes the payment (the backfill command can grant it later).
    try:
        with transaction.atomic():
            included.grant_term(
                doctor_uid=user.uid, order_id=order.pk,
                count=resolver.included_cases(row, included.DEFAULT_CATEGORY),
                accrual=line.period, starts_on=started_on, ends_on=ends_on)
    except Exception as failure:  # noqa: BLE001
        logger.error('order %s: the included cases could not be granted: %s', order.pk,
                     type(failure).__name__, exc_info=True)
        _report(order.pk, 'included cases not granted')
    from mytp_core.tasks import enqueue_on_commit

    from .tasks import send_purchase_email
    enqueue_on_commit(send_purchase_email, str(order.pk))
    return record


def open_purchase(user):
    """The doctor's open subscription order (in checkout or waiting for its payment), or
    None. Reads only."""
    from payments import service as payments
    return payments.open_subscription_order(user)


def purchase(user, subscription, period, voucher_code=''):
    """Buy `subscription` (a catalogue row) for `period` (`MONTHLY` or `UPFRONT`): the
    doctor's open subscription order when there is one (no second order), else a new one,
    made under a lock on the doctor's row. The card is saved whenever `monthly_possible`.
    Raises `pricing.OrderRefused` for a period not offered (monthly where it is not
    possible, or without a price) and for a voucher code with monthly (`voucher_period`), for a level the doctor cannot buy now (a paid tier, a
    manual record that counts: `subscription_not_offered`),
    or whatever the voucher or the pricing refuses; `payments.service.NoPaymentMethod`
    and `OrderError` pass through. A voucher covering the whole price gives an order
    already paid."""
    from catalogue import pricing
    from payments import service as payments

    possible = monthly_possible(user)
    if period == MONTHLY and (not possible or subscription.monthly_price is None):
        raise pricing.OrderRefused(pricing.PERIOD_NOT_OFFERED,
                                   'monthly is not offered to this doctor')
    if period == UPFRONT and subscription.upfront_price is None:
        raise pricing.OrderRefused(pricing.PERIOD_NOT_OFFERED, 'no upfront price')
    if period not in (MONTHLY, UPFRONT):
        raise pricing.OrderRefused(pricing.PERIOD_NOT_OFFERED, 'unknown period')
    if voucher_code and period == MONTHLY:
        raise pricing.OrderRefused(pricing.VOUCHER_PERIOD,
                                   'a voucher applies to the year upfront only')
    from . import vouchers
    voucher = vouchers.voucher_for_code(voucher_code) if voucher_code else None
    with transaction.atomic():
        # One purchase at a time per doctor: a second request waits, then finds the
        # first one's order.
        get_user_model().objects.select_for_update().get(pk=user.pk)
        pending = open_purchase(user)
        if pending is not None:
            return pending
        # Checked under the lock (a request racing a `paid`, another caller): only a doctor
        # who can switch, on Basic, may buy a level they can upgrade to.
        options = upgrade_options(user)
        found = options.level(subscription.tier) if options.on_offer else None
        if (found is None or not found.can_upgrade or not options.can_switch
                or options.tier in PAID_TIERS):
            raise pricing.OrderRefused(pricing.SUBSCRIPTION_NOT_OFFERED,
                                       'this doctor cannot buy this level now')
        return payments.create_subscription_order(
            user, subscription, period, actor=payments.doctor_actor(user),
            voucher=voucher, save_card=possible)


# --- Included cases (epic-subscriptions 13) ---------------------------------------------------

def included_left(user, day=None):
    """`(left, earned)` of the doctor's included TPS branded cases on `day` (today when
    None): see `subscriptions.included.left`. None when no grant counts (Basic, expired,
    a manual or dropped record, a term over) or the grants give none. Reads only."""
    if not is_client(user):
        return None
    found = included.left(user, day)
    if found is None or found[1] <= 0:
        return None
    return found


def _purchase_of(record):
    """The order id of the purchase that started the record's current term, or None."""
    return (SubscriptionChange.objects.filter(record=record, reason=PURCHASE,
                                              order_id__isnull=False)
            .order_by('-version').values_list('order_id', flat=True).first())


def records_missing_a_grant(day=None):
    """The doctor pks (in order) whose record counts on `day` as a standard paid tier
    bought by an order that has no included-case grant: those activated before
    epic-subscriptions 13 (`grant_included_cases`). Reads only."""
    day = day or resolver.today()
    pks = []
    for record in (DoctorSubscription.objects.filter(manual=False, tier__in=PAID_TIERS)
                   .exclude(status=DoctorSubscription.EXPIRED).order_by('doctor_id')):
        if not _counts(record, day) or record.ends_on is None:
            continue
        order_id = _purchase_of(record)
        if order_id is not None and not included.has_term_grant(order_id):
            pks.append(record.doctor_id)
    return pks


def grant_missing_term(doctor_pk, day=None):
    """Give the doctor `doctor_pk` the grant of their current term when it is missing
    (see `records_missing_a_grant`): today's catalogue number for the held row, the
    record's period and term. Returns True when a grant was created. Run it inside a
    transaction (the command's, one per doctor)."""
    day = day or resolver.today()
    record = (DoctorSubscription.objects.select_for_update()
              .filter(doctor_id=doctor_pk).select_related('doctor').first())
    if (record is None or record.manual or record.tier not in PAID_TIERS
            or not _counts(record, day) or record.ends_on is None
            or not is_client(record.doctor)):
        return False
    order_id = _purchase_of(record)
    if order_id is None or included.has_term_grant(order_id):
        return False
    row = resolver.subscription_by_id(record.subscription_id)
    if row is None:
        logger.error('subscription record %s: its subscription %s is not in the catalogue; '
                     'no included cases granted', record.pk, record.subscription_id)
        return False
    period = record.billing_period if record.billing_period in (MONTHLY, UPFRONT) else UPFRONT
    _grant, created = included.grant_term(
        doctor_uid=record.doctor.uid, order_id=order_id,
        count=resolver.included_cases(row, included.DEFAULT_CATEGORY), accrual=period,
        starts_on=record.started_on, ends_on=record.ends_on)
    return created
