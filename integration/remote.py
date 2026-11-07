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

Nothing of the record goes into a log line, a task or the cache.
"""
import logging

from django.conf import settings
from django.http import JsonResponse
from django.utils.cache import add_never_cache_headers

from eventlog.service import client_ip
from mytp_core import access, interface
from mytp_core.contracts import errors as contract_errors
from mytp_core.contracts import remote_record

from . import access_log
from .models import RemoteAccess

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


def remote_read(request, resource, object_id, load, build, assignee=None):
    """The answer to a remote read of `resource`. `load(object_id)` gives the record or
    None (`object_id` as asked, maybe None; the log then names the record's pk);
    `assignee(record)` its assignee UUID (for the ``assigned`` scope; None when the
    resource has none); `build(record)` the dict of all its fields (JSON-ready), called
    only after the access is logged."""
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
    return no_store(JsonResponse(remote_record(resource, settings.REGION, object_id, data)))
