"""The one service of a doctor's subscription (epic-subscriptions entry 1, AD-15, AD-16,
AD-22; user decisions 2026-11-14). Every caller (signup, the Django admin, the commands,
the later screens, purchases, renewals and the admin site's assignment) goes through it;
it is the only writer of the record and its change rows (with the backfill migration 0002).
The included cases' grant and use models are named only by `subscriptions.models` and
`subscriptions.included`: the service goes through `included`.

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

- `counting_purchase(user, day)`, `included_left(user, day=None)`, `grant_missing_term(pk)` and
  `records_missing_a_grant()` (epic-subscriptions 13, user decisions 2026-11-27): the
  included cases. `activate` also gives the doctor the term's grant
  (`subscriptions.included.grant_term`: the catalogue's `tps_branded` included cases of the
  bought row, frozen; upfront or monthly from the line's period; the term's dates), in the
  same transaction. Only the grant of the record's current purchase counts, and only while
  the record counts as a standard paid tier (`counting_purchase`). `included_left` gives `(left, earned)` for the home, or None.
  `records_missing_a_grant` / `grant_missing_term` are the backfill command's
  (`grant_included_cases`): a counting paid record whose purchase order has no grant gets
  one from today's catalogue row.

- `due_reminders(day)`, `renew_due(day)`, `drop_unrenewed(day)`, `renewal_info(user,
  day=None)`, `set_auto_renew(user, on, *, expected_version)`, `renew_now(user,
  voucher_code='')` and `renewal_notice(record_id, term_ends_on)` (epic-subscriptions 8.6,
  user decisions 2026-11-27): annual renewals. Only a standard (not manual), upfront, active
  paid-tier record takes part (`_annual`); monthly ones are 8.7's. The daily job
  (`renew_subscriptions`) queues one reminder 30 days before `ends_on` while auto-renew is on
  (`run_once` per record and term); on `ends_on` it makes the renewal order (the system as
  the actor, the held row's catalogue price that day, a year upfront, never a voucher) with
  its `Renewal` row (one per record and term: a rerun makes no second order or charge) and,
  outside any transaction, charges the saved card (`payments.repeat.charge_saved_card`); no
  card or a decline emails the order's checkout as a payment link; an unknown outcome
  emails nothing and keeps the doctor on the level while the order needs staff attention.
  The day after `ends_on`, a term not renewed (auto-renew off, unpaid, not sold) drops the
  record to Basic (reason `renewal_unpaid`). `activate` tells a renewal order by its
  `Renewal` row (`_renew`): the term `ends_on + 1 day` to 12 months later minus one day,
  auto-renew and status kept (a record dropped meanwhile is restored, with the auto-renew
  it had), reason `renewal` with the order on the change row, the new term's included
  cases, and the renewal receipt after commit. Within the last 30 days of the term the
  doctor may renew by hand (`renew_now`: a voucher allowed); paid, it is a renewal as
  above and the job charges nothing for that term.

Actors (AD-22): `doctor_actor(user)` (the user's uid), `admin_actor(uuid)` (an admin
user of the admin site), `SYSTEM_ACTOR` (no UUID).
"""
import contextvars
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
from .models import (ADMIN, DOCTOR, SYSTEM, DoctorSubscription, Renewal,
                     SubscriptionChange)

logger = logging.getLogger(__name__)

__all__ = ['Actor', 'ChangeRefused', 'Effective', 'Level', 'ManualLocked', 'RecordGone',
           'Reminder', 'StaleVersion', 'SYSTEM_ACTOR', 'TIERS', 'UpgradeOptions',
           'RenewalInfo', 'RenewalNotice',
           'activate', 'admin_actor', 'assign_manual', 'change', 'counting_purchase',
           'doctor_actor', 'doctors_without_a_record', 'drop_unrenewed', 'due_reminders',
           'effective', 'ensure_basic',
           'grant_missing_term', 'included_left', 'monthly_possible',
           'records_missing_a_grant', 'renew_due', 'renew_now', 'renewal_info',
           'renewal_notice',
           'offers_for', 'price_order_for', 'purchase_values', 'record_of', 'reminder',
           'reset_to_basic', 'set_auto_renew', 'term_of',
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
# Epic-subscriptions 8.6: a paid renewal order (the change row names the order), a term not
# renewed by its end (the record dropped to Basic) and the doctor turning auto-renew off/on.
RENEWAL = 'renewal'
RENEWAL_UNPAID = 'renewal_unpaid'
AUTO_RENEW = 'auto_renew'
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


def counting_purchase(user, day=None):
    """The order id of the purchase that started the doctor's current term (the record's
    latest `purchase` change) while the record counts on `day` (today when None) as a
    standard paid tier (Advanced or Premium, not manual); else None. Only that order's
    included-case grant counts (epic-subscriptions 13). Reads only."""
    day = day or resolver.today()
    record = record_of(user)
    if not (_counts(record, day) and record.tier in PAID_TIERS and not record.manual):
        return None
    return _purchase_of(record, day)


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
    # Epic-subscriptions 8.6: a renewal order (the job's, or "Renew now") is never a
    # purchase. Its `Renewal` row names it; one being made right now (a 0.00 order is paid
    # inside its creation) is named by `_RENEWING`.
    renewal = _renewal_of_order(order)
    if renewal is not None:
        return _renew(order, renewal)
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


def _purchase_of(record, day=None):
    """The order id of the purchase or renewal that started the record's term running on
    `day` (its latest `purchase` or `renewal` change whose term has started by then: a term
    renewed in advance starts only after the current one), or None. Without `day`, the
    latest."""
    rows = (SubscriptionChange.objects.filter(record=record, reason__in=(PURCHASE, RENEWAL),
                                              order_id__isnull=False)
            .order_by('-version').values_list('order_id', 'after'))
    for order_id, after in rows:
        started_on = (after or {}).get('started_on') or ''
        if day is None or started_on <= day.isoformat():
            return order_id
    return None


def _missing_grant(record, day):
    """(order id, catalogue row) of the grant the record's current term lacks, or None:
    the record counts on `day` as a standard paid tier of a client (not staff), bought by
    an order without a grant, and its row is in the catalogue. The one check of the dry
    run and the real run of `grant_included_cases`."""
    if (record is None or record.manual or record.tier not in PAID_TIERS
            or not _counts(record, day) or record.ends_on is None
            or not is_client(record.doctor)):
        return None
    order_id = _purchase_of(record, day)
    if order_id is None or included.has_term_grant(order_id):
        return None
    row = resolver.subscription_by_id(record.subscription_id)
    if row is None:
        logger.error('subscription record %s: its subscription %s is not in the catalogue; '
                     'no included cases granted', record.pk, record.subscription_id)
        return None
    return order_id, row


def records_missing_a_grant(day=None):
    """The doctor pks (in order) whose record lacks its term's included-case grant
    (`_missing_grant`): those activated before epic-subscriptions 13
    (`grant_included_cases`). Reads only."""
    day = day or resolver.today()
    records = (DoctorSubscription.objects.filter(manual=False, tier__in=PAID_TIERS)
               .exclude(status=DoctorSubscription.EXPIRED).select_related('doctor')
               .order_by('doctor_id'))
    return [record.doctor_id for record in records if _missing_grant(record, day)]


def grant_missing_term(doctor_pk, day=None):
    """Give the doctor `doctor_pk` the grant of their current term when it is missing
    (`_missing_grant`): today's catalogue number for the held row, the record's period and
    term. Returns True when a grant was created. Run it inside a transaction (the
    command's, one per doctor)."""
    day = day or resolver.today()
    record = (DoctorSubscription.objects.select_for_update()
              .filter(doctor_id=doctor_pk).select_related('doctor').first())
    found = _missing_grant(record, day)
    if found is None:
        return False
    order_id, row = found
    period = record.billing_period if record.billing_period in (MONTHLY, UPFRONT) else UPFRONT
    _grant, created = included.grant_term(
        doctor_uid=record.doctor.uid, order_id=order_id,
        count=resolver.included_cases(row, included.DEFAULT_CATEGORY), accrual=period,
        starts_on=record.started_on, ends_on=record.ends_on)
    return created


# --- Annual renewals (epic-subscriptions 8.6) -------------------------------------------------

# The reminder goes this many days before `ends_on`; "Renew now" is offered as long.
RENEWAL_NOTICE = timedelta(days=30)
# The `run_once` effects of the renewal emails (period: the term's `ends_on`).
REMINDER_EFFECT = 'subscription_renewal_reminder'
LINK_EFFECT = 'subscription_renewal_link'
RECEIPT_EFFECT = 'subscription_renewal_receipt'
PENDING, RENEWAL_PAID = Renewal.PENDING, Renewal.PAID
DECLINED, NO_CARD, UNKNOWN, NOT_SOLD = (Renewal.DECLINED, Renewal.NO_CARD, Renewal.UNKNOWN,
                                        Renewal.NOT_SOLD)
# The outcomes that email the doctor a payment link (or, not sold, that the term ends).
LINK_OUTCOMES = (DECLINED, NO_CARD, NOT_SOLD)

# (record pk, term_ends_on) of the renewal order being created now: its `paid` hook may fire
# inside the creation (an order a voucher covers in full), before its `Renewal` row names it.
_RENEWING = contextvars.ContextVar('subscriptions_renewing', default=None)


def _annual(record):
    """Whether the record takes part in annual renewals: a standard (not manual), upfront,
    active paid-tier record with an end date."""
    return (record is not None and not record.manual and record.tier in PAID_TIERS
            and record.billing_period == UPFRONT and record.ends_on is not None
            and record.status == DoctorSubscription.ACTIVE)


def _annual_records():
    return DoctorSubscription.objects.filter(
        manual=False, tier__in=PAID_TIERS, billing_period=UPFRONT,
        status=DoctorSubscription.ACTIVE, ends_on__isnull=False)


def _order(order_id):
    from payments.models import Order
    return Order.objects.filter(pk=order_id).first() if order_id else None


def _card_last4(user):
    """The last 4 digits of the doctor's saved card at a gateway that can charge it for a
    subscription order of theirs (their `subscription` rule's gateways, then the fake one),
    '' when none ('payment link'). Reads only; nothing of the card but the last 4."""
    from payments import repeat
    country = _country(user)
    rule = resolver.billing_rule(country, resolver.SUBSCRIPTION_CATEGORY)
    names = list(rule.gateways_for(country)) if rule is not None else []
    names.append(gateways.FAKE)
    for name in dict.fromkeys(names):
        if gateways.can_save_cards(name):
            card = repeat.saved_card(user, name)
            if card is not None:
                return card.last4 or '****'
    return ''


@dataclass(frozen=True)
class RenewalInfo:
    """The renewal line of the upgrade screen ("Your current level"): the record's `tier`
    and held catalogue row (`subscription`, None when gone), the term's `ends_on` and
    `renews_on` (the day after), `auto_renew`, the saved card's `card_last4` ('' for a
    payment link), its upfront `price` today (None without one), whether the doctor
    `can_renew_now` (the last 30 days of the term, the row still with a price) and the
    record's `version` (for `set_auto_renew`)."""
    tier: str
    subscription: object
    ends_on: date
    renews_on: date
    auto_renew: bool
    card_last4: str
    price: object
    can_renew_now: bool
    version: int


def renewal_info(user, day=None):
    """The doctor's `RenewalInfo` on `day` (today when None), or None when their record does
    not take part in annual renewals (Basic, monthly, manual, a term over). Reads only."""
    day = day or resolver.today()
    record = record_of(user)
    if not (_annual(record) and _counts(record, day) and day <= record.ends_on):
        return None
    row = resolver.subscription_by_id(record.subscription_id)
    price = row.upfront_price if row is not None else None
    return RenewalInfo(
        tier=record.tier, subscription=row, ends_on=record.ends_on,
        renews_on=record.ends_on + timedelta(days=1), auto_renew=record.auto_renew,
        card_last4=_card_last4(user), price=price,
        can_renew_now=(price is not None and row.tier == record.tier and not row.manual
                       and record.ends_on - RENEWAL_NOTICE <= day),
        version=record.version)


def set_auto_renew(user, on, *, expected_version):
    """Turn the doctor's auto-renew on or off (the upgrade screen's button), by the doctor,
    when the record is still at `expected_version`. ChangeRefused unless the record takes
    part in annual renewals and its term runs; StaleVersion, RecordGone as `change`.
    Returns the record."""
    if renewal_info(user) is None:
        raise ChangeRefused('auto-renew is turned on or off for a running annual subscription')
    return _write(user, expected_version, doctor_actor(user), AUTO_RENEW,
                  {'auto_renew': bool(on)})


def _bind(record, term_ends_on, order_id, outcome):
    """The `Renewal` row of the record's term, created or pointed at `order_id` (an
    abandoned order replaced) with `outcome`; one already naming the order is left as it
    is. Inside the caller's transaction."""
    now = timezone.now()
    renewal, created = Renewal.objects.select_for_update().get_or_create(
        record_id=getattr(record, 'pk', record), term_ends_on=term_ends_on,
        defaults={'order_id': order_id, 'outcome': outcome, 'created_at': now,
                  'updated_at': now})
    if not created and (renewal.order_id != order_id or order_id is None):
        renewal.order_id, renewal.outcome, renewal.updated_at = order_id, outcome, now
        renewal.save(update_fields=['order_id', 'outcome', 'updated_at'])
    return renewal


def _set_outcome(renewal_pk, outcome):
    """Record what the renewal came to (a paid one stays paid). Returns the row."""
    with transaction.atomic():
        renewal = Renewal.objects.select_for_update().get(pk=renewal_pk)
        if renewal.outcome != RENEWAL_PAID and renewal.outcome != outcome:
            renewal.outcome, renewal.updated_at = outcome, timezone.now()
            renewal.save(update_fields=['outcome', 'updated_at'])
        return renewal


def _create_renewal_order(user, record, row, *, actor, voucher=None):
    """The renewal order of the record's term (a year upfront of `row`), with its `Renewal`
    row; the card saved whenever a gateway of the doctor can (as a purchase). Inside the
    caller's transaction; `pricing.OrderRefused` and `payments.service` refusals pass
    through, creating nothing."""
    from payments import service as payments

    token = _RENEWING.set((record.pk, record.ends_on))
    try:
        with transaction.atomic():
            order = payments.create_subscription_order(
                user, row, UPFRONT, actor=actor, voucher=voucher,
                save_card=monthly_possible(user))
            _bind(record, record.ends_on, order.pk, PENDING)
    finally:
        _RENEWING.reset(token)
    return order


def _renewal_of_order(order):
    """The `Renewal` row of the renewal order `order` (locked), or None for a purchase."""
    renewal = Renewal.objects.select_for_update().filter(order_id=order.pk).first()
    if renewal is not None:
        return renewal
    pending = _RENEWING.get()
    if pending is None:
        return None
    record_pk, term_ends_on = pending
    record = DoctorSubscription.objects.filter(pk=record_pk).first()
    if record is None or record.doctor_id != order.user_id:
        return None
    return _bind(record, term_ends_on, order.pk, PENDING)


def _warn(subject, what):
    """A Sentry warning naming a record or an order by its UUID only (AD-23)."""
    import sentry_sdk
    message = f'subscription renewal {what}: {subject}'
    logger.warning(message)
    with sentry_sdk.new_scope() as scope:
        scope.set_tag('subscriptions.renewal', what)
        sentry_sdk.capture_message(message, level='warning')


def _auto_renew_before_drop(record, term_ends_on):
    """The auto-renew the record had when `drop_unrenewed` ended the term `term_ends_on`
    (False when it was not that drop)."""
    change = (SubscriptionChange.objects.filter(record=record, reason=RENEWAL_UNPAID)
              .order_by('-version').first())
    before = change.before if change is not None else {}
    return bool(before.get('ends_on') == term_ends_on.isoformat()
                and before.get('auto_renew'))


def _renew(order, renewal):
    """`activate` of a renewal order (the `paid` receiver, inside its transaction): the
    record runs another term from the renewed term's `ends_on + 1 day` (see the module
    docstring). Never raises for a record that cannot take it: the payment is kept and
    Sentry hears of it. Returns the record when it changed it now, else None."""
    if renewal.outcome != RENEWAL_PAID:
        renewal.outcome, renewal.updated_at = RENEWAL_PAID, timezone.now()
        renewal.save(update_fields=['outcome', 'updated_at'])
    line = order.lines.order_by('position').first()
    row = resolver.subscription_by_id(line.subscription_id) if line is not None else None
    if row is None or row.tier == BASIC or row.manual or line.period != UPFRONT:
        _report(order.pk, 'renewal not a paid standard subscription')
        return None
    record = (DoctorSubscription.objects.select_for_update().select_related('doctor')
              .filter(pk=renewal.record_id).first())
    user = record.doctor if record is not None else None
    if user is None or not is_client(user) or user.pk != order.user_id:
        _report(order.pk, 'renewal not a doctor')
        return None
    day = resolver.today()
    term = renewal.term_ends_on
    started_on, ends_on = term_of(term + timedelta(days=1))
    fields = {'tier': row.tier, 'subscription_id': row.pk, 'billing_period': UPFRONT,
              'started_on': started_on, 'ends_on': ends_on}
    counts = _counts(record, day)
    if (not record.manual and counts and record.tier == row.tier
            and record.billing_period == UPFRONT and record.ends_on == term):
        pass  # the term it renews: extended, auto-renew and status kept
    elif not (record.manual and counts) and (record.tier == BASIC or not counts):
        # Dropped to Basic (or ended) since: a late payment restores the level.
        fields.update(status=DoctorSubscription.ACTIVE, manual=False,
                      auto_renew=_auto_renew_before_drop(record, term))
    else:
        _report(order.pk, 'renewal record cannot take it')
        return None
    # The doctor for "Renew now" (they made the order), else the system.
    made_by = (order.changes.filter(event='created').values_list('actor_type', flat=True)
               .first())
    actor = doctor_actor(user) if made_by == DOCTOR else SYSTEM_ACTOR
    try:
        record = _write(user, record.version, actor, RENEWAL, fields, order_id=order.pk)
    except (ChangeRefused, ManualLocked, RecordGone, StaleVersion) as refused:
        _report(order.pk, f'renewal refused ({type(refused).__name__})')
        return None
    try:
        with transaction.atomic():
            included.grant_term(
                doctor_uid=user.uid, order_id=order.pk,
                count=resolver.included_cases(row, included.DEFAULT_CATEGORY),
                accrual=UPFRONT, starts_on=started_on, ends_on=ends_on)
    except Exception as failure:  # noqa: BLE001
        logger.error('order %s: the included cases could not be granted: %s', order.pk,
                     type(failure).__name__, exc_info=True)
        _report(order.pk, 'included cases not granted')
    from mytp_core.tasks import enqueue_on_commit

    from .tasks import send_renewal_receipt
    enqueue_on_commit(send_renewal_receipt, str(order.pk))
    return record


def renew_now(user, voucher_code=''):
    """Renew the doctor's annual subscription by hand ("Renew now", the last 30 days of the
    term): the doctor's open subscription order when there is one, else a new order of the
    held row for a year upfront with the optional subscription voucher, made under a lock
    on the doctor's row, with its `Renewal` row. Paid, it adds the next term (`_renew`) and
    the job charges nothing for it. `pricing.OrderRefused` (`subscription_not_offered`)
    outside the last 30 days or for a record that does not take part, and whatever the
    voucher or the pricing refuses; `payments.service` refusals pass through."""
    from catalogue import pricing
    from payments import service as payments

    from . import vouchers
    voucher = vouchers.voucher_for_code(voucher_code) if voucher_code else None
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        pending = open_purchase(user)
        if pending is not None:
            return pending
        info = renewal_info(user)
        if info is None or not info.can_renew_now:
            raise pricing.OrderRefused(pricing.SUBSCRIPTION_NOT_OFFERED,
                                       'this doctor cannot renew now')
        record = DoctorSubscription.objects.select_for_update().get(doctor_id=user.pk)
        return _create_renewal_order(user, record, info.subscription,
                                     actor=payments.doctor_actor(user), voucher=voucher)


def due_reminders(day=None):
    """Queue the renewal reminder of every annual record with auto-renew on whose term ends
    within 30 days of `day` (today when None), once per record and term (`run_once`, period
    `ends_on`; the task checks again). Returns how many were queued."""
    from mytp_core.tasks import effect_done, enqueue_on_commit

    from .tasks import send_renewal_reminder
    day = day or resolver.today()
    queued = 0
    for pk, ends_on in list(_annual_records().filter(
            auto_renew=True, ends_on__gt=day, ends_on__lte=day + RENEWAL_NOTICE)
            .order_by('ends_on', 'pk').values_list('pk', 'ends_on')):
        if effect_done(REMINDER_EFFECT, pk, period=ends_on.isoformat()):
            continue
        enqueue_on_commit(send_renewal_reminder, str(pk), ends_on.isoformat())
        queued += 1
    return queued


def _queue_link(record_pk, term_ends_on):
    from mytp_core.tasks import enqueue_on_commit

    from .tasks import send_renewal_link
    enqueue_on_commit(send_renewal_link, str(record_pk), term_ends_on.isoformat())


def _prepare(record_pk, day):
    """Under the record's lock: the (renewal, order) to charge for the record's term ending
    on or before `day`; NOT_SOLD when no order could be made now (the row says so, Sentry
    and the doctor hear of it); None when there is nothing to do (not due any more, or done
    by an earlier run)."""
    from catalogue import pricing
    from payments import service as payments
    from payments.models import Order

    with transaction.atomic():
        record = (DoctorSubscription.objects.select_for_update().select_related('doctor')
                  .filter(pk=record_pk).first())
        if not (_annual(record) and record.auto_renew and record.ends_on <= day
                and is_client(record.doctor)):
            return None
        renewal = (Renewal.objects.select_for_update()
                   .filter(record=record, term_ends_on=record.ends_on).first())
        order = _order(renewal.order_id) if renewal is not None else None
        if renewal is not None and renewal.outcome != PENDING:
            return None  # charged, declined, unknown or not sold by an earlier run
        if order is not None and order.state == Order.PAID:
            return None
        if order is not None and order.state in Order.OPEN_STATES:
            return renewal, order  # made by an earlier run (or by hand): charged now
        user = record.doctor
        row = resolver.subscription_by_id(record.subscription_id)
        try:
            if row is None or row.tier != record.tier or row.manual:
                raise pricing.OrderRefused(pricing.SUBSCRIPTION_NOT_OFFERED,
                                           'the held subscription is not in the catalogue')
            order = _create_renewal_order(user, record, row, actor=payments.SYSTEM_ACTOR)
        except (pricing.OrderRefused, payments.NoPaymentMethod) as refused:
            _bind(record, record.ends_on, None, NOT_SOLD)
            reason = getattr(refused, 'reason', '') or type(refused).__name__
            _warn(f'record {record.pk}', f'not sold ({reason})')
            _queue_link(record.pk, record.ends_on)
            return NOT_SOLD
        renewal = Renewal.objects.get(record=record, term_ends_on=record.ends_on)
        return renewal, order


def _charge(renewal, order):
    """Charge the doctor's saved card for the renewal order, outside any transaction; the
    outcome on the `Renewal` row; a payment link queued when there is no card or it is
    declined. Returns the outcome."""
    from payments import gateways as payment_gateways
    from payments import repeat
    from payments import service as payments

    try:
        result = repeat.charge_saved_card(order.pk, renewal.term_ends_on.isoformat(),
                                          actor=payments.SYSTEM_ACTOR)
        outcome = {repeat.PAID: RENEWAL_PAID, repeat.DECLINED: DECLINED,
                   repeat.UNKNOWN: UNKNOWN}.get(result.outcome)
    except (payments.OrderError, payment_gateways.UnsupportedOperation):
        outcome = NO_CARD  # no saved card, or the order cannot be charged without the doctor
    if outcome is None:  # not payable: paid meanwhile, or ended (the next run makes another)
        paid = _order(order.pk)
        outcome = RENEWAL_PAID if paid is not None and paid.paid_at is not None else PENDING
    with transaction.atomic():
        renewal = _set_outcome(renewal.pk, outcome)
        if renewal.outcome in LINK_OUTCOMES:
            _queue_link(renewal.record_id, renewal.term_ends_on)
    return renewal.outcome


def renew_due(day=None):
    """Renew every annual record with auto-renew on whose term ends on or before `day`
    (today when None): the order and its charge (see the module docstring), each record on
    its own, a failure logged by record UUID and skipped. Returns {outcome: count} of the
    records handled now (a rerun handles none)."""
    day = day or resolver.today()
    counts = {}
    for pk in list(_annual_records().filter(auto_renew=True, ends_on__lte=day)
                   .order_by('ends_on', 'pk').values_list('pk', flat=True)):
        try:
            found = _prepare(pk, day)
            if found is None:
                continue
            outcome = found if found == NOT_SOLD else _charge(*found)
        except Exception as failure:  # noqa: BLE001 - one record never stops the run
            logger.error('subscription renewal failed: record %s (%s)', pk,
                         type(failure).__name__, exc_info=True)
            continue
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def drop_unrenewed(day=None):
    """Drop to Basic every annual record whose term ended before `day` (today when None)
    without a paid renewal: auto-renew off, declined or unpaid, not sold. Left alone while
    the renewal order's charge needs staff attention (staff decide). By the system, reason
    `renewal_unpaid`; each record in its own transaction, a failure logged by record UUID.
    Returns how many were dropped."""
    from payments import service as payments
    from payments.models import Order

    day = day or resolver.today()
    dropped = 0
    for pk in list(_annual_records().filter(ends_on__lt=day).order_by('ends_on', 'pk')
                   .values_list('pk', flat=True)):
        try:
            with transaction.atomic():
                record = (DoctorSubscription.objects.select_for_update()
                          .select_related('doctor').filter(pk=pk).first())
                if not (_annual(record) and record.ends_on < day):
                    continue
                renewal = Renewal.objects.filter(record=record,
                                                 term_ends_on=record.ends_on).first()
                order = _order(renewal.order_id) if renewal is not None else None
                if order is not None and order.state == Order.PAID:
                    _warn(f'order {order.pk}', 'paid but not applied')
                    continue
                if order is not None and payments.needs_attention(order):
                    continue  # the charge's outcome is unknown: staff decide
                _write(record.doctor, record.version, SYSTEM_ACTOR, RENEWAL_UNPAID, {
                    'tier': BASIC, 'subscription_id': None, 'manual': False,
                    'auto_renew': False, 'status': DoctorSubscription.ACTIVE,
                    'billing_period': '', 'started_on': day, 'ends_on': None})
                dropped += 1
        except Exception as failure:  # noqa: BLE001 - one record never stops the run
            logger.error('subscription renewal drop failed: record %s (%s)', pk,
                         type(failure).__name__, exc_info=True)
    return dropped


@dataclass(frozen=True)
class RenewalNotice:
    """What a renewal email says: the doctor (`user`), the `tier` and held catalogue row
    (`subscription`, None when gone), the term's `ends_on`, the row's upfront `price` today
    (None without one), the saved card's `card_last4` ('' for a payment link),
    `auto_renew`, the renewal `order` (None before the renewal day or when none could be
    made) and the `outcome` of the `Renewal` row ('' without one)."""
    user: object
    tier: str
    subscription: object
    ends_on: date
    price: object
    card_last4: str
    auto_renew: bool
    order: object
    outcome: str


def renewal_notice(record_id, term_ends_on, *, reminder=False):
    """The `RenewalNotice` of the record `record_id`'s term ending `term_ends_on`, or None
    when no email is due: for the reminder (`reminder`), the record must still take part,
    with auto-renew on and that end date; for the payment link, the term's `Renewal` row
    must have been declined, found no card or not sold, and its order not be paid. Reads
    only."""
    from payments.models import Order

    record = (DoctorSubscription.objects.select_related('doctor')
              .filter(pk=record_id).first())
    if record is None or not is_client(record.doctor):
        return None
    renewal = Renewal.objects.filter(record=record, term_ends_on=term_ends_on).first()
    order = _order(renewal.order_id) if renewal is not None else None
    if reminder:
        if not (_annual(record) and record.auto_renew and record.ends_on == term_ends_on):
            return None
    elif (renewal is None or renewal.outcome not in LINK_OUTCOMES
          or (order is not None and order.state == Order.PAID)):
        return None
    tier = record.tier if record.tier in PAID_TIERS else None
    line = order.lines.order_by('position').first() if order is not None else None
    row_id = line.subscription_id if line is not None else (
        record.subscription_id if tier else None)
    row = resolver.subscription_by_id(row_id) if row_id else None
    if row is not None:
        tier = row.tier
    if tier is None:
        changes = (SubscriptionChange.objects.filter(record=record, reason=RENEWAL_UNPAID)
                   .order_by('-version').values_list('before', flat=True))
        tier = next((before.get('tier') for before in changes
                     if before.get('ends_on') == term_ends_on.isoformat()), None)
    return RenewalNotice(
        user=record.doctor, tier=tier or BASIC, subscription=row, ends_on=term_ends_on,
        price=row.upfront_price if row is not None else None,
        card_last4=_card_last4(record.doctor), auto_renew=record.auto_renew, order=order,
        outcome=renewal.outcome if renewal is not None else '')
