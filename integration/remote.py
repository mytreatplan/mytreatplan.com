"""Remote reads (epic-admin-interface entry 5, AD-4, AD-17, AD-23, interface I6): the admin
site reads one record of this region live; this region decides and logs.

`remote_read(request, resource, load, build)` is the whole flow of a read endpoint
(behind `signed_endpoint`, so the signature is checked first):

1. Only the admin site may read (another signed peer: 401 ``unknown_key``).
2. The admin user's access from the token (`mytp_core.interface.remote_actor`): ``sub`` a
   UUID, ``role`` a known role, ``regions`` region codes; then the shared rules
   (`mytp_core.access.decide`): this REGION among the actor's regions, a rule for the
   role, and for the ``assigned`` scope the record's assignee is the actor. Any refusal
   is 403 ``forbidden``, logged best effort; an unknown record 404 ``not_found``, logged
   the same way.
3. The granted access is written to the access log and committed
   (`access_log.record_granted`) with the names of the fields to be returned, before
   any data is built. If it cannot be written: 503 ``access_log_unavailable``, no data.
4. Only then `build(record)` gives the record's fields; the answer keeps only the role's,
   exactly the names logged, as `mytp_core.contracts.remote_record`, with
   ``Cache-Control: no-store``.

A versioned resource (`version(record)`, entry 6) adds the record's version to the answer.

`remote_write(request, resource, load, update, build, ...)` is the whole flow of a write
endpoint (entry 6, interface I7, AD-3, AD-15), in the order of the plan:

1. Only the admin site; the actor and this region, then the role (403 ``forbidden``,
   logged refused), as a read.
2. The body is a `RemoteWriteRequest` (closed; else 400 ``invalid_body``, logged refused);
   every changed name is in the role's writable fields (`mytp_core.access.decide_write`;
   else 403 ``forbidden``, reason ``field``, logged refused with the names); the values
   pass the resource's closed changes model (else 400). The record is loaded: the one
   the request's ``object_id`` names, when it names one (else 404: never another
   record), or the resource's default; for the ``assigned`` scope it must be the actor's
   (403).
3. The idempotency key (`RemoteWrite`, per actor): the same key with the same request
   (its hash, ``object_id`` included) gets the stored answer again, with only the fields
   the actor may read now, after a granted ``write`` row (access reason ``replay:...``)
   is committed (no row, no answer: 503); with another request 422
   ``idempotency_key_reused`` (logged refused). Nothing applied.
4. The write is logged granted and committed first: operation ``write``, ``fields`` the
   names of the fields the answer returns (as a read; decision user, 2026-11-09), and the
   names it changes in the access reason (``write:note``; `access_reason_for`). If that
   fails, 503 ``access_log_unavailable`` and nothing applied. Refused rows list the names
   the request tried to change.
5. In one transaction: the key is claimed (an insert under the unique (actor, key)
   constraint, so a concurrent retry waits and then replays), then `update` (the
   resource's service function) locks the row, checks ``expected_version`` and applies
   with version + 1, and the answer is stored with the key. A stale version rolls all of
   it back: 409 ``stale_version`` with the current version, logged refused (best effort).
6. 200 with the record as the actor may read it, ``Cache-Control: no-store``.

Nothing of the record goes into a log line, a task or the cache; the stored answer is
encrypted (`RemoteWrite.response`).
"""
import hashlib
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from pydantic import ValidationError

from eventlog.service import client_ip
from mytp_core import access, interface
from mytp_core.contracts import (RemoteWriteRequest, canonical_json, changes_for,
                                 remote_record, stale_version_body)
from mytp_core.contracts import errors as contract_errors

from . import access_log
from .models import REMOTE_WRITE_RETENTION_DAYS, RemoteAccess, RemoteWrite

logger = logging.getLogger('integration.remote')

NOT_FOUND = contract_errors.NOT_FOUND


def no_store(response):
    """``Cache-Control: no-store`` (and Django's never-cache headers) on `response`."""
    add_never_cache_headers(response)
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private, max-age=0'
    return response


def error(code):
    body, status = contract_errors.remote_read_error(code)
    return no_store(JsonResponse(body, status=status))


def _source(request):
    return {'source_site': request.interface_claims['iss'][:64],
            'source_ip': client_ip(request)[:64],
            'user_agent': request.META.get('HTTP_USER_AGENT', '')[:512]}


def _actor_values(actor, claims):
    if actor is not None:
        return {'actor_uuid': actor.sub, 'role': actor.role}
    role = claims.get('role')
    return {'actor_uuid': None, 'role': role if role in access.ROLES else ''}


def remote_read(request, resource, object_id, load, build, assignee=None, version=None):
    """The answer to a remote read of `resource`. `load(object_id)` gives the record or
    None (`object_id` as asked, maybe None; the log then names the record's pk);
    `assignee(record)` its assignee UUID (for the ``assigned`` scope; None when the
    resource has none); `build(record)` the dict of all its fields (JSON-ready), called
    only after the access is logged; `version(record)` its version, for a versioned
    resource (entry 6)."""
    from .views import _not_the_admin_site

    refused = _not_the_admin_site(request)
    if refused is not None:
        return refused
    claims = request.interface_claims
    actor = interface.remote_actor(claims)
    who = {'actor_type': RemoteAccess.ADMIN, **_actor_values(actor, claims),
           **_source(request)}

    def refuse(reason, code=contract_errors.FORBIDDEN):
        access_log.record_refused(resource=resource, object_id=object_id, reason=reason,
                                  **who)
        logger.info('remote read refused: %s %s (%s) by %s', resource, object_id, reason,
                    who['actor_uuid'] or '?')
        return error(code)

    # The actor and this region first, before looking the record up.
    early = access.decide(resource, actor, settings.REGION, assignee=None)
    if not early.granted and early.reason != access.REASON_ASSIGNED:
        return refuse(early.reason)
    record = load(object_id)
    if record is None:
        return refuse(NOT_FOUND, NOT_FOUND)
    object_id = str(record.pk)
    decision = access.decide(resource, actor, settings.REGION,
                             assignee=assignee(record) if assignee else None)
    if not decision.granted:
        return refuse(decision.reason)
    try:
        access_log.record_granted(resource=resource, object_id=object_id,
                                  fields=decision.fields, **who)
    except access_log.AccessLogUnavailable:
        return error(contract_errors.ACCESS_LOG_UNAVAILABLE)
    # Exactly the fields logged (a KeyError if `build` lacks one: a bug, never less data
    # than the log says without an error).
    full = build(record)
    data = {name: full[name] for name in decision.fields}
    logger.info('remote read: %s %s by %s', resource, object_id, actor.sub)
    return no_store(JsonResponse(remote_record(
        resource, settings.REGION, object_id, data,
        version=version(record) if version else None)))


# --- Remote writes (entry 6) ------------------------------------------------------------

# A write request is small: anything larger is not one.
MAX_WRITE_BODY_BYTES = 16 * 1024
STALE_VERSION = contract_errors.STALE_VERSION
INVALID_BODY = contract_errors.INVALID_BODY
KEY_REUSED = contract_errors.IDEMPOTENCY_KEY_REUSED
WRITE_REASON = 'write'  # the access reason of an applied write (granted row)
REPLAY = 'replay'  # the access reason of a write answered from its stored answer
MAX_ACCESS_REASON = RemoteAccess._meta.get_field('access_reason').max_length


def access_reason_for(kind, names):
    """``<kind>:<name>,<name>`` (``write:note``): the access reason of a granted write row,
    with the names the write changes (its ``fields`` are the names returned, as a read's).
    Names that do not fit the column are counted instead: ``write:a,b,+3``. The full list
    is the request's ``changes`` (the role's writable fields at most)."""
    text = f'{kind}:'
    for index, name in enumerate(names):
        rest = len(names) - index
        piece = name if index == 0 else f',{name}'
        tail = '' if rest == 1 else f',+{rest - 1}'
        if len(text) + len(piece) + len(tail) > MAX_ACCESS_REASON:
            return f'{text}{"," if index else ""}+{rest}'[:MAX_ACCESS_REASON]
        text += piece
    return text


class StaleVersion(Exception):
    """A service function's record is no longer at the expected version; nothing was
    changed. `current_version` is its version now."""

    def __init__(self, current_version):
        self.current_version = current_version
        super().__init__('the record was changed meanwhile')


class RecordGone(Exception):
    """A service function's record no longer exists."""


def write_error(code):
    body, status = contract_errors.remote_write_error(code)
    return no_store(JsonResponse(body, status=status))


def _no_constant(name):
    raise ValueError('not JSON')


def _write_request(raw):
    """The RemoteWriteRequest of a body, or None (too long, not JSON, not the closed
    shape)."""
    if len(raw) > MAX_WRITE_BODY_BYTES:
        return None
    try:
        return RemoteWriteRequest.model_validate(json.loads(raw, parse_constant=_no_constant))
    except (ValueError, RecursionError, ValidationError):
        return None


def request_hash(resource, write):
    """SHA-256 hex of what a write asks (resource, object id or None, expected version,
    changes), in canonical JSON: the same request gives the same hash, whatever its key
    order."""
    return hashlib.sha256(canonical_json({
        'resource': resource,
        'object_id': str(write.object_id) if write.object_id else None,
        'expected_version': write.expected_version,
        'changes': write.changes})).hexdigest()


def _replay(stored, fields):
    """The stored answer of a write, with only `fields` (what the actor may read now)."""
    data = {name: value for name, value in stored['data'].items() if name in fields}
    return remote_record(stored['resource'], stored['region'], stored['object_id'], data,
                         version=stored.get('version'))


def remote_write(request, resource, load, update, build, assignee=None, version=None):
    """The answer to a remote write of `resource` (POST, the body a
    `RemoteWriteRequest`). `load(object_id)` gives the record or None (the request's
    ``object_id``, or None for the resource's default record); `assignee(record)` its
    assignee UUID (``assigned`` scope); `update(object_id, expected_version, changes)`
    the resource's service function (locks, checks the version, applies, version + 1;
    raises StaleVersion or RecordGone; returns the updated record); `build(record)` all
    its fields, JSON-ready; `version(record)` its version."""
    from .views import _not_the_admin_site

    refused = _not_the_admin_site(request)
    if refused is not None:
        return refused
    claims = request.interface_claims
    actor = interface.remote_actor(claims)
    who = {'actor_type': RemoteAccess.ADMIN, **_actor_values(actor, claims),
           **_source(request), 'operation': RemoteAccess.WRITE}
    object_id = None
    names = ()

    def refuse(reason, code=contract_errors.FORBIDDEN, body=None):
        access_log.record_refused(resource=resource, object_id=object_id, reason=reason,
                                  fields=names, **who)
        logger.info('remote write refused: %s %s (%s) by %s', resource, object_id or '-',
                    reason, who['actor_uuid'] or '?')
        if body is not None:
            return no_store(JsonResponse(body, status=contract_errors.REMOTE_WRITE_STATUS[
                code]))
        return write_error(code)

    # The actor, this region and the role first, before reading the body.
    early = access.decide(resource, actor, settings.REGION, assignee=None)
    if not early.granted and early.reason != access.REASON_ASSIGNED:
        return refuse(early.reason)
    write = _write_request(request.body)
    if write is None:
        return refuse(INVALID_BODY, INVALID_BODY)
    names = tuple(sorted(write.changes))
    if not set(names) <= access.writable(resource, actor.role):
        return refuse(access.REASON_FIELD)
    try:
        changes = changes_for(resource, write.changes)
    except (KeyError, ValidationError):
        return refuse(INVALID_BODY, INVALID_BODY)
    requested = str(write.object_id) if write.object_id else None
    object_id = requested
    record = load(requested)
    if record is None or (requested is not None and str(record.pk) != requested):
        return refuse(NOT_FOUND, NOT_FOUND)
    object_id = str(record.pk)
    decision = access.decide_write(resource, actor, settings.REGION, names,
                                   assignee=assignee(record) if assignee else None)
    if not decision.granted:
        return refuse(decision.reason)

    digest = request_hash(resource, write)
    key = {'actor_uuid': actor.sub, 'idempotency_key': write.idempotency_key}
    reused, stored = _stored(key, resource, digest)
    if reused:
        return refuse(KEY_REUSED, KEY_REUSED)
    if stored is not None:
        # A retry: logged (granted, reason `replay`) and committed before the stored
        # answer, which holds record data, leaves; no row, no answer.
        try:
            access_log.record_granted(resource=resource, object_id=stored.object_id,
                                      fields=decision.fields,
                                      access_reason=access_reason_for(REPLAY, names), **who)
        except access_log.AccessLogUnavailable:
            return write_error(contract_errors.ACCESS_LOG_UNAVAILABLE)
        return _replayed(stored, decision.fields, key)

    try:
        access_log.record_granted(resource=resource, object_id=object_id,
                                  fields=decision.fields,
                                  access_reason=access_reason_for(WRITE_REASON, names), **who)
    except access_log.AccessLogUnavailable:
        return write_error(contract_errors.ACCESS_LOG_UNAVAILABLE)

    try:
        body = _apply(key, resource, object_id, digest, write, changes, update, build,
                      version, decision.fields)
    except StaleVersion as stale:
        return refuse(STALE_VERSION, STALE_VERSION,
                      body=stale_version_body(stale.current_version))
    except RecordGone:
        return refuse(NOT_FOUND, NOT_FOUND)
    if body is None:
        # A concurrent request with the same key was applied first: answer as it did
        # (this request's granted row is already written).
        reused, stored = _stored(key, resource, digest)
        if reused:
            return refuse(KEY_REUSED, KEY_REUSED)
        if stored is None:
            raise RuntimeError('a remote write key was claimed but is not stored')
        return _replayed(stored, decision.fields, key)
    logger.info('remote write: %s %s by %s, now version %s', resource, object_id,
                actor.sub, body.get('version'))
    return no_store(JsonResponse(body))


def _stored(key, resource, digest):
    """(reused, stored): (False, None) when the key is new; (True, None) when it was used
    for another request (another hash: body or object id); else (False, the
    RemoteWrite)."""
    stored = RemoteWrite.objects.filter(**key).first()
    if stored is None:
        return False, None
    if stored.request_hash != digest or stored.resource != resource:
        return True, None
    return False, stored


def _replayed(stored, fields, key):
    logger.info('remote write replayed: %s %s by %s', stored.resource, stored.object_id,
                key['actor_uuid'])
    return no_store(JsonResponse(_replay(stored.response, fields), status=stored.status))


def _apply(key, resource, object_id, digest, write, changes, update, build, version,
           fields):
    """Claim the key, apply through the service function and store the answer, in one
    transaction. The answer's body, or None when another request holds the key."""
    with transaction.atomic():
        try:
            with transaction.atomic():
                claim = RemoteWrite.objects.create(
                    resource=resource, object_id=object_id, request_hash=digest, status=200,
                    response={}, **key)
        except IntegrityError:
            return None
        record = update(object_id, write.expected_version, changes)
        full = build(record)
        data = {name: full[name] for name in fields}
        body = remote_record(resource, settings.REGION, object_id, data,
                             version=version(record) if version else None)
        claim.response = body
        claim.save(update_fields=['response'])
    return body


def purge_writes(now=None):
    """Delete the remote write keys older than REMOTE_WRITE_RETENTION_DAYS (decision
    2026-11-09: 7 days; run by `purge_outbox`). Returns the number deleted."""
    cutoff = (now or timezone.now()) - timedelta(days=REMOTE_WRITE_RETENTION_DAYS)
    deleted, _ = RemoteWrite.objects.filter(created_at__lt=cutoff).delete()
    return deleted
