"""The event log (CAP-10): one Attempt per signup (later: login) attempt, and its ordered
Events, so an attempt can be reconstructed step by step.

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
    outcome. Not tied to a user or a pending registration, so it outlives both."""

    SIGNUP = 'signup'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=30)
    started_at = models.DateTimeField(default=timezone.now)
    # Set when the attempt ends; the retention jobs (signup entry 10) count from it.
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    outcome = models.CharField(max_length=50, blank=True)

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
