"""The bank transfer adapter (epic-payments entry 5, user decisions 2026-11-19; epic-ordering
9.17, user decisions 2026-11-24/25).

No gateway is called: paying opens an attempt with no session end and the doctor is sent to
a page with the billing company's bank details, the amount and the order's reference as the
transfer concept. The order stays `pending_payment` until the transfer is received: staff
record it with `payments.service.mark_transfer_paid`, or the order is cancelled (by the doctor
or by staff). It never expires automatically; a reminder goes every 7 days (`REMIND_AFTER`,
`payments.transfers`) while the transfer attempt is open. Offered only when the order's
company has all its bank details (`catalogue.BillingCompany.has_bank_details`). No settings
and no secrets; the bank details are shown to the order's own doctor only and are never
logged.
"""
from datetime import timedelta

from django.urls import reverse

from catalogue.models import BillingCompany

from . import gateways

NAME = 'bank_transfer'
# The interval of the reminders: every 7 days from the order's freeze (`pending_at`).
REMIND_AFTER = timedelta(days=7)


def company_of(order):
    """The billing company of the order (looked up by its code, an active one first: a
    deactivated company keeps its code), or None."""
    return (BillingCompany.objects.filter(code=order.company_code or '')
            .order_by('-active', 'id').first())


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
    amount and the order's reference as the concept (no deadline: the order stays pending
    until the transfer is received), or None when the company has no bank details."""
    company = company_of(order)
    if company is None or not company.has_bank_details:
        return None
    return {'holder': company.bank_account_holder, 'iban': grouped_iban(company.iban),
            'bic': company.bic, 'bank_name': company.bank_name, 'amount': order.total,
            'currency': order.currency, 'concept': order.reference}


def register():
    # No session lifetime: `payments.service` opens a transfer attempt with no session end.
    gateways.register(NAME, start, cancel_attempt=cancel_attempt, accepts=accepts)
