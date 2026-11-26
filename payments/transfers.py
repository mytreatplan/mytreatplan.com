"""The 7-day reminder of an unpaid bank transfer (epic-payments entry 5; epic-ordering 9.17,
user decisions 2026-11-24/25: no automatic expiry, a reminder every 7 days, staff cancel).

`remind_due` (the `remind_transfers` command, hourly): for every open `bank_transfer` attempt
of an order still `pending_payment`, frozen 7 days ago or more (`pending_at`: choosing
transfer again does not restart the clock), whose latest reminder (`reminded_at`, over all the
order's transfer attempts) is none or 7 days ago or more, one plain-text email to the login
address in the user's language (`Profile.language`, else the site's), with the reference, the
amount, the bank details and a link to the transfer page. So a reminder goes at each 7-day
mark until the order is paid or cancelled; none while the doctor has switched to another
method. `Payment.reminded_at` is the business row (AD-8): one reminder per order per 7-day
period even if the job runs twice or a run fails halfway; it is set only after a successful
send, so a failure is retried by the next run. Failures are logged with the error class and
the payment's UUID only (an SMTP error may quote the address, and the bank details are never
logged). An order whose account is gone is marked reminded, with no email.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Max, OuterRef, Q, Subquery
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation

from accounts.profile import profile_of

from . import bank_transfer
from .models import Order, Payment
from .service import lock_order

logger = logging.getLogger(__name__)

SUBJECT_TEMPLATE = 'payments/email/transfer_reminder_subject.txt'
BODY_TEMPLATE = 'payments/email/transfer_reminder.txt'


def build_message(order, payment, user):
    """The reminder to `user`, rendered in their language."""
    language = profile_of(user).language  # the doctor's preferred language (Profile)
    if language not in dict(settings.LANGUAGES):
        language = settings.LANGUAGE_CODE
    with translation.override(language):
        context = {'order': order, 'details': bank_transfer.details(order, payment),
                   'url': f"{settings.SITE_URL}{reverse('checkout_transfer', args=[order.pk])}"}
        subject = ''.join(render_to_string(SUBJECT_TEMPLATE, context).splitlines())
        body = render_to_string(BODY_TEMPLATE, context)
    return EmailMessage(subject.strip(), body, to=[user.email])


def _last_reminded(order_id):
    """The latest `reminded_at` of the order's transfer attempts, or None."""
    return Payment.objects.filter(order_id=order_id, gateway=bank_transfer.NAME).aggregate(
        last=Max('reminded_at'))['last']


def _due(now):
    since = now - bank_transfer.REMIND_AFTER
    last = Subquery(Payment.objects.filter(
        order_id=OuterRef('order_id'), gateway=bank_transfer.NAME).order_by().values(
        'order_id').annotate(last=Max('reminded_at')).values('last')[:1])
    return Payment.objects.filter(
        gateway=bank_transfer.NAME, state=Payment.OPEN, order__pending_at__lte=since,
        order__state=Order.PENDING_PAYMENT).annotate(last_reminded=last).filter(
        Q(last_reminded__isnull=True) | Q(last_reminded__lte=since))


def remind_due(now=None):
    """Send the reminders that are due; returns how many were sent (an order without an
    account counts as none). Running it again within 7 days sends none more."""
    now = now or timezone.now()
    sent = 0
    for pk in list(_due(now).values_list('pk', flat=True)):
        try:
            with transaction.atomic():
                order = lock_order(Payment.objects.values_list('order_id', flat=True).get(pk=pk))
                payment = Payment.objects.select_for_update().get(pk=pk)
                since = now - bank_transfer.REMIND_AFTER
                last = _last_reminded(order.pk)
                if (payment.state != Payment.OPEN or order.state != Order.PENDING_PAYMENT
                        or order.pending_at is None or order.pending_at > since
                        or (last is not None and last > since)):
                    continue
                if order.user is None:
                    payment.reminded_at = now
                    payment.save(update_fields=['reminded_at'])
                    continue
                build_message(order, payment, order.user).send()
                payment.reminded_at = now
                payment.save(update_fields=['reminded_at'])
                sent += 1
        except Exception as failure:  # noqa: BLE001 - retried by the next run
            logger.error('transfer reminder not sent: payment %s (%s)', pk,
                         type(failure).__name__)
    return sent
