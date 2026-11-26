"""The order emails' task (epic-ordering 9.20). One try; a failure is logged by order UUID
and exception type only, nothing is marked sent, and the order is never changed, so staff
can run it again by hand. No automatic retry."""
import logging
import uuid

from django.tasks import task

from invoices.models import Invoice
from mytp_core.tasks import run_once
from payments import bank_transfer
from payments.models import Order, Payment

from . import emails

logger = logging.getLogger(__name__)


@task
def send_order_email(kind, order_id):
    """Send the `order_confirmation` or `payment_receipt` of the order, once. Returns True
    when it was sent now, False when it had been sent or is not due, None on a failure."""
    try:
        if kind not in emails.TEMPLATES:
            raise ValueError('unknown order email')
        order = Order.objects.select_related('user').get(pk=uuid.UUID(str(order_id)))
        if not emails.should_email(order):
            return False
        payment = None
        if kind == emails.CONFIRMATION:
            # Only while the order still waits for the transfer: a late or re-run task
            # for an order that moved on sends nothing and spends no mark.
            payment = order.payments.filter(gateway=bank_transfer.NAME,
                                            state=Payment.OPEN).first()
            if order.state != Order.PENDING_PAYMENT or payment is None:
                return False

        def send():
            if kind == emails.CONFIRMATION:
                message = emails.build_confirmation(order, payment)
            else:
                invoice = Invoice.objects.filter(order=order, kind=Invoice.ORDER).get()
                message = emails.build_receipt(order, invoice)
            message.send()
        return run_once(kind, order.pk, send)
    except Exception as failure:  # noqa: BLE001 - logged by UUID; no mark was kept
        logger.error('order email not sent: %s order %s (%s)', kind, order_id,
                     type(failure).__name__)
        return None
