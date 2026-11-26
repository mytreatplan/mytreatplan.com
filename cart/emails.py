"""The order emails (epic-ordering 9.20): an order confirmation when the doctor's bank-transfer
order is approved, and a payment receipt with the invoice PDF when it is paid. Plain text in
the doctor's language, patients by code only. The receivers only queue `cart.tasks`
`send_order_email` after commit (nothing is sent, rendered or raised in the hook's
transaction); the task sends each email once per order."""
from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import translation

from invoices import pdf
from mytp_core.tasks import enqueue_on_commit
from payments import bank_transfer
from payments.models import Order, Payment

CONFIRMATION, RECEIPT = 'order_confirmation', 'payment_receipt'
TEMPLATES = {CONFIRMATION: 'cart/email/order_confirmation', RECEIPT: 'cart/email/payment_receipt'}


def patient_lines(order):
    return list(order.lines.exclude(patient_code='').order_by('position'))


def should_email(order):
    """A service order of a doctor with at least one patient line; nothing else is emailed."""
    return (order.kind == Order.SERVICE and order.user_id is not None
            and order.lines.exclude(patient_code='').exists())


def _render(kind, order, user, url_name, url_args, **extra):
    with translation.override(pdf.language_of(user)):
        url = f"{settings.SITE_URL}{reverse(url_name, args=url_args)}"  # in the doctor's language
        context = {'order': order, 'lines': patient_lines(order), 'url': url, **extra}
        subject = ''.join(render_to_string(f'{TEMPLATES[kind]}_subject.txt', context).splitlines())
        body = render_to_string(f'{TEMPLATES[kind]}.txt', context)
    return subject.strip(), body


def build_confirmation(order, payment=None):
    """The confirmation of a bank-transfer order, with the bank details when the company has
    them (as the 7-day reminder)."""
    if payment is None:
        payment = order.payments.filter(gateway=bank_transfer.NAME).order_by('-created_at').first()
    subject, body = _render(CONFIRMATION, order, order.user, 'checkout_transfer', [order.pk],
                            details=bank_transfer.details(order, payment))
    return EmailMessage(subject, body, to=[order.user.email])


def build_receipt(order, invoice):
    """The receipt with the invoice's codes-only PDF attached."""
    subject, body = _render(RECEIPT, order, order.user, 'invoices', [], invoice=invoice)
    message = EmailMessage(subject, body, to=[order.user.email])
    message.attach(f'{invoice.number}.pdf', pdf.render_codes_only(invoice, order.user),
                   'application/pdf')
    return message


def _queue(kind, order):
    from .tasks import send_order_email
    if should_email(order):
        enqueue_on_commit(send_order_email, kind, str(order.pk))


def on_transfer_approved(order):
    _queue(CONFIRMATION, order)


def on_paid(order):
    _queue(RECEIPT, order)
