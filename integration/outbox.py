"""The outbox: events from this portal to the admin site (epic-admin-interface entry 3,
AD-3 item 2, AD-18, AD-19, interface I4).

Writing: `record(type, aggregate_id, aggregate_version, payload)` runs inside the
caller's `transaction.atomic()` (it refuses otherwise). It validates the payload against
the type's registered closed model and the central payload checks
(`mytp_core.contracts.build_payload`), builds the envelope (so a row that could not be
sent is never written), takes the aggregate's next `sequence` from its counter row
(OutboxAggregate, locked by `record` only, so concurrent writers wait for each other and
never fail, and the dispatcher never blocks them), writes an OutboxEvent and, when the
transaction commits, logs it and enqueues the dispatcher's nudge task (one per
transaction). If the caller's transaction rolls back, nothing stays.

Sending: `dispatch()` (the `dispatchoutbox` job every minute and the nudge task; both
are the same idempotent sweep, no long-running loop) sends the pending rows one at a
time, each in its own transaction holding the row's lock
(`select_for_update(skip_locked=True)` on PostgreSQL, so two runs never send the same
row at once). Per aggregate only the lowest pending `sequence` is eligible: a failing
event holds back that aggregate's later events, never another aggregate's. One event
per signed POST to `<admin>/api/v1/events`; the outcome:

- 2xx `accepted`, `duplicate` or `stale`: delivered;
- 2xx `rejected`: rejected (final, never resent; the admin lists it);
- refused here before sending (the body over 256 KB, a stored event that no longer
  passes the contract): rejected (final), with its reason, a warning line and one
  Sentry warning after the commit (event id, type, reason; no payload);
- anything else: retried, for ever, after `min(2^(attempts-1), 60)` minutes (decision
  2026-11-05). After 24 hours of failing, one Sentry warning per row (event id, type and
  error code; no payload), sent after the commit that marks it warned.

A run stops after MAX_FAILURES_IN_A_ROW failures in a row that say the admin site is
unreachable or unavailable (no answer, 5xx, 401); a failure of one event (another 4xx, an
answer without a result) does not count, so one bad aggregate never holds up the others.

A run killed after the admin stored an event leaves the row pending; the next run sends
it again and the admin answers `duplicate`. The admin is `INTERFACE_ADMIN_SITE` (its
INTERFACE_PEERS entry), or the only INTERFACE_PEERS entry when that setting is empty.
"""
import logging
import time
import uuid
from datetime import timedelta
from functools import partial

import sentry_sdk
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from mytp_core import interface
from mytp_core.contracts import (DELIVERED_RESULTS, MAX_EVENT_BYTES, EventEnvelope,
                                 EventResult, build_payload)

from .models import OUTBOX_RETENTION_DAYS, OutboxAggregate, OutboxEvent
from .peers import parse_peers

logger = logging.getLogger('integration.outbox')

EVENTS_PATH = '/api/v1/events'
MAX_BACKOFF_MINUTES = 60
WARN_AFTER = timedelta(hours=24)
# A run stops after this many failures in a row that say the admin site is unreachable or
# unavailable (the other rows wait for the next run rather than each for its timeout).
MAX_FAILURES_IN_A_ROW = 3
# A run stops after this many seconds (the next one carries on).
RUN_SECONDS = 50

DELIVERED = 'delivered'
REJECTED = 'rejected'
RETRY = 'retry'
# The error codes of retried failures that say the admin site is unreachable or
# unavailable (they count towards MAX_FAILURES_IN_A_ROW).
NETWORK = 'network'
PEER_URL = 'peer_url'
# The reasons of a row rejected here, before sending (final; the admin site never sees
# it): each sends one Sentry warning.
LOCAL_REJECTIONS = ('body_too_large', 'invalid_envelope')


class OutboxError(Exception):
    """`record` was called outside a transaction, or the event cannot be built."""


# --- Writing ----------------------------------------------------------------------------

def _uuid(value, what):
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise OutboxError(f'{what} is not a UUID') from None


def envelope_of(event):
    """The JSON-ready envelope of an OutboxEvent (validated again: a row that no longer
    passes the contract raises a ValueError)."""
    return EventEnvelope.model_validate({
        'event_id': str(event.event_id), 'type': event.type, 'version': event.version,
        'aggregate_id': str(event.aggregate_id),
        'aggregate_version': event.aggregate_version, 'sequence': event.sequence,
        'occurred_at': event.occurred_at, 'region': event.region,
        'payload': event.payload,
    }).model_dump(mode='json')


def _next_sequence(aggregate_id):
    """The aggregate's next sequence, from its counter row, locked until the caller's
    transaction ends. Two first events of a new aggregate at once: the second create
    fails on the primary key once the first commits, and then waits for its lock."""
    while True:
        counter = (OutboxAggregate.objects.select_for_update()
                   .filter(aggregate_id=aggregate_id).first())
        if counter is not None:
            counter.last_sequence += 1
            counter.save(update_fields=['last_sequence'])
            return counter.last_sequence
        try:
            with transaction.atomic():
                OutboxAggregate.objects.create(aggregate_id=aggregate_id, last_sequence=0)
        except IntegrityError:
            pass  # created by another transaction meanwhile: lock that one


def _enqueue_nudge():
    from .tasks import dispatch_outbox_task
    dispatch_outbox_task.enqueue()


def _log_recorded(event_id, event_type, aggregate_id, aggregate_version, sequence):
    logger.info('outbox event recorded: %s %s (aggregate %s v%s #%s)', event_id,
                event_type, aggregate_id, aggregate_version, sequence)


def _nudge_once(connection):
    """Enqueue the nudge when the transaction commits, once per transaction (a callback
    of a rolled-back savepoint is dropped by Django, so a later record adds it again)."""
    if not any(entry[1] is _enqueue_nudge for entry in connection.run_on_commit):
        transaction.on_commit(_enqueue_nudge)


def record(event_type, aggregate_id, aggregate_version, payload, *, version=1,
           occurred_at=None, region=None):
    """Write one event to the outbox inside the caller's transaction; returns the
    OutboxEvent. Raises OutboxError outside `transaction.atomic()`, and
    UnknownEventType, InvalidPayload or ContractViolation (all ValueError, never quoting
    a value) for a type without a registered model or a payload it refuses: nothing is
    written, and the caller's transaction rolls back with the exception. `region`
    (default settings.REGION) is for the staging sample only."""
    connection = transaction.get_connection()
    if not connection.in_atomic_block:
        raise OutboxError('outbox.record must run inside transaction.atomic()')
    aggregate_id = _uuid(aggregate_id, 'aggregate_id')
    payload = build_payload(event_type, payload)
    event = OutboxEvent(type=event_type, version=version, aggregate_id=aggregate_id,
                        aggregate_version=aggregate_version, sequence=1,
                        occurred_at=occurred_at or timezone.now(),
                        region=region or settings.REGION, payload=payload)
    # Every envelope field is checked before the row exists (the sequence is a
    # placeholder here; the size is checked with it).
    body = interface.json_body(envelope_of(event))
    if len(body) > MAX_EVENT_BYTES - 32:
        raise OutboxError(f'the event is larger than {MAX_EVENT_BYTES} bytes')
    event.sequence = _next_sequence(aggregate_id)
    event.save(force_insert=True)
    transaction.on_commit(partial(_log_recorded, event.event_id, event.type,
                                  event.aggregate_id, event.aggregate_version,
                                  event.sequence))
    _nudge_once(connection)
    return event


# --- Sending ----------------------------------------------------------------------------

def admin_site():
    """The admin site's name and events URL (`admin_site_url`)."""
    site, base_url = admin_site_url()
    return site, base_url + EVENTS_PATH


def admin_site_url():
    """The admin site's name and base URL (INTERFACE_ADMIN_SITE, or the only
    INTERFACE_PEERS entry); InterfaceConfigurationError when it cannot be told."""
    site = (getattr(settings, 'INTERFACE_ADMIN_SITE', '') or '').strip()
    peers = parse_peers(getattr(settings, 'INTERFACE_PEERS', '') or '')
    if not site:
        if len(peers) != 1:
            raise interface.InterfaceConfigurationError(
                'Set INTERFACE_ADMIN_SITE: INTERFACE_PEERS does not list exactly one site.')
        site = next(iter(peers))
    if site not in peers:
        raise interface.InterfaceConfigurationError(
            f'INTERFACE_ADMIN_SITE {site!r} is not in INTERFACE_PEERS.')
    return site, peers[site]


def backoff(attempts):
    """The wait after the `attempts`-th failure: min(2^(attempts-1), 60) minutes."""
    return timedelta(minutes=min(2 ** (max(attempts, 1) - 1), MAX_BACKOFF_MINUTES))


def _eligible(now):
    earlier = OutboxEvent.objects.filter(aggregate_id=OuterRef('aggregate_id'),
                                         state=OutboxEvent.PENDING,
                                         sequence__lt=OuterRef('sequence'))
    return (OutboxEvent.objects.filter(state=OutboxEvent.PENDING, next_attempt_at__lte=now)
            .filter(~Exists(earlier)).order_by('next_attempt_at', 'id'))


def _take(now):
    """The next eligible row, locked for this transaction (None when there is none)."""
    return _eligible(now).select_for_update(skip_locked=True).first()


def _error_code(exc):
    """The code of a call failure: `network` only for no answer at all, `peer_url` for a
    peer URL refused before the call, `http_<status>[_<peer code>]` for an HTTP error,
    `bad_answer` for a 2xx answer that is not a JSON object."""
    if exc.status is None:
        return NETWORK if str(exc).startswith('no answer') else PEER_URL
    if 200 <= exc.status < 300:
        return 'bad_answer'
    return f'http_{exc.status}' + (f'_{exc.code}' if exc.code else '')


def _unavailable(exc):
    """Whether a call failure says the admin site is unreachable or unavailable (rather
    than refusing this one event)."""
    return exc.status is None or exc.status >= 500 or exc.status == 401


def _send(event, audience, url):
    """(outcome, code, short text, whether it says the admin site is unavailable)."""
    try:
        envelope = envelope_of(event)
    except ValueError:
        # The stored row no longer passes the contract (a code change): final.
        return (REJECTED, 'invalid_envelope',
                'the stored event no longer passes the contract', False)
    try:
        answer = interface.signed_post(url, envelope, audience=audience,
                                       max_body_bytes=MAX_EVENT_BYTES)
    except interface.BodyTooLarge as exc:
        return REJECTED, 'body_too_large', str(exc), False
    except interface.PeerCallFailed as exc:
        return RETRY, _error_code(exc), str(exc), _unavailable(exc)
    try:
        result = EventResult.model_validate(answer.data)
    except ValueError:
        return RETRY, 'bad_answer', f'HTTP {answer.status} without a result', False
    if result.result in DELIVERED_RESULTS:
        return DELIVERED, result.result, '', False
    return REJECTED, 'rejected', result.reason, False


def _send_warning(event_id, event_type, code,
                  message='Outbox event failing for 24 hours'):
    message = f'{message}: {event_type} ({code})'
    logger.warning('%s, event %s', message, event_id)
    with sentry_sdk.new_scope() as scope:
        scope.set_tag('outbox.event_id', str(event_id))
        scope.set_tag('outbox.type', event_type)
        scope.set_tag('outbox.error', code)
        sentry_sdk.capture_message(message, level='warning')


def _warn_once(event, code, now):
    """Mark the row warned (saved with it) and send the one Sentry warning only after
    that commits: at most one per row."""
    if event.warned_at or not event.failing_since or now - event.failing_since < WARN_AFTER:
        return
    event.warned_at = now
    transaction.on_commit(partial(_send_warning, event.event_id, event.type, code))


def _after_send(event, outcome, code, text, now):
    if outcome == DELIVERED:
        event.state = OutboxEvent.DELIVERED
        event.delivered_at = now
        event.failing_since = None
        logger.info('outbox event delivered: %s %s (%s)', event.event_id, event.type, code)
    elif outcome == REJECTED:
        event.state = OutboxEvent.REJECTED
        event.last_error = f'{code}: {text}'[:200]
        event.failing_since = None
        logger.warning('outbox event rejected: %s %s (%s: %s)', event.event_id,
                       event.type, code, text)
        if code in LOCAL_REJECTIONS:
            # Refused here: the admin site never sees it, so it is reported once, after
            # the commit that marks the row rejected (event id, type, reason only).
            transaction.on_commit(partial(_send_warning, event.event_id, event.type, code,
                                          'Outbox event rejected before sending'))
    else:
        event.attempts += 1
        event.last_error = f'{code}: {text}'[:200]
        event.next_attempt_at = now + backoff(event.attempts)
        event.failing_since = event.failing_since or now
        logger.warning('outbox event not delivered: %s %s, attempt %s (%s)',
                       event.event_id, event.type, event.attempts, code)
        _warn_once(event, code, now)
    event.save(update_fields=['state', 'delivered_at', 'attempts', 'last_error',
                              'next_attempt_at', 'failing_since', 'warned_at'])


def pending_due(now=None):
    return OutboxEvent.objects.filter(state=OutboxEvent.PENDING,
                                      next_attempt_at__lte=now or timezone.now()).exists()


def dispatch(*, max_seconds=RUN_SECONDS, clock=time.monotonic):
    """Send the due pending events; returns {'delivered', 'rejected', 'retry'} counts.
    Nothing to send: no configuration is needed. Pending events but no admin site or
    signing key: InterfaceConfigurationError / SigningKeyUnavailable (the job fails, so
    its Sentry monitor shows it); the rows stay as they are."""
    counts = {DELIVERED: 0, REJECTED: 0, RETRY: 0}
    if not pending_due():
        return counts
    audience, url = admin_site()
    interface.signing_key_setting()
    started = clock()
    unavailable_in_a_row = 0
    while clock() - started < max_seconds and unavailable_in_a_row < MAX_FAILURES_IN_A_ROW:
        with transaction.atomic():
            event = _take(timezone.now())
            if event is None:
                break
            outcome, code, text, unavailable = _send(event, audience, url)
            _after_send(event, outcome, code, text, timezone.now())
        counts[outcome] += 1
        unavailable_in_a_row = unavailable_in_a_row + 1 if unavailable else 0
    return counts


def purge(now=None):
    """Delete the events delivered more than OUTBOX_RETENTION_DAYS ago, payload included
    (the sequence lives on in OutboxAggregate); rejected and pending rows stay. Returns
    the number deleted."""
    cutoff = (now or timezone.now()) - timedelta(days=OUTBOX_RETENTION_DAYS)
    deleted, _ = OutboxEvent.objects.filter(state=OutboxEvent.DELIVERED,
                                            delivered_at__lt=cutoff).delete()
    return deleted
