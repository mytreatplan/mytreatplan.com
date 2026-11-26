"""Staff refunds and cancellations (epic-payments entry 7, AD-7; user decisions
2026-11-21). The services the admin buttons (epic-sales-finance) will call.

`refund` refunds a paid order in full, in two phases, as entry 3 found the gateway needs:

1. Under the order lock: the order is `paid`, no refund of it is pending or waiting for staff,
   and a `Refund` row is written `pending` and committed.
2. Outside any lock: the gateway refunds (`Adapter.refund`), or nothing is called when staff
   refund by hand (`manual=True`, with a note: a bank transfer or a 0.00 order).
3. Under the lock again, in one transaction: the refund is `done`, the credit note is issued
   (`invoices.service.issue_credit_note`) and the order moves to `refunded`, firing the
   `refunded` hook.

A gateway that refuses (`GatewayError`) leaves the refund `failed`, no credit note and the
order `paid` (`RefundFailed`). A gateway that does not answer, or answers something
unknown, leaves it `needs_attention` (Sentry, by order UUID only): it is never retried
automatically, because Redsys may already have refunded, and the order stays `paid` with no
credit note, blocked for any refund (manual too), until staff resolve it (the admin site's
tools, epic-sales-finance). Only staff or the system refund; a doctor actor is
refused (`InvalidActor`).
"""
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from invoices import feed
from invoices import service as invoices
from invoices.models import Invoice

from . import gateways, service
from .models import Order, Payment, Refund

logger = logging.getLogger(__name__)

REASONS = tuple(code for code, _label in Refund.REASONS)
MAX_NOTE = 500


class RefundError(service.OrderError):
    """Base of the refusals of `refund`."""


class RefundPending(RefundError):
    """A refund of the order is pending or needs staff attention: no second one."""


class RefundFailed(RefundError):
    """The gateway refused the refund (no money moved; no credit note)."""


class InsideTransaction(RefundError):
    """`refund` was called inside a `transaction.atomic()`: the `pending` row could not be
    committed before the gateway call, and a later error would roll back the record of a
    refund the gateway may have made."""


class BadRefund(RefundError):
    """The reason is not one of REASONS, a manual refund has no note, or the order has no
    gateway payment to refund (record it as a manual refund)."""


def _check_staff(actor):
    service._check_actor(actor)
    if actor.type == service.DOCTOR:
        raise service.InvalidActor('only staff or the system refund or cancel')


def _confirmed_payment(order):
    return order.payments.filter(state=Payment.CONFIRMED).order_by('-confirmed_at').first()


def _start(order, expected_version, actor, reason, manual, note):
    """Phase 1: the `pending` row, committed before the gateway is called."""
    with transaction.atomic():
        locked = service.lock_order(order)
        if locked.state != Order.PAID:
            raise service.InvalidTransition(f'an order that is {locked.state} cannot be refunded')
        if locked.refunds.filter(state__in=(Refund.PENDING, Refund.NEEDS_ATTENTION)).exists():
            raise RefundPending('a refund of the order is pending or needs attention')
        service._check_version(locked, expected_version)
        if not Invoice.objects.filter(order=locked).exists():
            raise BadRefund('the order has no invoice to credit')
        payment = None
        if not manual:
            payment = _confirmed_payment(locked)
            found = gateways.adapter(payment.gateway) if payment is not None else None
            if found is None or not found.supports('refund'):
                raise BadRefund('the order has no payment the gateway can refund: record a '
                                'manual refund')
        now = timezone.now()
        try:
            with transaction.atomic():
                return Refund.objects.create(
                    order=locked, payment=payment, amount=locked.total,
                    currency=locked.currency, manual=manual, note=note, reason=reason,
                    actor_type=actor.type, actor_uuid=actor.uuid, created_at=now,
                    updated_at=now)
        except IntegrityError:
            raise RefundPending('a refund of the order is pending') from None


def _set(refund_id, state, **fields):
    """Set the state of the refund in its own short transaction."""
    with transaction.atomic():
        row = Refund.objects.select_for_update().get(pk=refund_id)
        row.state, row.updated_at = state, timezone.now()
        for name, value in fields.items():
            setattr(row, name, value)
        row.save()
        return row


def _call_gateway(refund):
    payment = refund.payment
    return gateways.adapter(payment.gateway).call('refund', payment, refund.amount)


def _settle(refund_id, actor, gateway_refund_id):
    """Phase 3: one transaction under the order's lock."""
    with transaction.atomic():
        order_id = Refund.objects.values_list('order_id', flat=True).get(pk=refund_id)
        locked = service.lock_order(order_id)  # the order first, then the refund (as `cancel`)
        row = Refund.objects.select_for_update().get(pk=refund_id)
        if row.state != Refund.PENDING:
            return row, locked
        invoice = Invoice.objects.get(order=locked)
        note = invoices.issue_credit_note(invoice, reason=row.reason, actor=actor)
        now = timezone.now()
        row.state, row.credit_note = Refund.DONE, note
        row.gateway_refund_id, row.completed_at, row.updated_at = gateway_refund_id, now, now
        row.save()
        feed.send_refund(row)
        locked = service._move(locked, Order.REFUNDED, 'refunded', (Order.PAID,),
                               'refunded_at', expected_version=locked.version, actor=actor,
                               now=now)
        return row, locked


def refund(order, *, expected_version, actor, reason, manual=False, note=''):
    """Refund the paid `order` in full (see the module's docstring). Returns the `Refund`:
    `done` (the credit note is `refund.credit_note`, the order is `refunded`) or
    `needs_attention` (the gateway gave no usable answer). Raises `InvalidActor`,
    `InvalidTransition` (the order is not paid), `RefundPending`, `StaleVersion`,
    `BadRefund`, `RefundFailed` (the gateway refused), `HookFailed` or
    `invoices.service.NumberingOnHold` (numbering is on hold after a restore:
    refused before anything is written or sent), `invoices.service.InvoiceError` (the settlement rolled back: the refund is `failed`,
    or `needs_attention` when money moved), `InsideTransaction` (called inside an atomic
    block: nothing is written)."""
    if transaction.get_connection().in_atomic_block:
        raise InsideTransaction('refund must run outside transaction.atomic(): the pending '
                                'row is committed before the gateway is called')
    _check_staff(actor)
    note = (note or '').strip()
    if reason not in REASONS:
        raise BadRefund(f'the reason is one of {", ".join(REASONS)}')
    if manual and not note:
        raise BadRefund('a refund made by hand needs a note')
    if len(note) > MAX_NOTE:
        raise BadRefund('the note is too long')
    if invoices.numbering_on_hold():
        # Before any row or gateway call: the credit note could not be numbered afterwards.
        raise invoices.NumberingOnHold('numbering is on hold after a restore: no refund now')
    pending = _start(order, expected_version, actor, reason, manual, note)
    order_id, gateway_refund_id = pending.order_id, ''
    if not manual:
        try:
            gateway_refund_id = str(_call_gateway(pending) or '')[:100]
        except gateways.GatewayUnknown as failure:
            return _attention(pending, order_id, type(failure).__name__)
        except gateways.GatewayError:
            _set(pending.pk, Refund.FAILED)
            raise RefundFailed('the gateway refused the refund') from None
        except Exception as failure:  # noqa: BLE001 - any other fault: the answer is unknown
            return _attention(pending, order_id, type(failure).__name__)
    try:
        done, _order = _settle(pending.pk, actor, gateway_refund_id)
    except Exception as failure:
        logger.error('order %s: a refund could not be settled: %s', order_id,
                     type(failure).__name__, exc_info=True)
        if manual:
            _set(pending.pk, Refund.FAILED)
        else:  # the gateway refunded: staff must finish it
            _set(pending.pk, Refund.NEEDS_ATTENTION, gateway_refund_id=gateway_refund_id)
            service.report_attention(order_id, 'refund done at the gateway but not settled')
        raise
    return done


def _attention(pending, order_id, why):
    logger.warning('order %s: a refund got no usable answer (%s)', order_id, why)
    service.report_attention(order_id, 'refund has no usable gateway answer')
    return _set(pending.pk, Refund.NEEDS_ATTENTION)


def staff_cancel(order, *, expected_version, actor):
    """Staff (or the system) cancel an unpaid order: `service.cancel`, refusing a doctor
    actor (`InvalidActor`). A paid order is refused (`InvalidTransition`: it leaves `paid`
    only through a refund); a live card session refuses (`AttemptOpen`)."""
    _check_staff(actor)
    return service.cancel(order, expected_version=expected_version, actor=actor)
