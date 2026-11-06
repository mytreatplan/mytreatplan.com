"""The stored files' background task (AD-8): deleting an object.

`delete_object_task(object_uuid, actor_type, actor_uuid)` deletes the S3 object under
both prefixes, the StoredObject row and writes the DeletionTrace, all inside
`run_once('storage.deleted', object_uuid)`: a second run (the task enqueued twice, or a
worker restarted mid-task) changes nothing and raises nothing. If S3 fails, nothing is
marked or removed and the task enqueues itself again after a delay, up to
MAX_ATTEMPTS; the last failure is reported to Sentry by object UUID only. After that the
hourly `sweepstorage` job enqueues it again (as the system) while the row is still
marked `delete_requested_at` (storage.sweep). Arguments,
log lines and reports never hold a key, URL, file name or credential (AD-23).
"""
import logging
import uuid
from datetime import timedelta

import sentry_sdk
from django.tasks import task
from django.utils import timezone

from mytp_core.tasks import run_once

from . import s3
from .models import DeletionTrace, StoredObject

logger = logging.getLogger(__name__)

DELETE_EFFECT = 'storage.deleted'
MAX_ATTEMPTS = 6
# Minutes before attempt n+1: 1, 2, 4, 8, 16.
RETRY_BASE_MINUTES = 1


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
