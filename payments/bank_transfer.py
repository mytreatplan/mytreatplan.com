"""The bank transfer adapter (epic-payments entry 5, user decisions 2026-11-19).

No gateway is called: paying opens a 14-day attempt (`SESSION_LIFETIME`) and the doctor is
sent to a page with the billing company's bank details, the amount and the order's
reference as the transfer concept. The order stays `pending_payment`; staff record the
transfer as received with `payments.service.mark_transfer_paid`, a reminder goes on day 7
(`payments.transfers`) and the `expire_orders` job expires the order when the attempt's
session ends. Offered only when the order's company has all its bank details
(`catalogue.BillingCompany.has_bank_details`). No settings and no secrets; the bank details
are shown to the order's own doctor only and are never logged.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.urls import reverse

from catalogue.models import BillingCompany

from . import gateways

NAME = 'bank_transfer'
SESSION_LIFETIME = timedelta(days=14)
REMIND_AFTER = timedelta(days=7)


def company_of(order):
    """The billing company of the order (looked up by its code), or None."""
    return BillingCompany.objects.filter(code=order.company_code).first()


def accepts(order):
    company = company_of(order)
    return company is not None and company.has_bank_details


def start(payment, request):
    """The transfer page of the order (nothing to create at a bank)."""
    return reverse('checkout_transfer', args=[payment.order_id])


def cancel_attempt(payment):
    """Nothing to cancel: the caller closes the attempt locally."""


def grouped_iban(iban):
    """The IBAN in groups of four, for reading."""
    compact = ''.join(iban.split())
    return ' '.join(compact[i:i + 4] for i in range(0, len(compact), 4))


def details(order, payment):
    """What the doctor needs to pay: the company's holder, IBAN (grouped), BIC and bank, the
    amount, the order's reference as the concept and the deadline (the last full day to pay:
    a date in the company's time zone, the day before the attempt's session ends), or None when
    the company has no bank details."""
    company = company_of(order)
    if company is None or not company.has_bank_details:
        return None
    deadline = payment.session_expires_at
    if deadline is not None:
        try:
            deadline = deadline.astimezone(ZoneInfo(company.time_zone))
        except (ZoneInfoNotFoundError, ValueError):
            pass
        deadline = (deadline - timedelta(days=1)).date()
    return {'holder': company.bank_account_holder, 'iban': grouped_iban(company.iban),
            'bic': company.bic, 'bank_name': company.bank_name, 'amount': order.total,
            'currency': order.currency, 'concept': order.reference, 'deadline': deadline}


def register():
    gateways.register(NAME, start, cancel_attempt=cancel_attempt, accepts=accepts,
                      session_lifetime=SESSION_LIFETIME)
