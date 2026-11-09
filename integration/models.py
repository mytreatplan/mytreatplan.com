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
- ConfigState and AdminDirectoryEntry (entry 4, AD-3 item 1, AD-22): the configuration
  snapshot pulled from the admin site (`integration.config.pull`): the applied version
  and the pull's state (one row), and the admin directory it carried (who an admin actor
  UUID is). Replaced together, in one transaction, only by a higher version.
- RemoteAccess (entry 5, AD-4, AD-22): this region's access log. Every remote read by an
  admin user (granted or refused) and every download of a stored file
  (storage.service), written before any data leaves (integration.access_log); every
  remote write (entry 6) before it is applied (`operation` `write`). Append only; kept
  ACCESS_RETENTION (5 calendar years, like the activity records), then deleted by the
  daily `eventlog_retention` job.
- SampleRecord (entry 5, staging only, INTERFACE_SAMPLE_RECORDS): the record the admin
  site reads and (entry 6) changes remotely until epic-ordering's patients exist; then
  it goes.
- RemoteWrite (entry 6, AD-3, AD-15): the idempotency keys of applied remote writes (one
  per admin user and key), with the request's hash and the answer given, so a retry gets
  the same answer and is applied once. Kept REMOTE_WRITE_RETENTION_DAYS, then deleted by
  the daily `purgeoutbox` job.
"""
import uuid
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db import models
from django.utils import timezone

from accounts.crypto import EncryptedCharField, EncryptedJSONField
from mytp_core.money import Money

# Decision (user, 2026-11-05): call logs are kept 90 days.
RETENTION_DAYS = 90
# Delivered outbox rows are kept 30 days (entry 3).
OUTBOX_RETENTION_DAYS = 30
# Decision (user, 2026-11-07, entry 5): access log rows are kept 5 calendar years, like
# the activity records (eventlog.retention.ACTIVITY_RETENTION).
ACCESS_RETENTION = relativedelta(years=5)
# Decision (user, 2026-11-09, entry 6): remote write idempotency keys are kept 7 days.
REMOTE_WRITE_RETENTION_DAYS = 7


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
    # Encrypted with the portal's field keys (decision 2026-11-06, entry 8: a client
    # payload holds email, names and mobile); decrypted only to build the envelope sent.
    payload = EncryptedJSONField()
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


# --- Configuration snapshot (entry 4) ---------------------------------------------------

class ConfigState(models.Model):
    """The applied configuration snapshot and the state of its pull (one row, pk 1).

    `version` is the applied snapshot's version (None before the first one);
    `last_error` the code of the last refused or failed pull (empty after a good one),
    never the snapshot. `warned_at`: when the one Sentry warning of the current outage (no
    good pull for an hour) was sent; saved before sending, cleared by the next good pull.
    `newer_format_version`: the last version kept out because its format is newer than
    this site reads (warned once per version)."""

    SINGLETON = 1

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON,
                                          editable=False)
    version = models.PositiveBigIntegerField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    last_pull_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=64, blank=True)
    warned_at = models.DateTimeField(null=True, blank=True)
    newer_format_version = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1),
                                   name='integration_configstate_singleton'),
        ]

    def __str__(self):
        return f'Configuration snapshot v{self.version or "-"}'


class AdminDirectoryEntry(models.Model):
    """An admin user, from the applied configuration snapshot: the name an admin actor
    UUID shows in this region's logs (AD-22). Never created here otherwise: admin users
    exist only on the admin site."""

    id = models.UUIDField(primary_key=True)
    display_name = models.CharField(max_length=150)
    title = models.CharField(max_length=150, blank=True)
    active = models.BooleanField()
    time_zone = models.CharField(max_length=64)

    class Meta:
        ordering = ['display_name', 'id']
        verbose_name_plural = 'admin directory entries'

    def __str__(self):
        return self.display_name


# --- Access log and the sample record (entry 5) ------------------------------------------

class AppendOnly(RuntimeError):
    """An access log row was to be changed, or deleted one by one: the log is append-only
    (only the retention job deletes old rows, in bulk)."""


class RemoteAccessQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnly('the access log is append-only')


class RemoteAccess(models.Model):
    """One access to this region's data (AD-4, AD-22): who (actor type and UUID, and the
    role the admin site gave), from where (the signing site and the IP), what (resource,
    object id, the names of the fields returned, never their values) and the outcome."""

    ADMIN = 'admin'
    DOCTOR = 'doctor'
    SYSTEM = 'system'
    ACTOR_TYPES = [(ADMIN, 'Admin user'), (DOCTOR, 'Portal user'), (SYSTEM, 'System')]
    GRANTED = 'granted'
    REFUSED = 'refused'
    OUTCOMES = [(GRANTED, 'Granted'), (REFUSED, 'Refused')]
    READ = 'read'
    WRITE = 'write'
    OPERATIONS = [(READ, 'Read'), (WRITE, 'Write')]

    at = models.DateTimeField(default=timezone.now, db_index=True)
    # Entry 6: a read (remote reads, downloads) or a remote write. A granted write's
    # `fields` are the names the answer returns, as a read's (decision user, 2026-11-09),
    # and its `access_reason` is `write:<changed names>` (`replay:<names>` for a retry
    # answered from its stored answer); granted means allowed to try (the row is written
    # before applying); a write refused as stale adds a refused row. A refused write's
    # `fields` are the names it tried to change.
    # db_default: the release before entry 6 still inserts rows during a rollback.
    operation = models.CharField(max_length=5, choices=OPERATIONS, default=READ,
                                 db_default=READ)
    actor_type = models.CharField(max_length=10, choices=ACTOR_TYPES)
    # None when a refused token carried no valid `sub`.
    actor_uuid = models.UUIDField(null=True, blank=True)
    # The admin role the token carried (mytp_core.access.ROLES); empty for a portal user.
    role = models.CharField(max_length=40, blank=True)
    # The site that signed the request (its `iss`), or this SITE for a local access.
    source_site = models.CharField(max_length=64, blank=True)
    # The caller's IP (eventlog.service.client_ip) and User-Agent, encrypted: a portal
    # user's are personal (as they were in the eventlog's download records).
    source_ip = EncryptedCharField(max_length=64, blank=True)
    user_agent = EncryptedCharField(max_length=512, blank=True)
    # A mytp_core.access resource (`sample_record`), or `stored_object` (a download).
    resource = models.CharField(max_length=64)
    object_id = models.CharField(max_length=64, blank=True)
    # A download: the stored object's purpose (StoredObject.purpose, such as `sample`)
    # and why the portal user downloaded it (storage.service's reason code, such as
    # `sample_page`). Both empty for a remote read; a remote write's access reason is
    # above (`operation`).
    purpose = models.CharField(max_length=40, blank=True)
    access_reason = models.CharField(max_length=50, blank=True)
    # The names of the fields returned, sorted; empty when refused.
    fields = models.JSONField(default=list, blank=True)
    outcome = models.CharField(max_length=10, choices=OUTCOMES)
    # Refused: mytp_core.access's reason (actor, region, role, assigned, field),
    # `not_found`, `download_refused`, or for a write `stale_version` or
    # `idempotency_key_reused`. Empty when granted.
    reason = models.CharField(max_length=32, blank=True)

    objects = RemoteAccessQuerySet.as_manager()

    class Meta:
        ordering = ['-at', '-id']
        verbose_name = 'access log entry'
        verbose_name_plural = 'access log'

    def __str__(self):
        outcome = f'{self.outcome} ({self.reason})' if self.reason else self.outcome
        return f'{self.actor_type} {self.actor_uuid or "?"} {self.resource} {outcome}'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnly('the access log is append-only')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnly('the access log is append-only')


class SampleRecord(models.Model):
    """The staging-only sample record (INTERFACE_SAMPLE_RECORDS, `seed_sample_record`): a
    patient name (encrypted, as patient data is), an amount (a financial field), an
    assignee (an admin user's UUID, for the Clinical User's `assigned` scope) and a
    note; `version` (entry 6) starts at 1 and goes up by one on each applied remote
    write (integration.sample.update_sample_record)."""

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient_name = EncryptedCharField(max_length=150)
    amount_value = models.DecimalField(max_digits=17, decimal_places=2,
                                       default=Decimal('0.00'))
    currency = models.CharField(max_length=3, default='EUR')
    assignee_uuid = models.UUIDField(null=True, blank=True)
    note = models.CharField(max_length=500, blank=True)
    # db_default: the release before entry 6 (seed_sample_record) still inserts rows
    # during a rollback.
    version = models.BigIntegerField(default=1, db_default=1)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Sample record {self.uuid}'

    @property
    def amount(self):
        return Money(self.amount_value, self.currency)


# --- Remote write idempotency keys (entry 6) ---------------------------------------------

class RemoteWrite(models.Model):
    """An applied remote write's idempotency key (decision 2026-11-09): one per admin user
    (`actor_uuid`, the token's `sub`) and key, with the SHA-256 of the request (resource,
    expected version, changes) and the answer given (HTTP status and body). A retry with
    the same key and the same body gets that answer again; another body is refused (422
    `idempotency_key_reused`). Written in the same transaction as the change
    (integration.remote), so a key exists only for an applied write. The body holds the
    record's fields the actor could read, so it is encrypted. Deleted after
    REMOTE_WRITE_RETENTION_DAYS by `purge_outbox` (integration.remote.purge_writes)."""

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    actor_uuid = models.UUIDField()
    idempotency_key = models.UUIDField()
    resource = models.CharField(max_length=64)
    object_id = models.CharField(max_length=64)
    # SHA-256 hex of the request's canonical JSON (integration.remote.request_hash).
    request_hash = models.CharField(max_length=64)
    status = models.PositiveSmallIntegerField()
    response = EncryptedJSONField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['actor_uuid', 'idempotency_key'],
                                    name='integration_remotewrite_actor_key'),
        ]

    def __str__(self):
        return f'Remote write {self.idempotency_key} ({self.resource} {self.status})'
