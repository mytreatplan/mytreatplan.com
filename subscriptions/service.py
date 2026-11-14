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

Actors (AD-22): `doctor_actor(user)` (the user's uid), `admin_actor(uuid)` (an admin
user of the admin site), `SYSTEM_ACTOR` (no UUID).
"""
import logging
from uuid import UUID
from dataclasses import dataclass
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.client_sync import is_client
from catalogue import resolver
from integration.remote import RecordGone, StaleVersion

from .models import (ADMIN, DOCTOR, SYSTEM, DoctorSubscription, SubscriptionChange)

logger = logging.getLogger(__name__)

__all__ = ['Actor', 'ChangeRefused', 'Effective', 'ManualLocked', 'RecordGone',
           'StaleVersion', 'SYSTEM_ACTOR', 'admin_actor', 'assign_manual', 'change',
           'doctor_actor', 'doctors_without_a_record', 'effective', 'ensure_basic',
           'offers_for', 'price_order_for', 'record_of']

BASIC = DoctorSubscription.BASIC

# The reasons this entry writes (a change row's `reason`; later entries add theirs).
SIGNUP = 'signup'
BACKFILL = 'backfill'
MANUAL_ASSIGNED = 'manual_assigned'


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


def effective(user, day=None):
    """The doctor's `Effective` on `day` (a date in Madrid time; today when None): see
    the module docstring. Reads only."""
    day = day or resolver.today()
    record = record_of(user)
    counts = _counts(record, day)
    tier = record.tier if counts else BASIC
    manual = bool(record and record.manual)
    held_row = None
    if counts and record.subscription_id is not None:
        held_row = resolver.subscription_by_id(record.subscription_id)
        if held_row is None:
            logger.error('subscription record %s: its subscription %s is not in the '
                         'catalogue; priced with the Basic of its country group',
                         record.pk, record.subscription_id)
        elif held_row.tier != record.tier or held_row.manual != record.manual:
            # Edited on the admin site under the same id: never priced as the new row.
            logger.error('subscription record %s: its subscription %s is now %s %s, the '
                         'record %s %s; priced with the Basic of its country group',
                         record.pk, record.subscription_id,
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


def _write(user, expected_version, actor, reason, fields):
    """Lock, check the version and the manual lock, check the values, apply, version + 1,
    change row; in a transaction (the caller's, when there is one)."""
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
            after=record.business_values(), version=record.version)
        return record


def change(user, *, expected_version, actor, reason, **fields):
    """Change the doctor's record (`fields`: any of its business fields), made by `actor`
    for `reason` (a short code), when it is still at `expected_version`: see the module
    docstring. Returns the record."""
    return _write(user, expected_version, actor, reason, fields)


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
