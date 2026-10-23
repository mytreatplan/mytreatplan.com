"""Retention of the event log.

Signup attempts and every other kind but the activity records (signup entry 10, CAP-10):
`anonymize` clears their personal values 30 days after the attempt ends, `delete_old`
deletes them 2 years after.

Activity records (epic-login entry 8): login records (kind `login`, epic-login entry 4),
page records (kind `page`, epic-login entry 7) and an erased account's purge record (kind
`account`, its encrypted email, QA A6) are a legal record. They are never
anonymized, keep their IP and User-Agent (encrypted in `Event.data`) intact, and
`delete_old_activity` deletes them 5 calendar years after the attempt ends. They hold the
account's pk with no foreign key, so they outlive an erased account's purge.

The daily `eventlog_retention` job (deploy/jobs.conf) calls `anonymize`, `delete_old`,
then `delete_old_activity`. Ages count from the attempt's `ended_at`: an attempt still
open is never anonymized or deleted by age. Each function takes `now` (default: the
current time), works in batches and returns its counts; a second run changes nothing.
"""
from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.utils import timezone

from .models import Attempt, Event

# The activity kinds: kept intact for ACTIVITY_RETENTION, outside signup's rules. A new
# activity kind is added here only.
ACTIVITY_KINDS = (Attempt.LOGIN, Attempt.PAGE, Attempt.ACCOUNT)

ANONYMIZE_AFTER = timedelta(days=30)
DELETE_AFTER = timedelta(days=730)
# 5 calendar years (epic-login entry 8, decisions 2 and 3); a change goes through review.
ACTIVITY_RETENTION = relativedelta(years=5)
BATCH_SIZE = 1000


def anonymize(now=None, batch_size=BATCH_SIZE):
    """Clear `data` (the only personal column) of the events of every attempt (but the
    ACTIVITY_KINDS) that ended ANONYMIZE_AFTER ago or more. Event type, step, field,
    reason and times stay. Returns the number of events anonymized."""
    cutoff = (now or timezone.now()) - ANONYMIZE_AFTER
    pending = (Event.objects.filter(attempt__ended_at__lte=cutoff, data__isnull=False)
               .exclude(attempt__kind__in=ACTIVITY_KINDS))
    cleared = 0
    while True:
        pks = list(pending.order_by('pk').values_list('pk', flat=True)[:batch_size])
        if not pks:
            return cleared
        cleared += Event.objects.filter(pk__in=pks).update(data=None)


def _delete(attempts_qs, batch_size):
    """Delete the attempts of `attempts_qs` with their events, in batches. Returns
    (attempts deleted, events deleted)."""
    attempts = events = 0
    while True:
        pks = list(attempts_qs.order_by('pk').values_list('pk', flat=True)[:batch_size])
        if not pks:
            return attempts, events
        _, per_model = Attempt.objects.filter(pk__in=pks).delete()
        attempts += per_model.get(Attempt._meta.label, 0)
        events += per_model.get(Event._meta.label, 0)


def delete_old(now=None, batch_size=BATCH_SIZE):
    """Delete the attempts (but the ACTIVITY_KINDS) that ended DELETE_AFTER ago or more,
    with their events. Returns (attempts deleted, events deleted)."""
    cutoff = (now or timezone.now()) - DELETE_AFTER
    old = Attempt.objects.filter(ended_at__lte=cutoff).exclude(kind__in=ACTIVITY_KINDS)
    return _delete(old, batch_size)


def delete_old_activity(now=None, batch_size=BATCH_SIZE):
    """Delete the activity attempts (ACTIVITY_KINDS) that ended ACTIVITY_RETENTION
    (5 calendar years) ago or more, with their events. Returns (attempts deleted, events
    deleted)."""
    cutoff = (now or timezone.now()) - ACTIVITY_RETENTION
    old = Attempt.objects.filter(ended_at__lte=cutoff, kind__in=ACTIVITY_KINDS)
    return _delete(old, batch_size)
