"""The Revolut adapter (epic-payments entry 4, user decisions 2026-11-22): card payments of
the Irish company (the EU region's `revolut`) on Revolut's hosted checkout page, over the
Revolut Merchant API (sandbox first).

- `start`: a Revolut order per attempt (`POST /api/orders`: the amount in cents, EUR, our
  order reference, a neutral description, `redirect_url` = our return page built from
  `settings.SITE_URL`, `expire_pending_after` 30 minutes; the attempt's session is 35); the
  attempt's `gateway_payment_id` is the Revolut order id and the doctor is redirected to Revolut's
  `checkout_url`. Only the reference and "MyTreatPlan order <reference>" leave the portal: no
  doctor name, email or other personal data. Any Revolut fault is a `GatewayError`.
- `verify_webhook`: `Revolut-Signature` holds one or more comma-separated `v1=<hex>` entries,
  each HMAC-SHA256 (a signing secret of `REVOLUT_WEBHOOK_SECRET`, comma-separated while it
  is rotated) over `v1.{Revolut-Request-Timestamp}.{raw body}`; the timestamp is Unix
  milliseconds, within 5 minutes. The payload is `{event, order_id, merchant_order_ext_ref}`
  only: the event id is `order_id:event`, no status, so the task always re-reads.
- `fetch_payment`: a real `GET /api/orders/{id}`: `pending`, `processing`, `authorised` are
  PENDING, `completed` PAID, `failed` and `cancelled` FAILED. The order is paid only through
  `service.confirm_payment`, which compares the amount and currency.
- `cancel_attempt`: `POST /api/orders/{id}/cancel` of a pending order; a refusal is settled
  by re-reading the order (already failed or cancelled: closed; anything else: still open,
  the existing re-check decides).
- `refund`: `POST /api/orders/{id}/refund` with an `Idempotency-Key`; 201 (a refund order)
  counts as refunded, as with Redsys. A 4xx or a `decline_reason` is a `GatewayError`; a
  timeout, a 5xx or an unreadable answer a `GatewayUnknown`.

No gateway call is made while a transaction or row lock is held (every operation is called
outside one). Every call sends `Authorization: Bearer <key>` and the pinned
`Revolut-Api-Version`. No saved cards, SEPA, Revolut Pay widget or outgoing request signing.

Settings (settings.env): REVOLUT_SECRET_KEY, REVOLUT_WEBHOOK_SECRET and REVOLUT_ENVIRONMENT
(`sandbox`, the default, or `production`). The adapter is available when the key is set.
Nothing of the keys, the signatures, card data or personal data is ever logged (AD-23).
"""
import hashlib
import hmac
import json
import logging
import urllib.error
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from urllib.parse import urlsplit

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from . import gateways
from .models import Payment

logger = logging.getLogger(__name__)

NAME = 'revolut'
SANDBOX, PRODUCTION = 'sandbox', 'production'
HOSTS = {SANDBOX: 'https://sandbox-merchant.revolut.com',
         PRODUCTION: 'https://merchant.revolut.com'}
API_VERSION = '2026-09-22'
SIGNATURE_PREFIX = 'v1'
WEBHOOK_PATH_EVENTS = ('ORDER_COMPLETED', 'ORDER_FAILED', 'ORDER_CANCELLED')
CURRENCY = 'EUR'
# What Revolut keeps the order payable (`expire_pending_after`), and the attempt's session on
# our side: five minutes longer, as our clock starts before Revolut's order exists.
EXPIRE_PENDING_AFTER = timedelta(minutes=30)
SESSION_LIFETIME = timedelta(minutes=35)
TIMESTAMP_TOLERANCE = timedelta(minutes=5)
REQUEST_TIMEOUT = 15
REFUND_TIMEOUT = 30
CHECKOUT_HOST_SUFFIX = '.revolut.com'

_STATUS = {'pending': gateways.PENDING, 'processing': gateways.PENDING,
           'authorised': gateways.PENDING, 'completed': gateways.PAID,
           'failed': gateways.FAILED, 'cancelled': gateways.FAILED}


class Refused(gateways.GatewayError):
    """Revolut answered with a 4xx: the call was refused (nothing happened)."""


# --- Settings ----------------------------------------------------------------------------------

def secret_key():
    return str(getattr(settings, 'REVOLUT_SECRET_KEY', '') or '').strip()


def webhook_secrets():
    """The signing secrets (several while one is rotated)."""
    raw = str(getattr(settings, 'REVOLUT_WEBHOOK_SECRET', '') or '')
    return [part.strip() for part in raw.split(',') if part.strip()]


def environment():
    value = str(getattr(settings, 'REVOLUT_ENVIRONMENT', SANDBOX) or SANDBOX).strip().lower()
    return PRODUCTION if value == PRODUCTION else SANDBOX


def enabled():
    return bool(secret_key())


def base_url():
    return HOSTS[environment()]


def accepts(order):
    """Euros only."""
    return order.currency == CURRENCY


# --- Amounts -----------------------------------------------------------------------------------

def to_cents(amount):
    """`amount` (a Decimal of at most 2 places) in integer cents."""
    cents = Decimal(amount) * 100
    if cents != cents.to_integral_value():
        raise ValueError('more than two decimal places')
    return int(cents)


def from_cents(cents):
    if isinstance(cents, bool) or not isinstance(cents, int) or cents < 0:
        raise ValueError('not an amount in cents')
    return (Decimal(cents) / 100).quantize(Decimal('0.01'))


# --- The Merchant API --------------------------------------------------------------------------

def request(method, path, payload=None, *, headers=None, timeout=REQUEST_TIMEOUT,
            ok=(200, 201)):
    """One Merchant API call; the parsed JSON (`None` for an empty body). A 4xx raises
    `Refused`, any other fault (network, timeout, 5xx, an unreadable answer)
    `GatewayUnknown`: the call may or may not have happened. Nothing of the answer, the key
    or the URL is put in the message."""
    from accounts import http
    key = secret_key()
    if not key:
        raise gateways.GatewayError('REVOLUT_SECRET_KEY is not set')
    try:
        text = http.request_json(
            method, base_url() + path, payload, timeout=timeout, ok=ok, headers={
                'Authorization': f'Bearer {key}', 'Revolut-Api-Version': API_VERSION,
                **(headers or {})})
        return json.loads(text) if text.strip() else None
    except urllib.error.HTTPError as failure:
        if 400 <= failure.code < 500:
            raise Refused(f'Revolut refused the call ({failure.code})') from None
        raise gateways.GatewayUnknown(f'Revolut answered {failure.code}') from None
    except http.FETCH_ERRORS as failure:
        raise gateways.GatewayUnknown(
            f'Revolut could not be reached ({type(failure).__name__})') from None


def _order_path(payment, suffix=''):
    order_id = payment.gateway_payment_id
    if not order_id or not order_id.replace('-', '').isalnum():
        raise gateways.GatewayError('the payment has no Revolut order id')
    return f'/api/orders/{order_id}{suffix}'


def _site_url(path):
    return settings.SITE_URL.rstrip('/') + path


def _checkout_url(order):
    """The order's hosted page; only an https page of Revolut's is trusted."""
    url = order.get('checkout_url') if isinstance(order, dict) else None
    parts = urlsplit(url) if isinstance(url, str) else None
    if (parts is None or parts.scheme != 'https' or not parts.hostname
            or not parts.hostname.endswith(CHECKOUT_HOST_SUFFIX)):
        raise gateways.GatewayError('Revolut gave no checkout page')
    return url


def start(payment, request_):
    """Open a Revolut order for the attempt (once: a second call returns the same page) and
    return its hosted page."""
    from . import service
    if payment.currency != CURRENCY:
        raise gateways.GatewayError(f'Revolut is not set up for {payment.currency[:3]}')
    if payment.gateway_payment_id:
        order = request('GET', _order_path(payment))
        state = order.get('state') if isinstance(order, dict) else None
        if state in ('processing', 'authorised'):
            # Being paid right now: the return page's re-read decides.
            return _site_url(reverse('checkout_return', args=[payment.order_id]))
        if state != 'pending':
            raise gateways.GatewayError('the Revolut order can no longer be paid')
        url = _checkout_url(order)
        service.extend_session(payment, SESSION_LIFETIME)
        return url
    reference = payment.order.reference
    order = request('POST', '/api/orders', {
        'amount': to_cents(payment.amount),
        'currency': CURRENCY,
        'description': f'MyTreatPlan order {reference}',
        'merchant_order_data': {'reference': reference},
        'redirect_url': _site_url(reverse('checkout_return', args=[payment.order_id])),
        'expire_pending_after': f'PT{int(EXPIRE_PENDING_AFTER.total_seconds() // 60)}M',
    })
    order_id = order.get('id') if isinstance(order, dict) else None
    if not isinstance(order_id, str) or not order_id:
        raise gateways.GatewayError('Revolut gave no order id')
    url = _checkout_url(order)
    service.set_gateway_payment_id(payment, order_id[:100])
    if Payment.objects.filter(pk=payment.pk).values_list('state', flat=True).first() \
            != Payment.OPEN:
        # A second Pay closed this attempt while the order was being created: the new Revolut
        # order must not stay payable.
        try:
            request('POST', _order_path(payment, '/cancel'), {})
        except gateways.GatewayError:
            pass
        raise gateways.GatewayError('the attempt was closed while it started')
    service.extend_session(payment, SESSION_LIFETIME)
    return url


# --- Webhooks ----------------------------------------------------------------------------------

def _signatures(header):
    """The `v1=<hex>` entries of the header, as bytes."""
    found = []
    for part in header.split(','):
        name, _, value = part.strip().partition('=')
        if name == SIGNATURE_PREFIX and value:
            try:
                found.append(value.encode('ascii'))
            except UnicodeEncodeError:
                continue
    return found


def sign(secret, timestamp, body):
    """The hex signature of `body` (bytes) for `timestamp` (the header's text)."""
    payload = f'{SIGNATURE_PREFIX}.{timestamp}.'.encode('utf-8') + body
    return hmac.new(secret.encode('utf-8'), payload, hashlib.sha256).hexdigest()


def verify_webhook(request_):
    header = request_.META.get('HTTP_REVOLUT_SIGNATURE', '')
    timestamp = request_.META.get('HTTP_REVOLUT_REQUEST_TIMESTAMP', '').strip()
    secrets = webhook_secrets()
    given = _signatures(header)
    if not secrets or not given or not timestamp.isascii() or not timestamp.isdigit():
        raise gateways.BadSignature('no signature')
    body = request_.body
    matched = False
    for secret in secrets:
        expected = sign(secret, timestamp, body).encode('ascii')
        for candidate in given:
            matched = hmac.compare_digest(expected, candidate.lower()) or matched
    if not matched:
        raise gateways.BadSignature('wrong signature')
    try:
        sent_at = datetime.fromtimestamp(int(timestamp) / 1000, dt_timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise gateways.BadSignature('malformed timestamp') from error
    try:
        data = json.loads(body.decode('utf-8'))
        order_id, name = data['order_id'], data['event']
        if (not isinstance(data, dict) or not isinstance(order_id, str) or not order_id
                or not isinstance(name, str) or not name):
            raise ValueError('not a Revolut webhook')
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as error:
        raise gateways.Malformed('not a Revolut webhook') from error
    event = gateways.WebhookEvent(event_id=f'{order_id}:{name}', payment_id=order_id)
    gateways.check_timestamp(gateways.adapter(NAME), sent_at, timezone.now(), event)
    return event


# --- Reading, cancelling, refunding ------------------------------------------------------------

def _read(payment):
    order = request('GET', _order_path(payment))
    if not isinstance(order, dict):
        raise gateways.GatewayError('Revolut answered something unexpected')
    return order


def fetch_payment(payment):
    """The Revolut order as Revolut says it is now."""
    order = _read(payment)
    if order.get('id') != payment.gateway_payment_id or order.get('type', 'payment') != 'payment':
        raise gateways.GatewayError('the Revolut order is not this payment')
    try:
        amount = from_cents(order.get('amount'))
    except ValueError:
        raise gateways.GatewayError('Revolut gave no amount') from None
    currency = str(order.get('currency') or '')[:3]
    # An unknown state never pays the order.
    status = _STATUS.get(str(order.get('state', '')).lower(), gateways.PENDING)
    return gateways.GatewayPayment(payment.gateway_payment_id, status, amount, currency)


def cancel_attempt(payment):
    """Close the pending Revolut order so it can no longer be paid. When Revolut refuses
    (the order is no longer pending), the order is re-read: already failed or cancelled counts
    as closed; any other state raises, and the attempt stays open for the existing re-check."""
    if not payment.gateway_payment_id:
        return
    try:
        request('POST', _order_path(payment, '/cancel'), {})
    except Refused:
        order = _read(payment)
        if str(order.get('state', '')).lower() not in ('failed', 'cancelled'):
            raise


def refund(payment, amount):
    """Refund `amount` (a Decimal) of the confirmed `payment`; the refund order's id. Revolut
    accepting it (201) counts as refunded."""
    from .models import Refund
    cents = to_cents(amount)
    # The refund row's own key: a retry after a refusal is a new request for Revolut.
    row = payment.refunds.filter(state=Refund.PENDING).order_by('-created_at').first()
    key = row.key if row is not None else uuid.uuid4()
    answer = request('POST', _order_path(payment, '/refund'), {
        'amount': cents,
        'currency': payment.currency,
        'merchant_order_data': {'reference': f'{payment.order.reference}-refund'},
    }, headers={'Idempotency-Key': str(key)},
        timeout=REFUND_TIMEOUT)
    if not isinstance(answer, dict):
        raise gateways.GatewayUnknown('Revolut answered something unexpected')
    # A declined or failed refund can come back as 200 with the decline inside `payments[]`.
    entries = [entry for entry in (answer.get('payments') or []) if isinstance(entry, dict)]
    reason = answer.get('decline_reason') or next(
        (entry.get('decline_reason') or entry.get('state') for entry in entries
         if entry.get('decline_reason') or entry.get('state') in ('declined', 'failed')), None)
    if reason:
        raise gateways.GatewayError(f'Revolut refused the refund ({str(reason)[:40]})')
    refund_id = answer.get('id')
    if not isinstance(refund_id, str) or not refund_id:
        raise gateways.GatewayUnknown('Revolut gave no refund id')
    return refund_id


def register():
    gateways.register(
        NAME, start, enabled=enabled, verify_webhook=verify_webhook,
        fetch_payment=fetch_payment, cancel_attempt=cancel_attempt, refund=refund,
        session_lifetime=SESSION_LIFETIME, timestamp_tolerance=TIMESTAMP_TOLERANCE,
        accepts=accepts, required_settings=('REVOLUT_SECRET_KEY', 'REVOLUT_WEBHOOK_SECRET'))
