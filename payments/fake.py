"""The fake gateway, for development and staging (epic-payments entries 1 and 2, user
decisions 2026-11-18 and 2026-11-19). Registered, but available only while
PAYMENTS_FAKE_GATEWAY is True (default off); the check payments.E001 refuses it on a public
site. Its "hosted page" is a portal page (payments.views.fake_gateway) with Pay and Fail
buttons.

It implements every operation of the adapter interface (payments.gateways). The fake
"gateway's" own records (payments, refunds, saved cards) are in the cache, which every
process of the site shares (the web workers, the task worker and `manage.py fake_webhook`),
for a day. Its Pay sends a signed webhook through the same handler a real gateway's goes
through (`payments.webhooks.handle`), signed as `t=<unix seconds>,v1=<HMAC-SHA256 of
"<t>.<body>">` in the `X-Fake-Signature` header with `PAYMENTS_FAKE_WEBHOOK_SECRET` (when
unset, a key derived from SECRET_KEY). The body is JSON: event_id, payment_id, amount,
currency (what the event claims; the amount that pays is the one re-read from the store).
"""
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse

from . import gateways
from .checks import public_site

SIGNATURE_HEADER = 'X-Fake-Signature'
META_HEADER = 'HTTP_X_FAKE_SIGNATURE'
STORE_SECONDS = 24 * 3600


def enabled():
    return bool(getattr(settings, 'PAYMENTS_FAKE_GATEWAY', False)) and not public_site()


def secret():
    configured = getattr(settings, 'PAYMENTS_FAKE_WEBHOOK_SECRET', '')
    if configured:
        return configured
    return hashlib.sha256(f'fake-webhook:{settings.SECRET_KEY}'.encode()).hexdigest()


def _digest(body, timestamp, key=None):
    key = secret() if key is None else key
    return hmac.new(key.encode(), f'{timestamp}.'.encode() + body, hashlib.sha256).hexdigest()


def sign(body, timestamp, key=None):
    return f't={timestamp},v1={_digest(body, timestamp, key)}'


# --- The fake gateway's own records -------------------------------------------------------------

def _store_key(gateway_payment_id):
    return f'payments:fake:{gateway_payment_id}'


def _put(gateway_payment_id, **record):
    cache.set(_store_key(gateway_payment_id), record, timeout=STORE_SECONDS)


def _get(gateway_payment_id):
    record = cache.get(_store_key(gateway_payment_id))
    if record is None:
        raise gateways.GatewayError('the fake gateway has no such payment')
    return record


def set_state(gateway_payment_id, status, amount=None, currency=None):
    """Change what the fake gateway holds for a payment: `status` pending, paid, failed
    or cancelled; optionally the amount and currency it reports."""
    record = dict(_get(gateway_payment_id))
    record['status'] = status
    if amount is not None:
        record['amount'] = str(amount)
    if currency is not None:
        record['currency'] = currency
    _put(gateway_payment_id, **record)


# --- The adapter's operations -------------------------------------------------------------------

def start(payment, request):
    from . import service
    gateway_payment_id = f'fake_{payment.pk.hex}'
    _put(gateway_payment_id, status=gateways.PENDING, amount=str(payment.amount),
         currency=payment.currency)
    service.set_gateway_payment_id(payment, gateway_payment_id)
    return reverse('checkout_fake', args=[payment.order_id])


def verify_webhook(request):
    header = request.META.get(META_HEADER, '')
    parts = dict(part.split('=', 1) for part in header.split(',') if '=' in part)
    timestamp, given = parts.get('t', ''), parts.get('v1', '')
    if not timestamp.isdigit() or not given:
        raise gateways.BadSignature('no signature')
    body = request.body
    if not hmac.compare_digest(_digest(body, timestamp).encode(),
                               given.encode('utf-8', 'replace')):
        raise gateways.BadSignature('wrong signature')
    try:
        data = json.loads(body)
        event = gateways.WebhookEvent(
            event_id=str(data['event_id']), payment_id=str(data['payment_id']),
            amount=Decimal(str(data['amount'])), currency=str(data['currency']))
    except (ValueError, KeyError, TypeError, InvalidOperation) as error:
        raise gateways.Malformed('not a fake webhook') from error
    sent_at = datetime.fromtimestamp(int(timestamp), dt_timezone.utc)
    from django.utils import timezone
    gateways.check_timestamp(gateways.adapter(gateways.FAKE), sent_at, timezone.now(), event)
    return event


def fetch_payment(payment):
    record = _get(payment.gateway_payment_id)
    status = {'paid': gateways.PAID, 'failed': gateways.FAILED}.get(
        record['status'], gateways.PENDING)
    return gateways.GatewayPayment(payment.gateway_payment_id, status,
                                   Decimal(record['amount']), record['currency'])


def cancel_attempt(payment):
    if payment.gateway_payment_id:
        if _get(payment.gateway_payment_id)['status'] == 'paid':
            raise gateways.GatewayError('the payment is paid: it cannot be cancelled')
        set_state(payment.gateway_payment_id, 'cancelled')


def refund(payment, amount):
    refund_id = f'fake_refund_{uuid.uuid4().hex}'
    _put(refund_id, payment=payment.gateway_payment_id, amount=str(amount))
    return refund_id


def save_card(user):
    token = f'fake_card_{uuid.uuid4().hex}'
    _put(token, user=str(user.pk))
    return token


def charge_saved_card(user, token, amount, currency):
    record = _get(token)
    if record.get('user') != str(user.pk):
        raise gateways.GatewayError('the card is not this user\'s')
    gateway_payment_id = f'fake_{uuid.uuid4().hex}'
    _put(gateway_payment_id, status='paid', amount=str(amount), currency=currency)
    return gateways.GatewayPayment(gateway_payment_id, gateways.PAID, Decimal(amount),
                                   currency)


# --- Sending webhooks ---------------------------------------------------------------------------

def webhook_body(payment, *, amount=None, event_id=None):
    return json.dumps({
        'event_id': event_id or f'evt_{uuid.uuid4().hex}',
        'payment_id': payment.gateway_payment_id,
        'amount': str(payment.amount if amount is None else amount),
        'currency': payment.currency,
    }).encode()


def send_webhook(body, *, timestamp=None, key=None, ip='127.0.0.1'):
    """Post `body` signed to the webhook handler as a gateway would (through the same
    handler, not over the network); returns the response."""
    from django.test import RequestFactory

    from . import webhooks
    timestamp = int(time.time()) if timestamp is None else timestamp
    request = RequestFactory().post(
        reverse('payments_webhook', args=[gateways.FAKE]), data=body,
        content_type='application/json', REMOTE_ADDR=ip,
        **{META_HEADER: sign(body, timestamp, key)})
    return webhooks.webhook(request, gateways.FAKE)


def pay(payment):
    """The doctor pressed Pay: the fake gateway marks the payment paid and sends its signed
    webhook."""
    set_state(payment.gateway_payment_id, 'paid')
    return send_webhook(webhook_body(payment))


def fail(payment):
    """The doctor pressed Fail."""
    set_state(payment.gateway_payment_id, 'failed')


def register():
    gateways.register(
        gateways.FAKE, start, enabled=enabled, verify_webhook=verify_webhook,
        fetch_payment=fetch_payment, cancel_attempt=cancel_attempt, refund=refund,
        save_card=save_card, charge_saved_card=charge_saved_card)
