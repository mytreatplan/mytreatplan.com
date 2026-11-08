"""The hourly `sweepstorage` job (`manage.py sweep_storage`, deploy/jobs.conf):

1. Pending uploads older than STORAGE_ABORT_INCOMPLETE_DAYS are forgotten (the bucket's
   lifecycle rule has removed their parts by then).
2. `quarantine/` objects with no StoredObject row are deleted when older than
   ORPHAN_AGE (an upload completed in S3 whose /storage/complete/ never came), or at
   once when larger than its purpose's limit (the part sizes are not signed, so a client
   could store more than it declared).
3. Rows marked `delete_requested_at` more than REDELETE_AFTER ago get their
   `delete_object_task` enqueued again (as the system), until the deletion succeeds.
4. Rows still in quarantine RESCAN_AFTER after they were recorded (and not being
   deleted) get their `scan_object_task` enqueued again (epic-platform entry 12): a scan
   that never finished (clamd down past the task's retries, a lost task) is tried until
   it decides. Not when a scan of that object is already waiting or running on the
   scans backend (READY or RUNNING, its retries included), so a backlog is not doubled.
   When any object has waited more than STALE_SCAN_AFTER, one Sentry message per run
   gives their count (no UUID, no name): the scan worker or clamd is probably down.

Returns counts only; nothing logs a key, URL or name (AD-23).
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import purposes, s3
from .models import PendingUpload, StoredObject

ORPHAN_AGE = timedelta(days=1)
# Past the task's own retries (1 + 2 + 4 + 8 + 16 minutes).
REDELETE_AFTER = timedelta(minutes=45)
# Past the scan task's own retries (the same 31 minutes) and a long queue of scans.
RESCAN_AFTER = timedelta(hours=1)
# Waiting this long for a scan means the scan worker or clamd is not working.
STALE_SCAN_AFTER = timedelta(hours=6)
DELETE_BATCH = 1000


def purge_pending(now):
    cutoff = now - timedelta(days=settings.STORAGE_ABORT_INCOMPLETE_DAYS)
    deleted, _ = PendingUpload.objects.filter(created_at__lt=cutoff).delete()
    return deleted


def _size_limit(object_id):
    pending = PendingUpload.objects.filter(pk=object_id).first()
    purpose = purposes.get(pending.purpose) if pending else None
    if purpose is not None:
        return purpose.max_size
    return max((purposes.get(name).max_size for name in purposes.names()),
               default=purposes.DEFAULT_MAX_SIZE)


def orphan_keys(now, client):
    """The `quarantine/` keys to delete (see the module docstring)."""
    paginator = client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=settings.STORAGE_BUCKET, Prefix=s3.QUARANTINE_PREFIX):
        listed = {}
        for item in page.get('Contents', []):
            object_id = s3.object_id_from_key(item.get('Key'))
            if object_id is not None:
                listed[object_id] = item
        known = set(StoredObject.objects.filter(pk__in=listed).values_list('pk', flat=True))
        for object_id, item in listed.items():
            if object_id in known:
                continue
            modified = item.get('LastModified')
            too_old = modified is not None and modified < now - ORPHAN_AGE
            if too_old or (item.get('Size') or 0) > _size_limit(object_id):
                yield s3.quarantine_key(object_id)


def delete_orphans(now, client=None):
    client = client or s3.make_client()
    deleted = 0
    with s3.out_of_sentry():
        keys = list(orphan_keys(now, client))
        for start in range(0, len(keys), DELETE_BATCH):
            s3.delete_keys(keys[start:start + DELETE_BATCH], client=client)
            deleted += len(keys[start:start + DELETE_BATCH])
    return deleted


def requeue_deletions(now):
    from .models import DeletionTrace
    from .tasks import delete_object_task

    pks = list(StoredObject.objects.filter(delete_requested_at__lt=now - REDELETE_AFTER)
               .values_list('pk', flat=True))
    for pk in pks:
        delete_object_task.enqueue(str(pk), DeletionTrace.SYSTEM, '')
    return len(pks)


def _pending_scans():
    """The object UUIDs (as strings) with a scan waiting or running on the scans backend.
    Read from django-tasks-db's table: the sweep is not task code."""
    from django.tasks import TaskResultStatus
    from django_tasks_db.models import DBTaskResult

    from .tasks import SCAN_BACKEND, scan_object_task

    rows = DBTaskResult.objects.filter(
        backend_name=SCAN_BACKEND, task_path=scan_object_task.module_path,
        status__in=[TaskResultStatus.READY, TaskResultStatus.RUNNING],
    ).values_list('args_kwargs', flat=True)
    return {str(row['args'][0]) for row in rows if row and row.get('args')}


def requeue_scans(now):
    from .tasks import scan_object_task

    waiting = StoredObject.objects.filter(state=StoredObject.QUARANTINE,
                                          delete_requested_at__isnull=True)
    pks = list(waiting.filter(created_at__lt=now - RESCAN_AFTER).values_list('pk', flat=True))
    pending = _pending_scans() if pks else set()
    requeued = 0
    for pk in pks:
        if str(pk) not in pending:
            scan_object_task.enqueue(str(pk))
            requeued += 1
    return requeued


def report_stale_scans(now):
    """Count the objects waiting for their scan for more than STALE_SCAN_AFTER; one
    Sentry message (the count only) when there are any."""
    import sentry_sdk

    stale = StoredObject.objects.filter(
        state=StoredObject.QUARANTINE, delete_requested_at__isnull=True,
        created_at__lt=now - STALE_SCAN_AFTER).count()
    if stale:
        sentry_sdk.capture_message(
            f'{stale} stored object(s) waiting for their virus scan for more than '
            f'{int(STALE_SCAN_AFTER.total_seconds() // 3600)} hours: check the scan worker '
            f'and clamd', level='error')
    return stale


def sweep(now=None, client=None):
    """Run the steps; returns their counts. Without the bucket settings (a site with
    no file storage yet) the bucket step is skipped (None)."""
    now = now or timezone.now()
    counts = {'pending': purge_pending(now), 'orphans': None,
              'redeletes': requeue_deletions(now), 'rescans': requeue_scans(now),
              'stale_scans': report_stale_scans(now)}
    if s3.missing_setting() is None:
        counts['orphans'] = delete_orphans(now, client)
    return counts
