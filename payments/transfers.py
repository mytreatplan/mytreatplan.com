"""The day-7 reminder of an unpaid bank transfer (epic-payments entry 5, user decision
2026-10-30: reminder on day 7, expiry after 14 days).

`remind_due` (the `remind_transfers` command, hourly): for every open `bank_transfer`
attempt of an order frozen 7 days ago or more (`pending_at`: choosing transfer again does not
restart the clock), not reminded yet, of an order still `pending_payment` whose session has
not ended, one plain-text email to the login address in the user's language
(`Profile.language`, else the site's), with the reference, the amount, the bank details, the
deadline and a link to the transfer page. `Payment.reminded_at` is the business row (AD-8): one
reminder per order (another attempt of it already reminded: none) even if the job runs twice
or a run fails halfway; it is set only after a successful send, so a failure is retried by
the next run. Failures are logged with the error class and the payment's UUID only (an SMTP
error may quote the address, and the bank details are never logged). An order whose account is
gone is marked reminded, with no email.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation

from accounts.profile import profile_of

from . import bank_transfer
from .models import Order, Payment
from .service import _lock

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


def _due(now):
    return Payment.objects.filter(
        gateway=bank_transfer.NAME, state=Payment.OPEN, reminded_at__isnull=True,
        order__pending_at__lte=now - bank_transfer.REMIND_AFTER, session_expires_at__gt=now,
        order__state=Order.PENDING_PAYMENT)


def remind_due(now=None):
    """Send the reminders that are due; returns how many were sent (an order without an
    account counts as none). Running it again sends none more."""
    now = now or timezone.now()
    sent = 0
    for pk in list(_due(now).values_list('pk', flat=True)):
        try:
            with transaction.atomic():
                order = _lock(Payment.objects.values_list('order_id', flat=True).get(pk=pk))
                payment = Payment.objects.select_for_update().get(pk=pk)
                if (payment.state != Payment.OPEN or payment.reminded_at is not None
                        or order.state != Order.PENDING_PAYMENT
                        or payment.session_expires_at is None
                        or payment.session_expires_at <= now
                        or Payment.objects.filter(
                            order=order, gateway=bank_transfer.NAME,
                            reminded_at__isnull=False).exists()):
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
