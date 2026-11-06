"""The interface endpoints the admin site calls (epic-admin-interface, AD-4): the ping
(entry 1) and the configuration snapshot's nudge (entry 4)."""
import logging

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from mytp_core import interface
from mytp_core.contracts import errors as contract_errors
from mytp_core.contracts import utc_text

from . import calls, config
from .calls import signed_endpoint
from .outbox import admin_site_url

logger = logging.getLogger('integration.config')


@require_GET
@signed_endpoint
def ping(request):
    """GET /api/v1/ping, signed by the admin site: this site's name, region and time (the
    contract's form, ISO 8601 UTC ending in "Z")."""
    return JsonResponse({'site': settings.SITE, 'region': settings.REGION,
                         'time': utc_text(timezone.now())})


@require_POST
@signed_endpoint
def config_nudge(request):
    """POST /api/v1/config/nudge, signed by the admin site: a new configuration snapshot
    is there. Answers 202 at once and enqueues a pull (`integration.config.request_pull`;
    repeated nudges coalesce into the one already queued). The body is ignored: the
    snapshot is always pulled, never pushed. Only the admin site may nudge: another signed
    peer is refused as an unknown key (401)."""
    claims = request.interface_claims
    try:
        admin, _url = admin_site_url()
    except interface.InterfaceError as exc:
        return calls._unavailable(request, exc)
    if claims['iss'] != admin:
        calls._outcome(request, False, contract_errors.UNKNOWN_KEY, claims['iss'],
                       claims.kid)
        return JsonResponse(interface.refusal_body(contract_errors.UNKNOWN_KEY), status=401)
    queued = config.request_pull()
    logger.info('config nudge from %s: %s', request.interface_claims['iss'],
                'pull queued' if queued else 'a pull is already queued')
    return JsonResponse({'status': 'queued' if queued else 'already_queued'}, status=202)
