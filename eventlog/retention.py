"""Retention of the event log (signup entry 10, CAP-10), for every attempt kind but login.

Login records (kind `login`, epic-login entry 4) are left alone, neither anonymized nor
deleted, until their own 5-year retention (epic-login entry 8, decision 1).

The daily `eventlog_retention` job (deploy/jobs.conf) calls `anonymize` then
`delete_old`. Ages count from the attempt's `ended_at`: an attempt still open is never
anonymized or deleted by age. Each function takes `now` (default: the current time),
works in batches and returns its counts; a second run changes nothing.
"""
from datetime import timedelta

from django.utils import timezone

from .models import Attempt, Event

# The kinds these rules do not apply to (kept intact until epic-login entry 8).
EXCLUDED_KINDS = (Attempt.LOGIN,)

ANONYMIZE_AFTER = timedelta(days=30)
DELETE_AFTER = timedelta(days=730)
BATCH_SIZE = 1000


def anonymize(now=None, batch_size=BATCH_SIZE):
    """Clear `data` (the only personal column) of the events of every attempt (but the
    EXCLUDED_KINDS) that ended ANONYMIZE_AFTER ago or more. Event type, step, field,
    reason and times stay. Returns the number of events anonymized."""
    cutoff = (now or timezone.now()) - ANONYMIZE_AFTER
    pending = (Event.objects.filter(attempt__ended_at__lte=cutoff, data__isnull=False)
               .exclude(attempt__kind__in=EXCLUDED_KINDS))
    cleared = 0
    while True:
        pks = list(pending.order_by('pk').values_list('pk', flat=True)[:batch_size])
        if not pks:
            return cleared
        cleared += Event.objects.filter(pk__in=pks).update(data=None)


def delete_old(now=None, batch_size=BATCH_SIZE):
    """Delete the attempts (but the EXCLUDED_KINDS) that ended DELETE_AFTER ago or more,
    with their events. Returns (attempts deleted, events deleted)."""
    cutoff = (now or timezone.now()) - DELETE_AFTER
    old = Attempt.objects.filter(ended_at__lte=cutoff).exclude(kind__in=EXCLUDED_KINDS)
    attempts = events = 0
    while True:
        pks = list(old.order_by('pk').values_list('pk', flat=True)[:batch_size])
        if not pks:
            return attempts, events
        _, per_model = Attempt.objects.filter(pk__in=pks).delete()
        attempts += per_model.get(Attempt._meta.label, 0)
        events += per_model.get(Event._meta.label, 0)
