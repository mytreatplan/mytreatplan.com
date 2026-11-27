"""The confirmation email of a subscription purchase (epic-subscriptions entry 5, user
decision 2026-11-26), modelled on the order emails (`cart.emails`): plain text in the
doctor's language, the level, the billing (monthly with a 12-month commitment, or a year
upfront), the amount paid, the end of the term (monthly: the next charge date), whether
the card is saved for the renewals, how to cancel (contact us, until epic-subscriptions
8.10), and the invoice's codes-only PDF attached. `service.activate` queues
`subscriptions.tasks.send_purchase_email` after commit; the task sends it once per order.

The annual renewal emails (epic-subscriptions 8.6, user decisions 2026-11-27), plain text in
the doctor's language, sent once by `subscriptions.tasks`:

- `build_reminder`: 30 days before the end of the term with auto-renew on: the renewal
  date, the held subscription's upfront price that day (+ VAT where applicable), the saved
  card's last 4 digits or "payment link", and how to turn auto-renew off (the upgrade
  screen).
- `build_renewal_receipt`: a paid renewal: the level, the amount paid, the new end of the
  term, the order, and the invoice's codes-only PDF attached (as the purchase email).
- `build_payment_link`: no saved card or a declined charge: the renewal order's checkout
  (absolute, login required) to pay within a day, the level kept until the end of the
  term and Basic after it until paid; or, when the subscription is no longer sold, that it
  ends with the term (no link). Monthly failures are 8.7's."""
from datetime import date, timedelta

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
REMINDER_TEMPLATE = 'subscriptions/email/renewal_reminder'
RECEIPT_TEMPLATE = 'subscriptions/email/renewal_receipt'
LINK_TEMPLATE = 'subscriptions/email/renewal_payment_link'
MONTH = relativedelta(months=1)
DAY = timedelta(days=1)


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


def _render(template, user, build):
    """(subject, body) of `template` in the doctor's language; `build(language)` gives the
    context (its URLs reversed in that language)."""
    language = pdf.language_of(user)
    with translation.override(language):
        context = build(language)
        subject = ''.join(render_to_string(f'{template}_subject.txt', context).splitlines())
        body = render_to_string(f'{template}.txt', context)
    return subject.strip(), body


def _url(name, *args):
    return f"{settings.SITE_URL}{reverse(name, args=args)}"


def build_reminder(notice):
    """The renewal reminder of `notice` (a `service.RenewalNotice`)."""
    def build(language):
        return {
            'level': level_name(notice.subscription, notice.tier, language),
            'ends_on': notice.ends_on,
            'price': notice.price, 'card_last4': notice.card_last4,
            'upgrade_url': _url('upgrade'), 'support_url': _url('support'),
        }
    subject, body = _render(REMINDER_TEMPLATE, notice.user, build)
    return EmailMessage(subject, body, to=[notice.user.email])


def build_payment_link(notice):
    """The payment link (or, not sold, the end of the subscription) of `notice`."""
    def build(language):
        order = notice.order
        return {
            'level': level_name(notice.subscription, notice.tier, language),
            'ends_on': notice.ends_on, 'basic_from': notice.ends_on + DAY,
            'declined': notice.outcome == service.DECLINED,
            'link': _url('checkout', order.pk) if order is not None else '',
            'order': order,
            'upgrade_url': _url('upgrade'), 'support_url': _url('support'),
        }
    subject, body = _render(LINK_TEMPLATE, notice.user, build)
    return EmailMessage(subject, body, to=[notice.user.email])


def build_renewal_receipt(order, after, invoice):
    """The receipt of the paid renewal `order`, from the record's values right after it
    (`service.purchase_values`), with the invoice's codes-only PDF attached."""
    user = order.user

    def build(language):
        return {
            'order': order, 'invoice': invoice,
            'level': level_name(resolver.subscription_by_id(after['subscription_id']),
                                after['tier'], language),
            'started_on': date.fromisoformat(after['started_on']),
            'ends_on': date.fromisoformat(after['ends_on']),
            'invoices_url': _url('invoices'), 'upgrade_url': _url('upgrade'),
        }
    subject, body = _render(RECEIPT_TEMPLATE, user, build)
    message = EmailMessage(subject, body, to=[user.email])
    message.attach(f'{invoice.number}.pdf', pdf.render_codes_only(invoice, user),
                   'application/pdf')
    return message
