"""The confirmation email of a subscription purchase (epic-subscriptions entry 5, user
decision 2026-11-26), modelled on the order emails (`cart.emails`): plain text in the
doctor's language, the level, the billing (monthly with a 12-month commitment, or a year
upfront), the amount paid, the end of the term (monthly: the next charge date), whether
the card is saved for the renewals, how to cancel (contact us, until epic-subscriptions
8.10), and the invoice's codes-only PDF attached. `service.activate` queues
`subscriptions.tasks.send_purchase_email` after commit; the task sends it once per order.
Renewal and failure emails belong to 8.6 and 8.7."""
from datetime import date

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import translation

from catalogue import resolver
from invoices import pdf
from payments import repeat

from . import service
from .levels import level_name

PURCHASE = 'subscription_purchase'
TEMPLATE = 'subscriptions/email/purchase_confirmation'
MONTH = relativedelta(months=1)


def build_confirmation(order, after, invoice):
    """The confirmation email of the paid subscription `order`, from the record's values
    right after it (`service.purchase_values`), with the invoice's codes-only PDF
    attached."""
    user = order.user
    monthly = after['billing_period'] == service.MONTHLY
    started_on = date.fromisoformat(after['started_on'])
    ends_on = date.fromisoformat(after['ends_on'])
    card_saved = bool(order.save_card and order.gateway
                      and repeat.saved_card(user, order.gateway) is not None)
    language = pdf.language_of(user)
    with translation.override(language):
        context = {
            'order': order, 'invoice': invoice, 'monthly': monthly,
            'level': level_name(resolver.subscription_by_id(after['subscription_id']),
                                after['tier'], language),
            'ends_on': ends_on,
            'next_charge': started_on + MONTH if monthly else None,
            'card_saved': card_saved,
            'support_url': f"{settings.SITE_URL}{reverse('support')}",
            'invoices_url': f"{settings.SITE_URL}{reverse('invoices')}",
        }
        subject = ''.join(render_to_string(f'{TEMPLATE}_subject.txt', context).splitlines())
        body = render_to_string(f'{TEMPLATE}.txt', context)
    message = EmailMessage(subject.strip(), body, to=[user.email])
    message.attach(f'{invoice.number}.pdf', pdf.render_codes_only(invoice, user),
                   'application/pdf')
    return message
