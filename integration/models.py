"""The interface's tables (epic-admin-interface, AD-4).

- InterfaceCall (entry 1): the log of the calls this site receives on /api/. One row per
  request to /api/, accepted or refused, written by
  integration.calls.InterfaceCallMiddleware. `iss` and `kid` are what the token claimed
  (unverified when the call was refused); never the token itself. Kept RETENTION_DAYS
  days, then deleted by the daily `purgeinterfacecalls` job (`manage.py
  purge_interface_calls`).
- OutboxEvent (entry 3, AD-3 item 2, AD-18, AD-19): the events this portal sends to the
  admin site, written in the caller's transaction by `integration.outbox.record` and sent
  by the dispatcher (`integration.outbox.dispatch`). Delivered rows are deleted after
  OUTBOX_RETENTION_DAYS by the daily `purgeoutbox` job; rejected rows are kept.
"""
import uuid

from django.db import models
from django.utils import timezone

# Decision (user, 2026-11-05): call logs are kept 90 days.
RETENTION_DAYS = 90
# Delivered outbox rows are kept 30 days (entry 3).
OUTBOX_RETENTION_DAYS = 30


class InterfaceCall(models.Model):
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    iss = models.CharField('issuer', max_length=64, blank=True)
    kid = models.CharField('key id', max_length=64, blank=True)
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=512)
    accepted = models.BooleanField(default=False)
    # A refusal code of mytp_core.interface (missing, signature, unknown_key, audience,
    # expired, request_hash, replayed), `unavailable` (no signing key) or `not_checked`
    # (no signed view answered: an unknown path or method); empty when accepted.
    reason = models.CharField(max_length=32, blank=True)
    status = models.PositiveSmallIntegerField()
    # The caller's IP (eventlog.service.client_ip); text, as it is not checked.
    ip = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ['-received_at']

    def __str__(self):
        outcome = 'accepted' if self.accepted else f'refused ({self.reason})'
        return f'{self.method} {self.path} from {self.iss or "?"} {outcome}'


class OutboxAggregate(models.Model):
    """The sequence counter of one aggregate (decision 2026-11-05, after review): locked
    and incremented by `outbox.record` only, so concurrent writers wait for each other
    and never fail, and the dispatcher never blocks them. Never purged: the sequence goes
    on after the aggregate's delivered events are deleted."""

    aggregate_id = models.UUIDField(primary_key=True)
    last_sequence = models.PositiveBigIntegerField(default=0)

    def __str__(self):
        return f'{self.aggregate_id} #{self.last_sequence}'


class OutboxEvent(models.Model):
    """One event to send to the admin site: its envelope's fields, and the delivery state.

    `sequence` numbers the events of one aggregate from 1 (taken from its OutboxAggregate
    counter by `record`); the dispatcher sends only the lowest pending sequence of each
    aggregate. `last_error` is `<code>: <short text>`, never a payload.
    """

    PENDING = 'pending'
    DELIVERED = 'delivered'
    REJECTED = 'rejected'
    STATES = [(PENDING, 'Pending'), (DELIVERED, 'Delivered'), (REJECTED, 'Rejected')]

    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    type = models.CharField(max_length=100)
    # The payload model's version of the type (the envelope's `version`).
    version = models.PositiveIntegerField(default=1)
    aggregate_id = models.UUIDField()
    aggregate_version = models.PositiveBigIntegerField()
    sequence = models.PositiveBigIntegerField()
    occurred_at = models.DateTimeField(default=timezone.now)
    # The region the event belongs to (settings.REGION when recorded).
    region = models.CharField(max_length=2)
    # Built by mytp_core.contracts.build_payload: the type's closed model, central checks.
    payload = models.JSONField()
    state = models.CharField(max_length=10, choices=STATES, default=PENDING)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.CharField(max_length=200, blank=True)
    # The first failed attempt of the current run of failures, and when the one Sentry
    # warning for a row failing 24 hours went out (once per row).
    failing_since = models.DateTimeField(null=True, blank=True)
    warned_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['aggregate_id', 'sequence'],
                                    name='integration_outbox_aggregate_sequence'),
        ]
        indexes = [
            models.Index(fields=['state', 'next_attempt_at'],
                         name='integration_outbox_due'),
            models.Index(fields=['state', 'delivered_at'],
                         name='integration_outbox_delivered'),
        ]

    def __str__(self):
        return (f'{self.type} {self.aggregate_id} v{self.aggregate_version} '
                f'#{self.sequence} ({self.state})')
