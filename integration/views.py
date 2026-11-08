"""The interface endpoints the admin site calls (epic-admin-interface, AD-4): the ping
(entry 1), the configuration snapshot's nudge (entry 4), the client re-sync (entry 8),
the contact message re-sync (entry 9) and the remote reads (entry 5, integration.remote)."""
import json
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from mytp_core import interface
from mytp_core.contracts import errors as contract_errors
from mytp_core.contracts import error_body, utc_text
from mytp_core.contracts.events import UUID_RE
from mytp_publicsite.models import ContactMessage

from . import calls, config, remote, sample
from .calls import signed_endpoint
from .outbox import admin_site_url

logger = logging.getLogger('integration.config')
clients_logger = logging.getLogger('integration.clients')
contact_messages_logger = logging.getLogger('integration.contact_messages')


@require_GET
@signed_endpoint
def ping(request):
    """GET /api/v1/ping, signed by the admin site: this site's name, region and time (the
    contract's form, ISO 8601 UTC ending in "Z")."""
    return JsonResponse({'site': settings.SITE, 'region': settings.REGION,
                         'time': utc_text(timezone.now())})


def _not_the_admin_site(request):
    """None when the request is signed by the admin site; else the response: 503 when the
    admin site cannot be told, 401 (an unknown key) for another signed peer."""
    claims = request.interface_claims
    try:
        admin, _url = admin_site_url()
    except interface.InterfaceError as exc:
        return calls._unavailable(request, exc)
    if claims['iss'] != admin:
        calls._outcome(request, False, contract_errors.UNKNOWN_KEY, claims['iss'],
                       claims.kid)
        return JsonResponse(interface.refusal_body(contract_errors.UNKNOWN_KEY), status=401)
    return None


@require_POST
@signed_endpoint
def config_nudge(request):
    """POST /api/v1/config/nudge, signed by the admin site: a new configuration snapshot
    is there. Answers 202 at once and enqueues a pull (`integration.config.request_pull`;
    repeated nudges coalesce into the one already queued). The body is ignored: the
    snapshot is always pulled, never pushed. Only the admin site may nudge: another signed
    peer is refused as an unknown key (401)."""
    refused = _not_the_admin_site(request)
    if refused is not None:
        return refused
    queued = config.request_pull()
    logger.info('config nudge from %s: %s', request.interface_claims['iss'],
                'pull queued' if queued else 'a pull is already queued')
    return JsonResponse({'status': 'queued' if queued else 'already_queued'}, status=202)


UNKNOWN_CLIENT = 'unknown_client'
UNKNOWN_CONTACT_MESSAGE = 'unknown_contact_message'
INVALID_BODY = 'invalid_body'


def _resync_uid(request):
    """The uid of a re-sync body `{"aggregate_id": "<uuid>"}` (no other key), or None."""
    try:
        data = json.loads(request.body)
    except (ValueError, RecursionError):
        data = None
    uid = data.get('aggregate_id') if isinstance(data, dict) and set(data) == {
        'aggregate_id'} else None
    return uid if isinstance(uid, str) and UUID_RE.fullmatch(uid) else None


@require_POST
@signed_endpoint
def clients_resync(request):
    """POST /api/v1/clients/resync, signed by the admin site (`resync_client`), body
    `{"aggregate_id": "<uid>"}`: emit that account's current state again
    (accounts.client_sync: `client.updated`, or `client.erased` for an erased account) as
    its next version. Answers 202 `{"status": "queued"}` and enqueues the task
    (`integration.tasks.resync_client_task`); 404 `unknown_client` for a uid that is no
    account here or a staff or erased account never sent; 400 `invalid_body` for any
    other body (extra keys included).
    Only the admin site may ask: another signed peer is refused as an unknown key
    (401)."""
    refused = _not_the_admin_site(request)
    if refused is not None:
        return refused
    try:
        data = json.loads(request.body)
    except (ValueError, RecursionError):
        data = None
    uid = data.get('aggregate_id') if isinstance(data, dict) and set(data) == {
        'aggregate_id'} else None
    if not (isinstance(uid, str) and UUID_RE.fullmatch(uid)):
        return JsonResponse(error_body(INVALID_BODY, 'The body is not {"aggregate_id": '
                                                     '"<uuid>"}.'), status=400)
    user = (get_user_model().objects.filter(uid=uid)
            .values('is_staff', 'is_superuser', 'sync_version', 'erased_at').first())
    issuer = request.interface_claims['iss']
    # Never sent and nothing to send (staff, or erased): unknown to the admin site.
    if user is None or (user['sync_version'] == 0 and (
            user['erased_at'] is not None or user['is_staff'] or user['is_superuser'])):
        clients_logger.info('client resync from %s: %s unknown', issuer, uid)
        return JsonResponse(error_body(UNKNOWN_CLIENT, 'No client has this id here.'),
                            status=404)
    from .tasks import resync_client_task
    resync_client_task.enqueue(uid)
    clients_logger.info('client resync from %s: %s queued', issuer, uid)
    return JsonResponse({'status': 'queued'}, status=202)


@require_POST
@signed_endpoint
def contact_messages_resync(request):
    """POST /api/v1/contact-messages/resync, signed by the admin site
    (`resync_contact_message`), body `{"aggregate_id": "<uid>"}`: emit that contact
    message's current state again (mytp_publicsite.contact_sync: `contact_message.updated`)
    as its next version. Answers 202 `{"status": "queued"}` and enqueues the task
    (`integration.tasks.resync_contact_message_task`); 404 `unknown_contact_message` for a
    uid that is no message here (never one, or deleted); 400 `invalid_body` for any other
    body (extra keys included). Only the admin site may ask: another signed peer is
    refused as an unknown key (401)."""
    refused = _not_the_admin_site(request)
    if refused is not None:
        return refused
    uid = _resync_uid(request)
    if uid is None:
        return JsonResponse(error_body(INVALID_BODY, 'The body is not {"aggregate_id": '
                                                     '"<uuid>"}.'), status=400)
    issuer = request.interface_claims['iss']
    if not ContactMessage.objects.filter(uid=uid).exists():
        contact_messages_logger.info('contact message resync from %s: %s unknown', issuer,
                                     uid)
        return JsonResponse(error_body(UNKNOWN_CONTACT_MESSAGE,
                                       'No contact message has this id here.'), status=404)
    from .tasks import resync_contact_message_task
    resync_contact_message_task.enqueue(uid)
    contact_messages_logger.info('contact message resync from %s: %s queued', issuer, uid)
    return JsonResponse({'status': 'queued'}, status=202)


@require_GET
@signed_endpoint
def remote_sample_record(request):
    """GET /api/v1/remote/sample-record, signed by the admin site with the admin user's
    access (staging only, INTERFACE_SAMPLE_RECORDS): the newest sample record, with the
    fields the user's role sees (integration.remote). Another signed peer is refused (401
    `unknown_key`) first, whatever the setting; then 404 `not_found` when the setting
    is off."""
    refused = _not_the_admin_site(request)
    if refused is not None:
        return refused
    if not sample.enabled():
        return remote.error(contract_errors.NOT_FOUND)
    return remote.remote_read(request, sample.RESOURCE, None, sample.load, sample.build,
                              assignee=sample.assignee)
