"""The regional access log (epic-admin-interface entry 5, AD-4, AD-22): `RemoteAccess` rows.

- `record_granted(...)`: written with a plain create in its own durable transaction, so it
  is committed before the caller builds any data. Any failure raises
  `AccessLogUnavailable`: the caller returns no data (a remote read answers 503
  `access_log_unavailable`; a download gives no URL). Fail closed. Call it outside any
  transaction: inside one, the durable block refuses (so no data either), since a row
  that a later rollback could undo would not be "written first".
- `record_granted_list(...)` (epic-ordering 9.18): a list read, one granted row per listed
  record, committed together, under the same fail-closed rule.
- `record_refused(...)`: best effort. A failure is logged (its error class only) and
  never changes the refusal the caller gives.

`operation` (entry 6): `read` (the default: remote reads, downloads) or `write` (a remote
write, logged granted before it is applied, with the names of the fields the answer
returns, as a read; the changed names are in `access_reason`, `write:note`).

Rows hold names and codes, never a field's value. Not the event log: its never-raise
contract stays as it is.
"""
import logging

from django.db import transaction

from .models import RemoteAccess

logger = logging.getLogger('integration.access_log')


class AccessLogUnavailable(RuntimeError):
    """The access log row could not be written, so no data may leave."""


def _create(**values):
    with transaction.atomic(durable=True):
        return RemoteAccess.objects.create(**values)


def record_granted(*, actor_type, actor_uuid, resource, object_id, fields, role='',
                   source_site='', source_ip='', user_agent='', purpose='',
                   access_reason='', operation=RemoteAccess.READ):
    """Write a granted access (with the sorted names of the fields that will be returned,
    or for a write those it changes) and commit it; AccessLogUnavailable on any
    failure."""
    try:
        return _create(operation=operation, actor_type=actor_type, actor_uuid=actor_uuid,
                       role=role,
                       source_site=source_site, source_ip=source_ip,
                       user_agent=user_agent[:512], resource=resource,
                       object_id=str(object_id), purpose=purpose,
                       access_reason=access_reason, fields=sorted(fields),
                       outcome=RemoteAccess.GRANTED, reason='')
    except Exception as error:  # noqa: BLE001 - any failure means no data
        logger.error('access log write failed: %s %s (%s)', resource, object_id,
                     type(error).__name__)
        raise AccessLogUnavailable('the access could not be logged') from None


def record_granted_list(*, actor_type, actor_uuid, resource, object_ids, fields, role='',
                        source_site='', source_ip='', user_agent='', access_reason=''):
    """A granted list read (epic-ordering 9.18): one granted row per listed record (each
    with the sorted names of the fields returned), all written and committed together;
    AccessLogUnavailable on any failure (then nothing is written and no data leaves)."""
    rows = [RemoteAccess(operation=RemoteAccess.READ, actor_type=actor_type,
                         actor_uuid=actor_uuid, role=role, source_site=source_site,
                         source_ip=source_ip, user_agent=user_agent[:512],
                         resource=resource, object_id=str(object_id), purpose='',
                         access_reason=access_reason, fields=sorted(fields),
                         outcome=RemoteAccess.GRANTED, reason='')
            for object_id in object_ids]
    try:
        with transaction.atomic(durable=True):
            return RemoteAccess.objects.bulk_create(rows)
    except Exception as error:  # noqa: BLE001 - any failure means no data
        logger.error('access log write failed: %s list of %d (%s)', resource, len(rows),
                     type(error).__name__)
        raise AccessLogUnavailable('the access could not be logged') from None


def record_refused(*, actor_type, actor_uuid, resource, object_id, reason, role='',
                   source_site='', source_ip='', user_agent='', purpose='',
                   access_reason='', operation=RemoteAccess.READ, fields=()):
    """Write a refused access, best effort: the row, or None if it could not be
    written. `fields`: for a refused write, the names of the fields it would have
    changed (empty for a read)."""
    try:
        return _create(operation=operation, actor_type=actor_type, actor_uuid=actor_uuid,
                       role=role[:40],
                       source_site=source_site, source_ip=source_ip,
                       user_agent=user_agent[:512], resource=resource,
                       object_id=str(object_id or ''), purpose=purpose,
                       access_reason=access_reason, fields=sorted(fields)[:50],
                       outcome=RemoteAccess.REFUSED, reason=reason)
    except Exception as error:  # noqa: BLE001 - a refusal is never blocked by its log
        logger.error('access log write failed for a refusal: %s (%s)', resource,
                     type(error).__name__)
        return None
