"""The event log (CAP-10): one Attempt per signup attempt, and its ordered Events, so an
attempt can be reconstructed step by step. Login activity (epic-login entry 4) is one
Attempt of kind `login` per record (eventlog.service.record_activity), tied to the
account by `account_id`; so is each page shown to a logged-in user (kind `page`, epic-login
entry 7, accounts.page_records), and an erased account's purge (kind `account`, QA A6,
accounts.erasure).

Only `Event.data` holds personal values (email, IP address, browser string, ...), encrypted
with the 2.2 field encryption (accounts.crypto). The other columns are non-personal, so
anonymizing an event (signup entry 10) means clearing `data`. Passwords and verification
codes are never logged. Write through eventlog.service, never directly.
"""
import uuid

from django.db import models
from django.utils import timezone

from accounts.crypto import EncryptedJSONField


class Attempt(models.Model):
    """One attempt at a flow (`kind`): started at its first data received, ended with an
    outcome. Never a foreign key to a user or a pending registration, so it outlives both.

    `account_id` is the account's pk (a plain number, not a ForeignKey, kept after the
    account is purged), set for login, page and account records; never the email blind index, which
    erasure replaces."""

    SIGNUP = 'signup'
    LOGIN = 'login'
    PAGE = 'page'
    # An erased account's purge (accounts.erasure, QA A6): its pk and encrypted email.
    ACCOUNT = 'account'

    # AD-5: crosses sites; its UUID primary key is its global id.
    CROSSES_SITES = True

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=30)
    started_at = models.DateTimeField(default=timezone.now)
    # Set when the attempt ends; the retention jobs (signup entry 10) count from it.
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    outcome = models.CharField(max_length=50, blank=True)
    account_id = models.BigIntegerField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.kind} attempt {self.pk}'


class Event(models.Model):
    """One thing that happened in an attempt, in order (`created_at`, then `id`)."""

    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name='events')
    created_at = models.DateTimeField(default=timezone.now)
    step = models.CharField(max_length=50, blank=True)
    event = models.CharField(max_length=50)
    field = models.CharField(max_length=100, blank=True)
    reason = models.CharField(max_length=100, blank=True)
    # Personal values (JSON), encrypted; NULL once anonymized or when there are none.
    data = EncryptedJSONField(null=True, blank=True)

    class Meta:
        ordering = ['created_at', 'id']

    def __str__(self):
        return f'{self.event} ({self.step})' if self.step else self.event
