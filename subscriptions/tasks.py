"""The subscription purchase's confirmation email task (epic-subscriptions entry 5), as the
order emails' (`cart.tasks`): one try; a failure is logged by order UUID and exception
type only, nothing is marked sent, and neither the order nor the record is changed, so
staff can run it again by hand. No automatic retry."""
import logging
import uuid

from django.tasks import task

from invoices.models import Invoice
from mytp_core.tasks import run_once
from payments.models import Order

from . import emails, service

logger = logging.getLogger(__name__)


@task
def send_purchase_email(order_id):
    """Send the confirmation of the paid subscription order `order_id`, once. Returns True
    when it was sent now, False when it had been sent or is not due (not a paid
    subscription order, or one that changed no record), None on a failure."""
    try:
        order = Order.objects.select_related('user').get(pk=uuid.UUID(str(order_id)))
        if (order.kind != Order.SUBSCRIPTION or order.state != Order.PAID
                or order.user_id is None):
            return False
        after = service.purchase_values(order)
        if after is None:
            return False

        def send():
            invoice = Invoice.objects.filter(order=order, kind=Invoice.ORDER).get()
            emails.build_confirmation(order, after, invoice).send()
        return run_once(emails.PURCHASE, order.pk, send)
    except Exception as failure:  # noqa: BLE001 - logged by UUID; no mark was kept
        logger.error('subscription purchase email not sent: order %s (%s)', order_id,
                     type(failure).__name__)
        return None
