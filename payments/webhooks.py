"""Webhooks from the payment gateways (epic-payments entry 2, AD-8, AD-23; user decisions
2026-11-19).

`POST /payments/webhook/<gateway>/` (public; outside the language prefixes and `/api/`):
the adapter checks the signature and the timestamp (`Adapter.verify_webhook`), the payment
is looked up by the gateway's own id, the event is taken in once (a database unique
constraint on (gateway, event id), not the cache), a `WebhookLog` row is written and a task
(`payments.tasks.confirm_webhook`) is queued; the answer is 200 at once. The task re-reads
the payment from the gateway and confirms it (`payments.service.confirm_payment`: the one
way an order is paid), with its attempts and next try on the log row. A verified paid event
that carries a saved card (entry 11, `WebhookEvent.card`) has it stored first
(`payments.repeat.store_card`, its own transaction); a paid event of an order that asked to
save the card and carries none is a Sentry warning.

Every webhook writes one log row, accepted or refused: the result, the IP, the gateway's
ids, the amount it claimed, the SHA-256 and size of the body, never the body. A refusal is
a `logger.warning` with the order's UUID (when the payment is known) and nothing else: no
signature, secret, body or personal data (AD-23). Bodies are limited to 64 KB (413); an IP
with too many failed signatures in a window gets 429 (the counter is in the cache, keyed by
a hash of the IP). There is no IP allowlist.
"""
import hashlib
import hmac
import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from eventlog.service import client_ip
from mytp_core.tasks import enqueue_on_commit

from . import gateways, service
from .models import Payment, WebhookLog

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024
# Failed signatures per IP before a 429, and the window they are counted in.
FAILED_SIGNATURE_LIMIT = 20
FAILED_SIGNATURE_WINDOW = 600
# The re-read's tries and the wait before each retry (the last one is final: Sentry).
RETRY_BACKOFF = (timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10),
                 timedelta(minutes=30), timedelta(hours=2))
MAX_ATTEMPTS = len(RETRY_BACKOFF) + 1
LOG_RETENTION_DAYS = 180

_REFUSAL_STATUS = {
    WebhookLog.BAD_SIGNATURE: 401, WebhookLog.STALE: 400, WebhookLog.UNKNOWN: 404,
    WebhookLog.TOO_LARGE: 413, WebhookLog.RATE_LIMITED: 429,
}


def _failures_key(ip):
    digest = hmac.new(settings.SECRET_KEY.encode(), ip.encode(), hashlib.sha256).hexdigest()
    return f'payments:badsig:{digest[:32]}'


def _failure_count(ip):
    return cache.get(_failures_key(ip)) or 0


def _count_failure(ip):
    key = _failures_key(ip)
    if cache.add(key, 1, timeout=FAILED_SIGNATURE_WINDOW):
        return
    try:
        cache.incr(key)
    except ValueError:  # expired between the two calls
        cache.add(key, 1, timeout=FAILED_SIGNATURE_WINDOW)


def _log(gateway, result, ip, body, *, event=None, payment=None, event_id=None):
    event_id = event_id if event_id is not None else (event.event_id if event else '')
    return WebhookLog.objects.create(
        gateway=gateway, result=result, ip=ip[:64], body_sha256=hashlib.sha256(body).hexdigest(),
        body_size=len(body), event_id=(event_id or '')[:100],
        gateway_payment_id=(event.payment_id if event else '')[:100],
        amount=event.amount if event else None, currency=(event.currency if event else '')[:3],
        status=(event.status or '') if event else '', payment=payment)


def _refuse(gateway, result, ip, body, *, event=None, payment=None):
    """Log a refusal (a warning with the order's UUID only) and answer it."""
    _log(gateway, result, ip, body, event=event, payment=payment)
    logger.warning('payment webhook refused: %s (gateway %s, order %s)', result, gateway,
                   payment.order_id if payment is not None else '-')
    return HttpResponse(status=_REFUSAL_STATUS[result], content_type='text/plain')


def handle(adapter, request):
    """The shared handling of one webhook for `adapter`; returns the response."""
    gateway, ip = adapter.name, client_ip(request)
    declared = request.META.get('CONTENT_LENGTH') or '0'
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return _refuse(gateway, WebhookLog.TOO_LARGE, ip, b'')
    body = request.body
    if len(body) > MAX_BODY_BYTES:
        return _refuse(gateway, WebhookLog.TOO_LARGE, ip, b'')
    if _failure_count(ip) >= FAILED_SIGNATURE_LIMIT:
        return _refuse(gateway, WebhookLog.RATE_LIMITED, ip, body)
    try:
        event = adapter.call('verify_webhook', request)
    except gateways.BadSignature:
        _count_failure(ip)
        return _refuse(gateway, WebhookLog.BAD_SIGNATURE, ip, body)
    except gateways.Stale as stale:
        return _refuse(gateway, WebhookLog.STALE, ip, body, event=stale.event)
    except gateways.Malformed:
        return _refuse(gateway, WebhookLog.UNKNOWN, ip, body)
    payment = Payment.objects.filter(gateway=gateway,
                                     gateway_payment_id=event.payment_id).first() \
        if event.payment_id else None
    if payment is None or not event.event_id:
        return _refuse(gateway, WebhookLog.UNKNOWN, ip, body, event=event)
    if event.status == gateways.PAID:
        _store_card(payment, event.card)
    try:
        with transaction.atomic():
            row = _log(gateway, WebhookLog.ACCEPTED, ip, body, event=event, payment=payment)
            enqueue_on_commit(_task(), row.pk)
    except IntegrityError:
        _log(gateway, WebhookLog.DUPLICATE, ip, body, event=event, payment=payment)
        logger.info('payment webhook duplicate: gateway %s, order %s', gateway,
                    payment.order_id)
    return HttpResponse(status=200, content_type='text/plain')


def _store_card(payment, card):
    """Keep the card a verified paid event carries (entry 11; `payments.repeat.store_card`,
    its own transaction). A failure is logged by order UUID and never refuses the webhook."""
    from . import repeat
    try:
        repeat.store_card(payment, card)
    except Exception as failure:  # noqa: BLE001 - the payment is handled all the same
        logger.error('payment webhook: the saved card was not stored (%s): order %s',
                     type(failure).__name__, payment.order_id)


def _task():
    from .tasks import confirm_webhook
    return confirm_webhook


@csrf_exempt
@never_cache
@require_POST
def webhook(request, gateway):
    adapter = gateways.adapter(gateway)
    if adapter is None or not adapter.supports('verify_webhook'):
        raise Http404('no such gateway')
    return handle(adapter, request)


# --- The task's work ---------------------------------------------------------------------------

_RESULT_OF = {service.AMOUNT_MISMATCH: WebhookLog.AMOUNT_MISMATCH,
              service.NOT_PAYABLE: WebhookLog.NOT_PAYABLE}


REFUND_NOTICE = 'refund_notice'


def reread_and_confirm(payment):
    """Re-read `payment` from its gateway and confirm it (`service.confirm_payment`);
    returns the outcome. Raises when the gateway cannot be read."""
    adapter = gateways.adapter(payment.gateway)
    if adapter is None:
        raise gateways.GatewayError('the gateway is not available')
    return service.confirm_payment(payment, adapter.call('fetch_payment', payment))


def process(log_id):
    """The task's work for one accepted log row: re-read, confirm, record. Idempotent (a row
    already processed, or one not accepted, is left). The re-read (an HTTP call) and the
    confirmation take no lock of this row. A failed re-read, or one that still says pending,
    is retried with backoff (`attempts`, `next_try_at` on the row); after the last try an
    error goes to Sentry by order UUID. Returns the outcome, 'retry', 'failed' or None."""
    row = WebhookLog.objects.filter(pk=log_id).select_related('payment').first()
    if (row is None or row.processed_at is not None or row.payment is None
            or row.result not in WebhookLog.DEDUPED):
        return None
    if row.gateway == 'redsys' and not row.status:
        # A notification that says nothing of the payment is a refund's (type 3; epic-payments
        # entry 7): logged, marked done, never re-read or retried.
        return _record(row.pk, REFUND_NOTICE)
    try:
        outcome = reread_and_confirm(row.payment)
    except Exception as failure:  # noqa: BLE001 - any gateway or hook fault is retried
        logger.warning('payment webhook %s: not confirmed yet (%s)', row.pk,
                       type(failure).__name__)
        outcome = None
    return _record(row.pk, outcome)


def _record(log_id, outcome):
    """Record the result on the row, in a short transaction; skipped when the row was
    processed meanwhile."""
    now = timezone.now()
    with transaction.atomic():
        row = WebhookLog.objects.select_for_update().filter(pk=log_id).first()
        if row is None or row.processed_at is not None:
            return None
        if outcome is not None and outcome != service.STILL_PENDING:
            row.processed_at, row.next_try_at = now, None
            row.result = _RESULT_OF.get(outcome, row.result)
            row.save(update_fields=['processed_at', 'next_try_at', 'result'])
            return outcome
        row.attempts += 1
        if row.attempts >= MAX_ATTEMPTS:
            row.next_try_at = None
            row.save(update_fields=['attempts', 'next_try_at'])
            order_id = row.payment.order_id if row.payment_id else '-'
            service.report_attention(order_id, 'could not be confirmed')
            return 'failed'
        row.next_try_at = now + RETRY_BACKOFF[row.attempts - 1]
        row.save(update_fields=['attempts', 'next_try_at'])
        return 'retry'


def purge_logs(now=None):
    """Delete the log rows older than LOG_RETENTION_DAYS; returns how many."""
    cutoff = (now or timezone.now()) - timedelta(days=LOG_RETENTION_DAYS)
    return WebhookLog.objects.filter(received_at__lt=cutoff).delete()[0]
