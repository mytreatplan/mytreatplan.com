"""Pending bank transfers on the admin site (epic-ordering 9.18; mytp_core 0.34.0, resource
`pending_transfer`, area `sales_orders`): the admin site lists this region's bank transfer
orders still waiting for their money, and staff confirm the payment or cancel the order.

`GET /api/v1/remote/pending-transfers` (integration.remote.remote_list): every order that is
`pending_payment` with an open `bank_transfer` attempt (as `payments.transfers._due`,
without the dates), oldest first, each logged in the access log. A row names the doctor by
the account's `uid` only (the admin site has the client record); no patient data.

`POST` to the same route (integration.remote.remote_write): `{"state": "paid"}` or
`{"state": "cancelled"}` for the order the request's `object_id` names, at its
`expected_version`. `update` turns it into the one service call (AD-15):
`payments.service.mark_transfer_paid` with the order's own total (finance types nothing,
user decision 2026-11-25) or `payments.service.cancel`, as `admin_actor(<staff uuid>)`.
The version is checked here first, under the order's lock: an order changed since it was
listed (paid or cancelled meanwhile included) is a 409 `stale_version`, never a silent
replay of the service's "already there".
"""
import logging
import uuid

from django.db import transaction
from django.db.models import Max, OuterRef, Subquery

from mytp_core import access
from mytp_core.contracts import utc_text
from mytp_core.contracts.remote import TRANSFER_CANCELLED, TRANSFER_PAID
from mytp_core.money import Money
from payments import service
from payments.bank_transfer import NAME as BANK_TRANSFER
from payments.models import Order, Payment

from .remote import RecordGone, StaleVersion

logger = logging.getLogger(__name__)

RESOURCE = access.PENDING_TRANSFER


def _last_reminder():
    return Subquery(Payment.objects.filter(
        order_id=OuterRef('pk'), gateway=BANK_TRANSFER).order_by().values(
        'order_id').annotate(last=Max('reminded_at')).values('last')[:1])


def pending():
    """The orders waiting for a bank transfer: `pending_payment` with an open transfer
    attempt, annotated with the latest reminder (`last_reminder_at`), oldest first."""
    open_transfer = Payment.objects.filter(gateway=BANK_TRANSFER,
                                           state=Payment.OPEN).values('order_id')
    return (Order.objects.filter(state=Order.PENDING_PAYMENT, pk__in=open_transfer)
            .select_related('user').annotate(last_reminder_at=_last_reminder())
            .order_by('pending_at', 'created_at', 'pk'))


def load_all(limit):
    """At most `limit` pending transfer orders (the list read)."""
    return list(pending()[:limit])


def load(object_id=None):
    """The order `object_id` (the write's record, whatever its state now: a changed order
    is then refused as stale), or None. No default record: a write names its order."""
    if object_id is None:
        return None
    try:
        pk = uuid.UUID(str(object_id))
    except ValueError:
        return None
    return (Order.objects.filter(pk=pk).select_related('user')
            .annotate(last_reminder_at=_last_reminder()).first())


def version(order):
    return order.version


def _time(value):
    return utc_text(value) if value is not None else None


def build(order):
    """Every field of the resource, JSON-ready (the contract's `PendingTransferData`)."""
    if not hasattr(order, 'last_reminder_at'):
        order = load(order.pk)
    return {
        'reference': order.reference,
        'client_uid': str(order.user.uid) if order.user_id else None,
        'company_code': order.company_code,
        'total': Money(order.total, order.currency).to_contract(),
        'pending_at': _time(order.pending_at),
        'last_reminder_at': _time(order.last_reminder_at),
        'state': order.state,
    }


def update(object_id, expected_version, changes, *, actor):
    """Apply a remote write's `changes` (`{"state": "paid" | "cancelled"}`, already checked
    by the contract and the role's writable fields) to the order `object_id`, as `actor`
    (`payments.service.admin_actor`), in a transaction (the caller's): lock the order,
    check it is still at `expected_version` (else StaleVersion; nothing changed), then
    the one service call. A refusal of the service because the order is no longer a
    pending transfer is answered as stale too. Returns the order; RecordGone when it
    does not exist."""
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=object_id).first()
        if order is None:
            raise RecordGone(object_id)
        if order.version != expected_version:
            raise StaleVersion(order.version)
        state = changes['state']
        try:
            if state == TRANSFER_PAID:
                done = service.mark_transfer_paid(order, order.total,
                                                  expected_version=expected_version,
                                                  actor=actor)
            elif state == TRANSFER_CANCELLED:
                done = service.cancel(order, expected_version=expected_version,
                                      actor=actor)
            else:
                raise ValueError(f'not a pending transfer change: {state!r}')
        except service.StaleVersion as stale:
            raise StaleVersion(stale.current) from None
        except (service.InvalidTransition, service.NoPaymentMethod,
                service.AttemptOpen) as refused:
            # Still at the version, yet no longer a pending transfer (or paying right
            # now): nothing changed; the admin site reloads its list.
            logger.info('order %s: %s refused (%s)', order.pk, state,
                        type(refused).__name__)
            raise StaleVersion(order.version) from None
        return done
