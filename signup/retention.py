"""Retention of unfinished signups (signup entry 10, CAP-9).

The hourly `purge_pending_signups` job (deploy/jobs.conf) calls `purge_pending`,
`end_orphan_attempts` and `clear_expired_cache`; signup entry 8 (cancel) reuses
`remove_pending(row, outcome)`. The three job functions take `now` (default: the current
time) and return their counts; `remove_pending` returns whether it deleted the row. A
second run changes nothing. Nothing here logs or prints personal values.

Only the registration store is cleared here. The event log keeps an attempt's personal
values until 30 days after it ends (eventlog.retention).
"""
from django.conf import settings
from django.db import connection
from django.utils import timezone

from eventlog import service as eventlog
from eventlog.models import Attempt

from .models import ACTIVE_FOR, PendingRegistration

BATCH_SIZE = 500
TIMEOUT = 'timeout'
PURGED_EVENT = 'purged'


def remove_pending(row, outcome):
    """Delete the pending registration `row` (its legal choices go with it, CASCADE) and,
    if its attempt is still open, record `purged` (reason `outcome`, no data) and end
    the attempt with `outcome` (`timeout` for the purge, `cancelled` for entry 8).

    Returns True if the row was deleted here, False if it was already gone."""
    deleted, _ = PendingRegistration.objects.filter(pk=row.pk).delete()
    if not deleted:
        return False
    attempt = eventlog.get_attempt(row.attempt_id)
    if attempt is not None and attempt.ended_at is None:
        eventlog.record(attempt, PURGED_EVENT, reason=outcome)
        eventlog.end_attempt(attempt, outcome)
    return True


def purge_pending(now=None, batch_size=BATCH_SIZE):
    """Remove every pending registration created ACTIVE_FOR (24 h) ago or more, ending
    its open attempt as `timeout`. Returns the number of rows removed."""
    now = now or timezone.now()
    removed = 0
    while True:
        batch = list(PendingRegistration.objects.expired(now).order_by('created_at', 'pk')
                     .only('pk', 'attempt_id')[:batch_size])
        if not batch:
            return removed
        for row in batch:
            removed += remove_pending(row, TIMEOUT)


def end_orphan_attempts(now=None, batch_size=BATCH_SIZE):
    """End as `timeout` the open signup attempts started ACTIVE_FOR ago or more that no
    pending registration points to (refused at step 1, or a resume offer never taken),
    so they end and are later anonymized. Returns the number of attempts ended."""
    now = now or timezone.now()
    orphans = (Attempt.objects
               .filter(kind=Attempt.SIGNUP, ended_at__isnull=True,
                       started_at__lte=now - ACTIVE_FOR)
               .exclude(pk__in=PendingRegistration.objects.filter(attempt__isnull=False)
                        .values('attempt_id')))
    ended = 0
    while True:
        pks = list(orphans.order_by('pk').values_list('pk', flat=True)[:batch_size])
        if not pks:
            return ended
        # Same filter again: a row created since the SELECT keeps its attempt open.
        ended += orphans.filter(pk__in=pks).update(
            ended_at=timezone.now(), outcome=TIMEOUT)


def clear_expired_cache(now=None):
    """Delete the expired rows of the database cache (they hold blind indexes; Django
    culls only past MAX_ENTRIES). Returns the number of rows deleted (0 when the
    default cache is not the database cache)."""
    config = settings.CACHES.get('default', {})
    if config.get('BACKEND') != 'django.core.cache.backends.db.DatabaseCache':
        return 0
    now = (now or timezone.now()).replace(microsecond=0)
    table = connection.ops.quote_name(config['LOCATION'])
    with connection.cursor() as cursor:
        cursor.execute(f'DELETE FROM {table} WHERE expires < %s',
                       [connection.ops.adapt_datetimefield_value(now)])
        return cursor.rowcount
