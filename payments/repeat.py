"""Saved cards and repeat charges (epic-payments entry 11, AD-8, AD-23; user decisions
2026-11-26). Gateway-neutral: a gateway takes part when it registers `saves_cards` with a
`charge_saved_card` operation (`payments.gateways.can_save_cards`).

Saving: an order that asks for it (`Order.save_card`) is paid as any other; the gateway's
verified paid webhook carries the card (`WebhookEvent.card`) and `store_card` keeps it as
the doctor's one `SavedCard` of that gateway (a newer save replaces the older). A paid
order that asked to save the card and came without one is a Sentry warning by order UUID.

Charging: `charge_saved_card(order, period, actor=...)` charges the doctor's saved card
for `order` without the doctor present, as the payment of `period` (the caller's key, e.g.
`2027-01`). Idempotent per order and period: a confirmed, open or needs-attention charge of
the same period is never repeated (a database constraint too); only a declined one lets a
new charge (with a new gateway id) be made. The steps, no gateway call while a transaction
or row lock is held:

1. under the order's lock: the order is frozen as `pending_payment` with the card's
   gateway (as the doctor's Pay does: `payments.service` checks of the entity, the gateway
   and live attempts) and an open `Payment` of the period is created;
2. the gateway is called (`charge_saved_card(card, payment)`);
3. paid: `payments.service.confirm_payment` pays the order (its `paid` hook once) ->
   `paid`; declined: the attempt is `failed` with the decline code, the order stays payable
   -> `declined` (the caller decides what follows); anything else (no answer, an untrusted
   answer, a hook that failed, an amount that differs) leaves the attempt `needs_attention`
   with a Sentry error by order UUID -> `unknown`, and a rerun never charges (staff check
   the gateway's own records).

Nothing of a card but its brand, last 4 digits and expiry is ever logged or shown.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts import fiscal

from . import gateways, service
from .models import Order, Payment, SavedCard

logger = logging.getLogger(__name__)

# RepeatResult.outcome
PAID, DECLINED, UNKNOWN = 'paid', 'declined', 'unknown'
NOT_PAYABLE = service.NOT_PAYABLE
CARD_EXPIRED = 'card_expired'
REPEAT = 'repeat_charge'
# The charges of a period that block a new one.
BLOCKING = (Payment.OPEN, Payment.CONFIRMED, Payment.NEEDS_ATTENTION)


class NoSavedCard(service.OrderError):
    """The order's doctor has no saved card at a gateway the order can be charged on."""


@dataclass(frozen=True)
class RepeatResult:
    """What a repeat charge came to: `outcome` PAID, DECLINED, UNKNOWN or NOT_PAYABLE;
    `payment` the charge (None when no charge was made); `code` the decline code."""
    outcome: str
    payment: Optional[Payment] = None
    code: str = ''


# --- Saved cards ---------------------------------------------------------------------------

def saved_card(user, gateway):
    """The `SavedCard` of `user` at `gateway`, or None."""
    if user is None:
        return None
    return SavedCard.objects.filter(user_id=getattr(user, 'pk', user), gateway=gateway).first()


def _text(card, name, limit):
    value = card.get(name)
    return str(value).strip()[:limit] if value is not None else ''


def _warn(order_id, what):
    """A Sentry warning naming the order by its UUID only (AD-23)."""
    import sentry_sdk
    message = f'saved card {what}: order {order_id}'
    logger.info(message)
    with sentry_sdk.new_scope() as scope:
        scope.set_tag('payments.saved_card', what)
        sentry_sdk.capture_message(message, level='warning')


def store_card(payment, card):
    """Keep `card` (a dict: `token`, `cof_txnid`, `number` masked, `brand`, `expiry` YYMM)
    as the doctor's saved card at `payment`'s gateway, replacing an older one, in its own
    transaction, when `payment` (paid at the gateway) is of an order that asked to save the
    card and is not itself a repeat charge. A paid order that asked and came without a card
    is a Sentry warning. Returns the `SavedCard`, or None."""
    order = Order.objects.filter(pk=payment.order_id).first()
    if order is None or not order.save_card or payment.period or order.user_id is None:
        return None
    token = _text(card, 'token', 200) if isinstance(card, dict) else ''
    if not token:
        _warn(order.pk, 'not returned by the gateway')
        return None
    number = _text(card, 'number', 40)
    last4 = number[-4:] if number[-4:].isdigit() else ''
    expiry = _text(card, 'expiry', 4)
    if not (len(expiry) == 4 and expiry.isdigit() and 1 <= int(expiry[2:]) <= 12):
        expiry = ''
    values = {'token': token, 'cof_txnid': _text(card, 'cof_txnid', 200),
              'masked_number': number, 'brand': _text(card, 'brand', 20), 'last4': last4,
              'expiry': expiry, 'created_at': timezone.now()}
    for _ in range(2):  # two notifications at once: the second updates the first's row
        try:
            with transaction.atomic():
                found, _created = SavedCard.objects.select_for_update().update_or_create(
                    user_id=order.user_id, gateway=payment.gateway, defaults=values)
            return found
        except IntegrityError:
            continue
    return None


def delete_cards(user):
    """Delete every saved card of `user` (account erasure); returns how many."""
    return SavedCard.objects.filter(user_id=getattr(user, 'pk', user)).delete()[0]


# --- Repeat charges ------------------------------------------------------------------------

def _outcome_of(payment):
    if payment.state == Payment.CONFIRMED:
        return RepeatResult(PAID, payment)
    if payment.state == Payment.FAILED:
        return RepeatResult(DECLINED, payment, payment.decline_code)
    return RepeatResult(UNKNOWN, payment)


def _existing(order_id, period):
    return (Payment.objects.filter(order_id=order_id, period=period, state__in=BLOCKING)
            .order_by('-created_at').first())


def _card_for(order):
    """The gateway and saved card to charge `order` with: the order's own gateway first,
    then the others it is offered, among those that can save cards."""
    names = [name for name in gateways.offered(order) if gateways.can_save_cards(name)]
    if order.gateway in names:
        names.remove(order.gateway)
        names.insert(0, order.gateway)
    if not names:
        raise gateways.UnsupportedOperation('no gateway of the order can charge a saved card')
    for name in names:
        card = saved_card(order.user_id, name)
        if card is not None:
            return name, card
    raise NoSavedCard('the doctor has no saved card for this order')


def _open_charge(order_id, gateway, period, actor):
    """Freeze the order as `pending_payment` with `gateway` and create the open charge of
    `period`, under the order's lock; returns (payment, None), or (None, a RepeatResult)
    when a charge of the period exists or the order is not payable now."""
    now = timezone.now()
    with transaction.atomic():
        locked = service._lock(order_id)
        existing = _existing(locked.pk, period)
        if existing is not None:
            return None, _outcome_of(existing)
        if locked.state not in Order.OPEN_STATES:
            return None, RepeatResult(NOT_PAYABLE)
        if gateway not in gateways.offered(locked):
            raise service.GatewayRefused(f'the gateway {gateway!r} is not offered')
        if not service._patients_still_free(locked):
            raise service.PatientBusy('a patient of the order has a case in progress')
        if service._live_attempts(locked, now):
            raise service.AttemptOpen('a payment attempt of the order is open')
        entity = locked.fiscal_entity
        before = locked.state
        if before == Order.CHECKOUT:
            if entity is None or not fiscal.is_usable(entity):
                raise service.NoEntity('the order has no usable fiscal entity')
            if (entity.version != locked.bill_entity_version
                    or fiscal.vies_status(entity) != locked.bill_vies_status):
                raise service.EntityChanged('the fiscal entity changed since it was chosen')
            fiscal.mark_used(entity)
            locked.pending_at = now
        elif locked.bill_entity_id is None:
            raise service.NoEntity('the order has no fiscal entity')
        locked.state, locked.gateway = Order.PENDING_PAYMENT, gateway
        service._stamp(locked, actor, now)
        locked.save()
        service._change(locked, actor, service.PAYMENT_STARTED, before, now, gateway=gateway,
                        repeat=True)
        # No session end: the charge is open only while the gateway is called; one left
        # open (the process died meanwhile) blocks the order and the period for staff.
        payment = Payment.objects.create(
            order=locked, gateway=gateway, amount=locked.total, currency=locked.currency,
            period=period, session_expires_at=None, created_at=now, updated_at=now)
        return payment, None


def _needs_attention(payment, what):
    """The charge's outcome is not known: an open charge becomes `needs_attention` (an
    error to Sentry by order UUID). One the gateway's own notification settled meanwhile
    keeps that outcome."""
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.state in (Payment.CONFIRMED, Payment.FAILED):
            return _outcome_of(locked)
        if locked.state == Payment.OPEN:
            service._set_payment_state(locked, Payment.NEEDS_ATTENTION, timezone.now())
    service.report_attention(payment.order_id, f'repeat charge {what}')
    return RepeatResult(UNKNOWN, locked)


def _declined(payment, code):
    code = (code or 'declined')[:20]
    failed = service.fail_payment(payment)
    if failed.state != Payment.FAILED:
        return _outcome_of(failed)  # the gateway's notification settled it meanwhile
    Payment.objects.filter(pk=payment.pk).update(decline_code=code)
    failed.decline_code = code
    logger.info('repeat charge declined (%s): order %s', code, payment.order_id)
    return RepeatResult(DECLINED, failed, code)


def charge_saved_card(order, period, *, actor=service.SYSTEM_ACTOR):
    """Charge the doctor's saved card for `order` (an `Order` or its id) as the payment of
    `period` (a non-empty key of at most 20 characters); see the module docstring. Returns
    a `RepeatResult`. Raises `NoSavedCard` or `gateways.UnsupportedOperation` (nothing
    created), and the refusals of `payments.service` (`AttemptOpen`, `NoEntity`,
    `EntityChanged`, `GatewayRefused`, `PatientBusy`) before any charge."""
    service._check_actor(actor)
    period = str(period or '').strip()
    if not period or len(period) > 20:
        raise ValueError('a period is 1-20 characters')
    order_id = getattr(order, 'pk', order)
    existing = _existing(order_id, period)
    if existing is not None:
        return _outcome_of(existing)
    current = Order.objects.filter(pk=order_id).first()
    if current is None:
        raise service.OrderError(f'no order {order_id}')
    if current.state not in Order.OPEN_STATES:
        return RepeatResult(NOT_PAYABLE)
    gateway, card = _card_for(current)
    if card.expired(timezone.localdate()):
        logger.info('repeat charge not made: the saved card expired: order %s', current.pk)
        return RepeatResult(DECLINED, None, CARD_EXPIRED)
    try:
        payment, done = _open_charge(order_id, gateway, period, actor)
    except IntegrityError:
        # Another charge of the period (or another attempt) was opened meanwhile.
        existing = _existing(order_id, period)
        if existing is not None:
            return _outcome_of(existing)
        raise service.AttemptOpen('a payment attempt of the order is open') from None
    if done is not None:
        return done
    try:
        result = gateways.adapter(gateway).call('charge_saved_card', card, payment)
    except Exception as failure:  # noqa: BLE001 - any fault: the charge may have happened
        logger.warning('repeat charge: no trustworthy answer (%s): order %s',
                       type(failure).__name__, payment.order_id)
        return _needs_attention(payment, 'outcome unknown')
    payment.refresh_from_db()
    if result.status == gateways.FAILED:
        return _declined(payment, result.code)
    if result.status != gateways.PAID:
        return _needs_attention(payment, 'outcome unknown')
    try:
        outcome = service.confirm_payment(payment, result)
    except service.HookFailed:
        return _needs_attention(payment, 'paid but the order could not be paid')
    payment.refresh_from_db()
    if outcome == service.CONFIRMED:
        return RepeatResult(PAID, payment)
    return RepeatResult(UNKNOWN, payment)  # needs attention, reported by confirm_payment
