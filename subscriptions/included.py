"""Included cases (epic-subscriptions 13; user decisions 2026-11-27): the per-doctor ledger
of the free cases a paid subscription includes. Mirrors `subscriptions.vouchers`, per order
line instead of per order. The only module, with `subscriptions.models`, that names
`CaseGrant` and `CaseUse`: `subscriptions.service`, the hooks, the home's tag and the
backfill command go through it.

- `grant_term(...)`: a grant for one subscription term, with explicit values (the caller,
  `service.activate`, reads the catalogue once). Idempotent per source: a second call for
  the same order creates nothing and changes nothing.
- `available(user, day=None)` and `left(user, day=None)`: what the doctor may still use on
  `day` (Madrid date; today when None). Only the grant of the record's current purchase
  counts (`service.counting_purchase`: its latest `purchase` or `renewal` order whose term
  has started (8.6), while the record
  counts as a standard paid tier), and only on the days of its term. Earned: `count` (upfront) or `count × months paid // 12`
  (monthly; the months paid are the confirmed payments of the purchase order,
  `payments.service.months_paid`). Available: earned − reserved − consumed.
- `reserve(order)`, `consume(order)`, `release(order)` and `refund(order)`: the receivers
  of the order hooks `created`, `paid`, `cancelled` / `expired` and `refunded` (registered
  in `SubscriptionsConfig.ready`). Each acts on the order's lines with `included_case` set
  (one use per line), locks the doctor's grants (`select_for_update`) and is idempotent per
  line. A use is taken from the soonest-ending grant (of the line's product category) with
  something available; when none has, `reserve` raises `NotEnough` and the order is not
  created (`payments.service.HookFailed`). `consume` reserves a missing use first and,
  when nothing is available any more, logs it and lets the payment go through (a hook must
  not undo money that arrived). A consumed use is released only by `refund`, and only while
  its grant's term still runs.

No outbox events (user decision 2026-11-27): sales-finance reads the usage later.
"""
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from catalogue import resolver
from catalogue.models import Product

from .models import CaseGrant, CaseUse

logger = logging.getLogger(__name__)

RESERVED, CONSUMED, RELEASED = CaseUse.RESERVED, CaseUse.CONSUMED, CaseUse.RELEASED
UPFRONT, MONTHLY = CaseGrant.UPFRONT, CaseGrant.MONTHLY
SUBSCRIPTION_TERM = CaseGrant.SUBSCRIPTION_TERM
DEFAULT_CATEGORY = Product.TPS_BRANDED
TERM_MONTHS = 12


class NotEnough(Exception):
    """An order line asks for an included case and the doctor has none available: the
    `created` hook raises it, and the order is not created."""


# --- Grants ---------------------------------------------------------------------------------

def grant_term(*, doctor_uid, order_id, count, accrual, starts_on, ends_on,
               category=DEFAULT_CATEGORY):
    """The grant of one subscription term, bought by the order `order_id`: created with
    these values, or the existing one of that order (unchanged). Returns (grant, created).
    Run it inside the caller's transaction."""
    if accrual not in (UPFRONT, MONTHLY):
        raise ValueError(f'accrual is upfront or monthly, not {accrual!r}')
    existing = CaseGrant.objects.filter(source=SUBSCRIPTION_TERM, source_id=order_id).first()
    if existing is not None:
        return existing, False
    try:
        with transaction.atomic():  # a savepoint: a race leaves the caller's intact
            return CaseGrant.objects.create(
                doctor_uid=doctor_uid, source=SUBSCRIPTION_TERM, source_id=order_id,
                category=resolver.normal_category(category), count=max(int(count), 0),
                accrual=accrual, starts_on=starts_on, ends_on=ends_on), True
    except IntegrityError:
        existing = CaseGrant.objects.filter(source=SUBSCRIPTION_TERM,
                                            source_id=order_id).first()
        if existing is None:
            raise
        return existing, False


def has_term_grant(order_id):
    """Whether the purchase order `order_id` has its grant. Reads only."""
    return CaseGrant.objects.filter(source=SUBSCRIPTION_TERM, source_id=order_id).exists()


def earned(grant):
    """How many cases the grant has given so far: `count` (upfront) or `count × months
    paid // 12` (monthly: a case is earned only when fully paid for)."""
    if grant.accrual == UPFRONT:
        return grant.count
    from payments import service as payments
    months = min(payments.months_paid(grant.source_id), TERM_MONTHS)
    return grant.count * months // TERM_MONTHS


def _live(grant):
    return grant.uses.filter(state__in=CaseUse.LIVE_STATES).count()


def _term_sources(user, day):
    """The source ids whose grants may count on `day`: the record's current purchase
    order (none when the record does not count as a standard paid tier)."""
    from . import service
    order_id = service.counting_purchase(user, day) if user is not None else None
    return [] if order_id is None else [order_id]


def _eligible(user, day, category=None, lock=False):
    """The doctor's grants that count on `day` (soonest-ending first): the current
    purchase's (`_term_sources`), within their term. Locked when `lock`."""
    sources = _term_sources(user, day)
    if not sources:
        return []
    grants = CaseGrant.objects.filter(doctor_uid=user.uid, source=SUBSCRIPTION_TERM,
                                      source_id__in=sources, starts_on__lte=day,
                                      ends_on__gte=day)
    if category is not None:
        grants = grants.filter(category=category)
    if lock:
        grants = grants.select_for_update()
    return list(grants.order_by('ends_on', 'starts_on', 'id'))


def left(user, day=None, category=DEFAULT_CATEGORY):
    """(available, earned) of the doctor's counting grants of `category` on `day`, or None
    when no grant counts (Basic, expired, dropped, a term over). Reads only."""
    day = day or resolver.today()
    grants = _eligible(user, day, category)
    if not grants:
        return None
    total = sum(earned(grant) for grant in grants)
    used = sum(_live(grant) for grant in grants)
    return max(total - used, 0), total


def available(user, day=None, category=DEFAULT_CATEGORY):
    """How many included cases of `category` the doctor may use on `day` (0 for none)."""
    found = left(user, day, category)
    return 0 if found is None else found[0]


# --- The order hooks ------------------------------------------------------------------------

def _included_lines(order):
    return list(order.lines.filter(included_case=True).order_by('position'))


def _category(line):
    product = resolver.product(line.product_id) if line.product_id else None
    if product is None:
        raise NotEnough(f'order {line.order_id}: the line {line.pk} has no catalogue product')
    return resolver.normal_category(product.category)


def _lock_grants(doctor_uid):
    """Lock every grant of the doctor (in id order: one lock order for every hook), so
    two orders of the same doctor take and give back cases one at a time."""
    list(CaseGrant.objects.select_for_update().filter(doctor_uid=doctor_uid)
         .order_by('id').values_list('id', flat=True))


def _locked_uses(order):
    return {use.line_id: use for use in
            CaseUse.objects.select_for_update().filter(order_id=order.pk)
            .select_related('grant')}


def _lock_for(order):
    """Lock the grants of the doctors of the order's uses, then its uses."""
    for doctor_uid in set(CaseUse.objects.filter(order_id=order.pk)
                          .values_list('doctor_uid', flat=True)):
        _lock_grants(doctor_uid)
    return _locked_uses(order)


def _user(order):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(pk=order.user_id).first() if order.user_id else None


def _reserve_line(order, line, user, day):
    """Take one case for `line` from the soonest-ending grant with one available (the
    doctor's grants locked); NotEnough when none has."""
    grants = _eligible(user, day, _category(line), lock=True)
    for grant in grants:
        if earned(grant) - _live(grant) > 0:
            now = timezone.now()
            return CaseUse.objects.create(
                grant=grant, doctor_uid=user.uid, order_id=order.pk, line_id=line.pk,
                state=RESERVED, version=1, created_at=now, updated_at=now)
    raise NotEnough(f'order {order.pk}: no included case available for line {line.pk}')


def reserve(order):
    """The order was created: one `reserved` use per included line. Idempotent per line;
    an order without included lines is left alone. NotEnough when the doctor has no case
    available (the order is not created)."""
    lines = _included_lines(order)
    if not lines:
        return []
    user = _user(order)
    if user is None:
        raise NotEnough(f'order {order.pk}: an included case is used by an account')
    _lock_grants(user.uid)
    uses = _locked_uses(order)
    day = resolver.today()
    for line in lines:
        if line.pk not in uses:
            uses[line.pk] = _reserve_line(order, line, user, day)
    return [uses[line.pk] for line in lines]


def _set(use, state, now=None):
    now = now or timezone.now()
    use.state, use.version, use.updated_at = state, use.version + 1, now
    use.save(update_fields=['state', 'version', 'updated_at'])
    return use


def consume(order):
    """The order was paid: each included line's use is `consumed`. Idempotent. A line
    without a use (an order made before this release) is reserved first; when nothing is
    available any more the payment still goes through and the case is logged."""
    lines = _included_lines(order)
    if not lines:
        return []
    user = _user(order)
    if user is not None:
        _lock_grants(user.uid)
    uses = _lock_for(order)
    day = resolver.today(order.paid_at) if order.paid_at else resolver.today()
    result = []
    for line in lines:
        use = uses.get(line.pk)
        if use is None:
            try:
                with transaction.atomic():
                    if user is None:
                        raise NotEnough('no account')
                    use = _reserve_line(order, line, user, day)
            except (IntegrityError, NotEnough):
                logger.error('order %s: line %s was paid without an included case available',
                             order.pk, line.pk, exc_info=True)
                continue
        if use.state == RESERVED:
            _set(use, CONSUMED)
        elif use.state == RELEASED:
            logger.error('order %s: paid, but the included case of line %s was released',
                         order.pk, line.pk)
        result.append(use)
    return result


def release(order):
    """The order was cancelled or expired: its `reserved` uses are `released` (the cases
    are available again). A consumed use stays; a released one stays. Idempotent."""
    released = []
    for use in _lock_for(order).values():
        if use.state == RESERVED:
            released.append(_set(use, RELEASED))
    return released


def refund(order):
    """The paid order was refunded: its `consumed` uses are `released` while their grant's
    term still runs (today, Madrid, on or before `ends_on`); after it nothing comes back.
    Idempotent."""
    uses = _lock_for(order)
    if not uses:
        return []
    today = resolver.today()
    released = []
    for use in uses.values():
        if use.state == CONSUMED and today <= use.grant.ends_on:
            released.append(_set(use, RELEASED))
    return released

