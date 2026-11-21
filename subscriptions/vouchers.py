"""Voucher redemption (epic-subscriptions entry 4, AD-20, S5; user decisions 2026-11-20):
what records that an order used a voucher, so one voucher is not used on any number of
orders. The reusable API (epic-ordering's try-me vouchers call it too):

- `voucher_for_code(code)`: the snapshot `Voucher` of a typed code, or raises
  `catalogue.pricing.OrderRefused` (`voucher_unknown`) when this region's snapshot does not
  hold it (never defined, another region's, or a blank code). Every screen that takes a
  code uses it.
- `check_use(voucher, doctor_uid)`: raises `catalogue.pricing.OrderRefused`
  (`voucher_used`) when a live redemption (reserved or consumed) blocks it: a
  subscription voucher has one live redemption in total, a try-me voucher one per doctor.
  `payments.service` calls it before an order is created, so the doctor gets a clear
  refusal; the database constraints of `VoucherRedemption` are the last word when two
  orders race (the loser fails as `payments.service.HookFailed`, no order).
- `reserve(order)`, `consume(order)` and `release(order)`: the receivers of the order
  hooks `created`, `paid`, and `cancelled` / `expired` (registered in
  `SubscriptionsConfig.ready`). Each is idempotent per `order.id`, does nothing for an
  order without a voucher, and records its event through `integration.outbox.record`
  inside the hook's transaction: `voucher.redeemed` version 1 (reserved) and 2
  (consumed), `voucher.released` version 2, aggregated by the redemption's id. A
  consumed redemption is never released. The payload has no patient data.

A reservation lasts as long as its order (24 hours in checkout, 14 days for a bank
transfer); the holder frees the voucher by cancelling the order.
"""
import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from mytp_core.contracts import VOUCHER_REDEEMED, VOUCHER_RELEASED

from catalogue import pricing, resolver
from catalogue.models import Voucher
from integration import outbox

from .models import VoucherRedemption

logger = logging.getLogger(__name__)

RESERVED, CONSUMED, RELEASED = (VoucherRedemption.RESERVED, VoucherRedemption.CONSUMED,
                                VoucherRedemption.RELEASED)
TRY_ME = 'try_me'
CENT = Decimal('0.01')


def voucher_for_code(code):
    """The snapshot voucher for a typed `code` (with or without the hyphen, any case);
    `OrderRefused` (`voucher_unknown`) when it is absent, blank included."""
    found = resolver.voucher_by_code(code) if code else None
    if found is None:
        raise pricing.OrderRefused(pricing.VOUCHER_UNKNOWN,
                                   'the voucher code is not in this region\'s catalogue')
    return found


def check_use(voucher, doctor_uid):
    """Raise `OrderRefused` (`voucher_used`) when a live redemption blocks `voucher` for
    the doctor `doctor_uid`; nothing otherwise."""
    live = VoucherRedemption.objects.filter(voucher_id=voucher.pk,
                                            state__in=VoucherRedemption.LIVE_STATES)
    if voucher.type == TRY_ME:
        live = live.filter(doctor_uid=doctor_uid)
    # Any other type is never usable (pricing refuses it): the strictest rule here.
    if live.exists():
        raise pricing.OrderRefused(
            pricing.VOUCHER_USED, f'the voucher {voucher.display_code} is already used',
            subject=voucher)


def _amount(order):
    amount = Decimal(order.voucher_taken).quantize(CENT)
    return {'amount': amount, 'currency': order.currency}


def _record(event_type, redemption, order, now):
    outbox.record(event_type, redemption.pk, redemption.version, {
        'redemption_id': redemption.pk, 'voucher_id': redemption.voucher_id,
        'voucher_type': redemption.voucher_type, 'client_uid': redemption.doctor_uid,
        'order_id': redemption.order_id, 'order_reference': order.reference,
        'state': redemption.state,
        'amount': {'amount': redemption.amount, 'currency': redemption.currency},
        'at': now})


def _locked(order):
    return VoucherRedemption.objects.select_for_update().filter(order_id=order.pk).first()


def reserve(order):
    """The order was created: hold the voucher for it (state `reserved`, event version 1).
    Idempotent; an order without a voucher is left alone. A live redemption of the same
    voucher that blocks it makes the database refuse the row (`IntegrityError`)."""
    if not order.voucher_id:
        return None
    existing = _locked(order)
    if existing is not None:
        return existing
    if order.user is None:
        raise ValueError('a voucher is reserved for an account')
    voucher = Voucher.objects.filter(pk=order.voucher_id).first()
    if voucher is None:
        raise ValueError('the voucher is not in the catalogue')
    now = timezone.now()
    redemption = VoucherRedemption.objects.create(
        voucher_id=voucher.pk, voucher_type=voucher.type, doctor_uid=order.user.uid,
        order_id=order.pk, state=RESERVED, amount=_amount(order)['amount'],
        currency=order.currency, version=1, created_at=now, updated_at=now)
    _record(VOUCHER_REDEEMED, redemption, order, now)
    return redemption


def consume(order):
    """The order was paid: the redemption is `consumed` (event version 2). Idempotent. An
    order created before redemptions existed (no row) is reserved first; when its voucher
    is held by another order meanwhile the payment still goes through, and the case is
    logged (the money has arrived: a hook must not undo it)."""
    if not order.voucher_id:
        return None
    redemption = _locked(order)
    if redemption is None:
        try:
            with transaction.atomic():
                redemption = reserve(order)
        except (IntegrityError, ValueError):
            logger.error('order %s: its voucher could not be redeemed at payment', order.pk,
                         exc_info=True)
            return None
    if redemption.state == CONSUMED:
        return redemption
    if redemption.state == RELEASED:
        logger.error('order %s: paid, but its voucher redemption was released', order.pk)
        return redemption
    now = timezone.now()
    redemption.state, redemption.version = CONSUMED, 2
    redemption.amount = _amount(order)['amount']
    redemption.updated_at = now
    redemption.save(update_fields=['state', 'version', 'amount', 'updated_at'])
    _record(VOUCHER_REDEEMED, redemption, order, now)
    return redemption


def release(order):
    """The order was cancelled or expired: a `reserved` redemption is `released` (event
    version 2) and the voucher can be used again. A consumed one stays (a refund is
    epic-payments' 7.7); a released one stays as it is. Idempotent."""
    if not order.voucher_id:
        return None
    redemption = _locked(order)
    if redemption is None or redemption.state != RESERVED:
        return redemption
    now = timezone.now()
    redemption.state, redemption.version = RELEASED, 2
    redemption.updated_at = now
    redemption.save(update_fields=['state', 'version', 'updated_at'])
    _record(VOUCHER_RELEASED, redemption, order, now)
    return redemption
