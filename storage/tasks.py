"""The stored files' background tasks (AD-8): deleting an object, and scanning a new one.

Deleting: `delete_object_task(object_uuid, actor_type, actor_uuid)` deletes the S3
object under both prefixes, the StoredObject row and writes the DeletionTrace, all inside
`run_once('storage.deleted', object_uuid)`: a second run (the task enqueued twice, or a
worker restarted mid-task) changes nothing and raises nothing. If S3 fails, nothing is
marked or removed and the task enqueues itself again after a delay, up to
MAX_ATTEMPTS; the last failure is reported to Sentry by object UUID only. After that the
hourly `sweepstorage` job enqueues it again (as the system) while the row is still
marked `delete_requested_at` (storage.sweep).

Scanning (AD-9, E6; epic-platform entry 12): `scan_object_task(object_uuid)`, enqueued
when an upload is recorded (storage.views.complete), runs on the site's scan worker (the
`scans` task backend and queue, systemd mytp-<site>-scan-worker) so a long scan never
holds the main worker. It streams `quarantine/<uuid>` from the bucket to clamd (INSTREAM,
storage.clamd) outside any database transaction, then, inside
`run_once('storage.scanned', object_uuid)`:

- clamd's OK: server-side copy to `clean/<uuid>` (idempotent, before any row lock),
  then under the row lock the quarantine object deleted and state `clean`; if the row
  was deleted or its deletion asked meanwhile, the clean copy is deleted too (the
  deletion wins);
- FOUND (a virus, or a file clamd could not fully scan: limits exceeded, encrypted or
  broken executables) or a size-limit ERROR: the object is deleted from the bucket (both
  prefixes), the row kept with state `rejected` and a DeletionTrace by the system
  (decision 2026-11-07).

Any other clamd ERROR, an unknown or garbled answer (outcome `scan_error`; decision
2026-11-08), clamd unreachable (or silent) or an S3 failure: nothing changes (the object stays in
quarantine) and the task enqueues itself again like the deletion (MAX_ATTEMPTS, then one
Sentry message); the sweep enqueues scans still in quarantine after an hour again. A
rerun after the copy but before the state was saved finds `clean/<uuid>` (and perhaps no
quarantine object any more) and finishes the promotion: one outcome, no error. An
object found under neither key (a rerun after an interrupted rejection) is rejected
with the outcome code `missing`.

Arguments, log lines and reports hold the object's UUID and the outcome code only (and
the signature name in the log line): never a key, URL, file name or credential (AD-23).
"""
import logging
import uuid
from datetime import timedelta

import sentry_sdk
from django.conf import settings
from django.tasks import task
from django.utils import timezone

from mytp_core.tasks import run_once

from . import clamd, s3
from .models import DeletionTrace, StoredObject

logger = logging.getLogger(__name__)

DELETE_EFFECT = 'storage.deleted'
SCAN_EFFECT = 'storage.scanned'
MAX_ATTEMPTS = 6
# Minutes before attempt n+1: 1, 2, 4, 8, 16.
RETRY_BASE_MINUTES = 1
# The task backend and queue of the scan worker (settings.TASKS, deploy/systemd/
# scan-worker.service.template).
SCAN_BACKEND = 'scans'
SCAN_QUEUE = 'scans'
# Seconds clamd may take for any step, the verdict on a 1 GiB file included (past clamd's
# own MaxScanTime of 10 minutes, deploy/clamav/clamd.conf).
SCAN_TIMEOUT = 15 * 60

# Outcome codes of a scan that did not decide (the object stays in quarantine).
CLAMD_UNAVAILABLE = 'clamd_unavailable'
S3_ERROR = 's3_error'
# Outcome code of an object found neither in quarantine nor clean: rejected.
MISSING = 'missing'


def _delete(object_id, actor_type, actor_uuid):
    s3.delete_keys(s3.object_keys(object_id))
    StoredObject.objects.filter(pk=object_id).delete()
    DeletionTrace.objects.get_or_create(
        object_id=object_id,
        defaults={'actor_type': actor_type, 'actor_uuid': actor_uuid or None,
                  'deleted_at': timezone.now()})


@task
def delete_object_task(object_uuid, actor_type=DeletionTrace.SYSTEM, actor_uuid='', attempt=1):
    """Delete one stored object for good (see the module docstring). Returns 'deleted',
    'already deleted', 'retry' or 'failed'."""
    object_id = uuid.UUID(str(object_uuid))
    if actor_type not in dict(DeletionTrace.ACTOR_TYPES):
        raise ValueError('unknown actor type')
    actor = uuid.UUID(actor_uuid) if actor_uuid else None
    try:
        done = run_once(DELETE_EFFECT, object_id, lambda: _delete(object_id, actor_type, actor))
    except Exception as exc:  # noqa: BLE001 - S3 or the database failed; nothing was kept
        reason = s3.describe_error(exc)
        logger.warning('storage: deleting object %s failed (%s), attempt %d of %d',
                       object_id, reason, attempt, MAX_ATTEMPTS)
        if attempt < MAX_ATTEMPTS:
            delay = timedelta(minutes=RETRY_BASE_MINUTES * 2 ** (attempt - 1))
            delete_object_task.using(run_after=timezone.now() + delay).enqueue(
                str(object_id), actor_type, actor_uuid, attempt + 1)
            return 'retry'
        with sentry_sdk.new_scope() as scope:
            scope.set_tag('storage.object', str(object_id))
            sentry_sdk.capture_message(
                f'Stored object deletion failed after {MAX_ATTEMPTS} attempts: {object_id} '
                f'({reason})', level='error')
        return 'failed'
    return 'deleted' if done else 'already deleted'


# --- Scanning ---------------------------------------------------------------------------

class _Undecided(Exception):
    """The scan gave no verdict (clamd or S3 failed); `code` is the outcome code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _verdict(object_id):
    """Stream `quarantine/<uuid>` to clamd. Returns (code, verdict):

    - (verdict.code, verdict) after a scan;
    - ('clean', None) when the quarantine object is gone but `clean/<uuid>` is there (a
      rerun after the promotion's copy and quarantine delete): finish the promotion;
    - (MISSING, None) when neither is there (a rerun after an interrupted rejection, or
      an object lost otherwise): nothing can ever be scanned, so it is rejected.

    Raises _Undecided."""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        try:
            chunks = s3.open_stream(s3.quarantine_key(object_id))
        except ClientError as exc:
            if not s3.is_not_found(exc):
                raise
            return ('clean' if s3.exists(s3.clean_key(object_id)) else MISSING), None
        verdict = clamd.scan_stream(chunks, socket_path=settings.STORAGE_CLAMD_SOCKET,
                                    timeout=SCAN_TIMEOUT)
        if not verdict.decided:
            # An ERROR other than a size limit, or an answer clamd should never give:
            # nothing was decided, so it is retried (decision 2026-11-08).
            raise _Undecided(verdict.code)
        return verdict.code, verdict
    except clamd.ClamdUnavailable as exc:
        raise _Undecided(CLAMD_UNAVAILABLE) from exc
    except (ClientError, BotoCoreError, OSError) as exc:
        raise _Undecided(S3_ERROR) from exc


def _copy_to_clean(object_id):
    """Server-side copy to `clean/<uuid>`, before any row lock (a 1 GiB copy takes a
    while). Idempotent: skipped when the clean object is already there."""
    target = s3.clean_key(object_id)
    if not s3.exists(target):
        s3.copy_object(s3.quarantine_key(object_id), target)


def _finish_promotion(object_id):
    """Under the row lock: delete the quarantine object and mark the row clean. If the
    row is gone or its deletion was asked meanwhile, the deletion wins: the clean copy
    just made is deleted too (the deletion task may already have run). False when
    nothing was promoted."""
    obj = StoredObject.objects.select_for_update().filter(pk=object_id).first()
    if obj is None or obj.delete_requested_at is not None:
        s3.delete_keys([s3.clean_key(object_id)])
        return False
    if obj.state != StoredObject.QUARANTINE:
        return False
    s3.delete_keys([s3.quarantine_key(object_id)])
    obj.state = StoredObject.CLEAN
    obj.save(update_fields=['state'])
    return True


def _reject(object_id):
    """Delete the object from the bucket (both keys; a key already gone is no error),
    keep the row as rejected, leave the system's DeletionTrace. False when the row may no
    longer change (gone, already decided, or its deletion asked: that deletion removes
    every key)."""
    obj = (StoredObject.objects.select_for_update()
           .filter(pk=object_id, state=StoredObject.QUARANTINE,
                   delete_requested_at__isnull=True).first())
    if obj is None:
        return False
    s3.delete_keys(s3.object_keys(object_id))
    obj.state = StoredObject.REJECTED
    obj.save(update_fields=['state'])
    DeletionTrace.objects.get_or_create(
        object_id=object_id,
        defaults={'actor_type': DeletionTrace.SYSTEM, 'actor_uuid': None,
                  'deleted_at': timezone.now()})
    return True


def _retry_or_report(object_id, code, attempt):
    logger.warning('storage: scan of object %s undecided (%s), attempt %d of %d',
                   object_id, code, attempt, MAX_ATTEMPTS)
    if attempt < MAX_ATTEMPTS:
        delay = timedelta(minutes=RETRY_BASE_MINUTES * 2 ** (attempt - 1))
        scan_object_task.using(run_after=timezone.now() + delay).enqueue(
            str(object_id), attempt + 1)
        return 'retry'
    with sentry_sdk.new_scope() as scope:
        scope.set_tag('storage.object', str(object_id))
        sentry_sdk.capture_message(
            f'Stored object scan failed after {MAX_ATTEMPTS} attempts: {object_id} ({code})',
            level='error')
    return 'failed'


@task(backend=SCAN_BACKEND, queue_name=SCAN_QUEUE)
def scan_object_task(object_uuid, attempt=1):
    """Scan one stored object and promote or reject it (see the module docstring).
    Returns the outcome code ('clean', 'infected', 'limit_exceeded', 'unscannable',
    'scan_error', 'missing'), 'already scanned', 'skipped' (the object is gone or being deleted),
    'retry' or 'failed'."""
    object_id = uuid.UUID(str(object_uuid))
    obj = StoredObject.objects.filter(pk=object_id).first()
    if obj is None or obj.delete_requested_at is not None:
        return 'skipped'
    if obj.state != StoredObject.QUARANTINE:
        return 'already scanned'
    try:
        code, verdict = _verdict(object_id)
        promote = code == 'clean'
        if promote and verdict is not None:
            _copy_to_clean(object_id)
        applied = []
        done = run_once(SCAN_EFFECT, object_id, lambda: applied.append(
            (_finish_promotion if promote else _reject)(object_id)))
    except _Undecided as exc:
        return _retry_or_report(object_id, exc.code, attempt)
    except Exception as exc:  # noqa: BLE001 - S3 or the database failed; nothing was kept
        from botocore.exceptions import BotoCoreError, ClientError
        code = S3_ERROR if isinstance(exc, (ClientError, BotoCoreError, OSError)) else 'error'
        return _retry_or_report(object_id, code, attempt)
    if not done:
        return 'already scanned'
    if applied != [True]:
        return 'skipped'
    if verdict is not None and verdict.result == clamd.FOUND:
        logger.warning('storage: object %s rejected (%s, %s)', object_id, code,
                       verdict.signature)
    elif not promote:
        logger.warning('storage: object %s rejected (%s)', object_id, code)
    else:
        logger.info('storage: object %s scanned clean', object_id)
    return code
