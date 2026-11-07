"""Downloading and deleting stored files (AD-9, AD-23).

- `download_url(obj, user, reason)`: the purpose's hook decides; the access is recorded
  first in the region's access log (integration.RemoteAccess, epic-admin-interface
  entry 5, decision 2026-11-07: resource `stored_object`, the object's UUID and
  purpose, the reason as `access_reason`, the portal user's UUID, IP and User-Agent;
  never the file's name), committed before
  a presigned GET (5 minutes at most) is made. A refusal is recorded too, best effort. If
  the record cannot be written there is no URL. (Before entry 5 these were eventlog
  activity records of kind `file`; the old ones stay there until their retention ends.)
- `delete(obj, actor)`: marks the row (`delete_requested_at`: no download URL from then
  on) and enqueues `delete_object_task` when the transaction commits; the task removes
  the S3 object, the row, and leaves a DeletionTrace. The `sweepstorage` job enqueues it
  again for rows still marked (storage.sweep).
"""
import re

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from django.conf import settings

from eventlog.service import client_ip
from integration import access_log
from integration.models import RemoteAccess
from mytp_core.tasks import enqueue_on_commit

from . import purposes, s3
from .models import DeletionTrace

REASON_RE = re.compile(r'^[a-z][a-z0-9_]{0,49}$')
_EXTENSION_RE = re.compile(r'\.([A-Za-z0-9]{1,10})$')

DOWNLOAD = 'download'
DOWNLOAD_REFUSED = 'download_refused'
# The access log's resource for a download.
RESOURCE = 'stored_object'


class DownloadRefused(PermissionDenied):
    """The purpose's hook (or the object's state) does not allow this download."""


class AccessRecordFailed(RuntimeError):
    """The access record could not be written, so no download URL is given."""


class NotConfigured(RuntimeError):
    """A STORAGE_* setting the bucket needs is empty."""


def _record(event, obj, user, reason, request):
    """Write the download (or its refusal) to the access log: the RemoteAccess row, or
    None if it could not be written."""
    values = {
        'actor_type': RemoteAccess.DOCTOR if user else RemoteAccess.SYSTEM,
        'actor_uuid': getattr(user, 'uid', None) if user else None,
        'source_site': settings.SITE,
        'source_ip': client_ip(request)[:64] if request is not None else '',
        'user_agent': (request.META.get('HTTP_USER_AGENT', '')[:512]
                       if request is not None else ''),
        'resource': RESOURCE, 'object_id': str(obj.pk), 'purpose': obj.purpose,
        'access_reason': reason,
    }
    if event == DOWNLOAD_REFUSED:
        return access_log.record_refused(reason=DOWNLOAD_REFUSED, **values)
    try:
        return access_log.record_granted(fields=['download_url'], **values)
    except access_log.AccessLogUnavailable:
        return None


def download_filename(obj):
    """The name the browser saves a download as: the object's UUID with the original
    file's extension (the name itself never goes into a URL)."""
    match = _EXTENSION_RE.search(obj.original_name or '')
    return f'{obj.pk}.{match.group(1).lower()}' if match else str(obj.pk)


def download_url(obj, user, reason, *, request=None):
    """A presigned GET for `obj`, valid at most 5 minutes, for `user`, recorded with
    `reason` (a code such as `sample_page`). Raises DownloadRefused (recorded),
    AccessRecordFailed (no URL) or NotConfigured."""
    if not isinstance(reason, str) or not REASON_RE.match(reason):
        raise ValueError('reason must be a code: lowercase letters, digits and "_"')
    if (missing := s3.missing_setting()):
        raise NotConfigured(f'{missing} is not set')
    purpose = purposes.get(obj.purpose)
    if obj.delete_requested_at is not None or not purposes.may_download(purpose, user, obj):
        _record(DOWNLOAD_REFUSED, obj, user, reason, request)
        raise DownloadRefused('this download is not allowed')
    if _record(DOWNLOAD, obj, user, reason, request) is None:
        raise AccessRecordFailed('the access record could not be written')
    return s3.presign_download(obj.key, content_type=obj.content_type,
                               filename=download_filename(obj))


def actor_of(actor):
    """(actor type, actor UUID as a string or '') for a user or None (the system)."""
    if actor is None:
        return DeletionTrace.SYSTEM, ''
    return DeletionTrace.USER, str(actor.uid)


def delete(obj, actor):
    """Delete `obj` (S3 object, row) after the current transaction commits, leaving a
    DeletionTrace; `actor` is the user who asked, or None for the system. Only the
    object's UUID and the actor go into the task (AD-23). The row is marked in the
    caller's transaction."""
    from .models import StoredObject
    from .tasks import delete_object_task

    if obj.delete_requested_at is None:
        obj.delete_requested_at = timezone.now()
        StoredObject.objects.filter(pk=obj.pk, delete_requested_at__isnull=True).update(
            delete_requested_at=obj.delete_requested_at)
    actor_type, actor_uuid = actor_of(actor)
    enqueue_on_commit(delete_object_task, str(obj.pk), actor_type, actor_uuid)
