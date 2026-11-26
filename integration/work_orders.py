"""Work orders on the admin site (epic-case-lifecycle 10.1; mytp_core 0.36.0, resource
`work_order`, area `work_orders`): the admin site reads one case and asks for one staff
move on it.

`GET /api/v1/remote/work-orders?id=<work order uuid>` (integration.remote.remote_read): the
case's `code`, `stage`, `product_category`, `assignee` (the admin user it is assigned to) and
`move` (the last named move that changed it), with its version; no patient name or data.

`POST` to the same route (integration.remote.remote_write): `{"move": "<staff move>"}` (and
`"assignee"` for `assign`) for the work order the request's `object_id` names, at its
`expected_version`. `update` is a thin adapter (AD-15): it calls
`patients.service.apply_move` as `admin_actor(<staff uuid>)`. A move the stage no longer
allows, or one the service refuses, is answered as a 409 `stale_version` (as the bank
transfer route does): the admin site reloads the case.
"""
import logging
import uuid

from mytp_core import access
from patients import service
from patients.models import StageChange, WorkOrder

from .remote import RecordGone, StaleVersion

logger = logging.getLogger(__name__)

RESOURCE = access.WORK_ORDER


def load(object_id=None):
    """The work order `object_id`, or None (none named, not a UUID, or no such case)."""
    if object_id is None:
        return None
    try:
        pk = uuid.UUID(str(object_id))
    except ValueError:
        return None
    return WorkOrder.objects.filter(pk=pk).first()


def version(work_order):
    return work_order.version


def assignee(work_order):
    """The assigned admin user's UUID (text), or None: the `assigned` scope's check."""
    return str(work_order.assignee_uuid) if work_order.assignee_uuid else None


def build(work_order):
    """Every field of the resource, JSON-ready (the contract's `WorkOrderData`)."""
    last = (StageChange.objects.filter(work_order_id=work_order.pk).exclude(move='')
            .order_by('-at', '-id').values_list('move', flat=True).first())
    return {
        'code': work_order.code,
        'stage': work_order.stage,
        'product_category': service._product_category(work_order),
        'assignee': assignee(work_order),
        'move': last or None,
    }


def update(object_id, expected_version, changes, *, actor):
    """Apply a remote write's `changes` (`{"move": ..., "assignee": ...}`, already checked
    by the contract and the role's writable fields) to the work order `object_id` as
    `actor` (`subscriptions.service.admin_actor`), in the caller's transaction: the one
    service call, `patients.service.apply_move`. Returns the work order; RecordGone when it
    does not exist; StaleVersion when the version moved on or the move is refused."""
    work_order = load(object_id)
    if work_order is None:
        raise RecordGone(object_id)
    try:
        return service.apply_move(work_order, changes['move'], expected_version, actor,
                                  assignee=changes.get('assignee'))
    except service.StaleVersion as stale:
        raise StaleVersion(stale.current) from None
    except service.MoveRefused as refused:
        # Still at the version, yet the move is not allowed from this stage now: nothing
        # changed; the admin site reloads the case.
        logger.info('work order %s: move %s refused (%s)', work_order.pk, changes['move'],
                    type(refused).__name__)
        work_order.refresh_from_db()
        raise StaleVersion(work_order.version) from None
