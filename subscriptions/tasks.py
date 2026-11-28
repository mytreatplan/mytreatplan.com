"""The subscription purchase's confirmation email task (epic-subscriptions entry 5), as the
order emails' (`cart.tasks`): one try; a failure is logged by order UUID and exception
type only, nothing is marked sent, and neither the order nor the record is changed, so
staff can run it again by hand. No automatic retry.

The annual renewal emails (epic-subscriptions 8.6) work the same way: the reminder and the
payment link once per record and term (`run_once`, period: the term's `ends_on`), the
receipt once per renewal order; each checks again that it is still due
(`service.renewal_notice`, `service.purchase_values`). So do the monthly emails
(epic-subscriptions 8.7): a failed attempt once per charge and attempt, a month's receipt
once per order (`service.month_notice`, `service.month_receipt`)."""
import logging
import uuid
from datetime import date

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


def _renewal_email(effect, record_id, term_ends_on, build, reminder):
    try:
        ends_on = date.fromisoformat(str(term_ends_on))
        notice = service.renewal_notice(uuid.UUID(str(record_id)), ends_on,
                                        reminder=reminder)
        if notice is None:
            return False
        return run_once(effect, record_id, lambda: build(notice).send(),
                        period=ends_on.isoformat())
    except Exception as failure:  # noqa: BLE001 - logged by UUID; no mark was kept
        logger.error('subscription renewal email not sent: %s, record %s (%s)', effect,
                     record_id, type(failure).__name__)
        return None


@task
def send_renewal_reminder(record_id, term_ends_on):
    """Send the renewal reminder of the record `record_id`'s term ending `term_ends_on`
    (ISO date), once. True when sent now, False when sent before or not due, None on a
    failure."""
    return _renewal_email(service.REMINDER_EFFECT, record_id, term_ends_on,
                          emails.build_reminder, reminder=True)


@task
def send_renewal_link(record_id, term_ends_on):
    """Send the payment link (or the not-sold notice) of the record `record_id`'s term
    ending `term_ends_on`, once. As `send_renewal_reminder`."""
    return _renewal_email(service.LINK_EFFECT, record_id, term_ends_on,
                          emails.build_payment_link, reminder=False)


@task
def send_renewal_receipt(order_id):
    """Send the receipt of the paid renewal order `order_id`, once. True when sent now,
    False when sent before or not due, None on a failure."""
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
            emails.build_renewal_receipt(order, after, invoice).send()
        return run_once(service.RECEIPT_EFFECT, order.pk, send)
    except Exception as failure:  # noqa: BLE001 - logged by UUID; no mark was kept
        logger.error('subscription renewal receipt not sent: order %s (%s)', order_id,
                     type(failure).__name__)
        return None


@task
def send_month_failed(charge_id, attempt):
    """Send the failed-attempt email (the order's payment link and the next retry, or the
    drop to Basic) of attempt `attempt` of the month charge `charge_id` (epic-subscriptions
    8.7), once (`run_once`, period: the due date and the attempt). True when sent now, False
    when sent before or not due any more (paid, another attempt since), None on a failure."""
    try:
        notice = service.month_notice(uuid.UUID(str(charge_id)), int(attempt))
        if notice is None:
            return False
        return run_once(service.MONTH_FAILED_EFFECT, charge_id,
                        lambda: emails.build_month_failed(notice).send(),
                        period=f'{notice.due_on.isoformat()}#{int(attempt)}')
    except Exception as failure:  # noqa: BLE001 - logged by UUID; no mark was kept
        logger.error('subscription month email not sent: charge %s (%s)', charge_id,
                     type(failure).__name__)
        return None


@task
def send_month_receipt(order_id):
    """Send the receipt of the paid month order `order_id` (epic-subscriptions 8.7), once.
    True when sent now, False when sent before or not due, None on a failure."""
    try:
        notice = service.month_receipt(uuid.UUID(str(order_id)))
        if notice is None:
            return False

        def send():
            invoice = Invoice.objects.filter(order=notice.order, kind=Invoice.ORDER).get()
            emails.build_month_receipt(notice, invoice).send()
        return run_once(service.MONTH_RECEIPT_EFFECT, notice.order.pk, send)
    except Exception as failure:  # noqa: BLE001 - logged by UUID; no mark was kept
        logger.error('subscription month receipt not sent: order %s (%s)', order_id,
                     type(failure).__name__)
        return None
