"""Signed endpoints and the log of every call to /api/ (epic-admin-interface entry 1, AD-4).

`signed_endpoint(view)` checks the request's MyTP-Signature (mytp_core.interface) against
the admin site's public keys (INTERFACE_PEER_KEYS) before the view runs: a refusal is a
401 with `{"error": {"code": ..., "message": ...}}` and nothing else; a site without a
signing key, or with malformed interface settings, answers 503 (`unavailable`).

`InterfaceCallMiddleware` writes an InterfaceCall for every request to /api/, whatever its
outcome, and logs a warning line for each refused one (the reason, the claimed issuer and
key id, the method, path and status; never the token). A failed write never breaks the
response.
"""
import logging
import re
from functools import wraps

from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from eventlog.service import client_ip
from mytp_core import interface

from .models import InterfaceCall

logger = logging.getLogger('integration.calls')

API_PREFIX = '/api/'
UNAVAILABLE = 'unavailable'
NOT_CHECKED = 'not_checked'
# Anything else failed while checking or answering (the cache, a too large body, a library
# error): logged with this reason, then re-raised (Django answers 4xx/5xx).
ERROR = 'error'
_SAFE = re.compile(r'^[\w.:-]{1,64}$')


def _safe(value):
    """A claimed iss/kid as logged and stored: plain identifiers only."""
    text = str(value or '')
    return text if _SAFE.match(text) else ('?' if text else '')


def keyring():
    """The admin site's public keys (INTERFACE_PEER_KEYS)."""
    return interface.Keyring.from_settings()


def _outcome(request, accepted, reason, iss='', kid=''):
    request.interface_outcome = {'accepted': accepted, 'reason': reason,
                                 'iss': _safe(iss), 'kid': _safe(kid)}


def _unavailable(request, detail):
    # One warning, and the response marked as logged, so django.request does not log the
    # 503 as an error (a Sentry event for every scanner hit before the keys are set).
    logger.warning('interface unavailable: %s', detail)
    _outcome(request, False, UNAVAILABLE)
    response = JsonResponse({'error': {'code': UNAVAILABLE, 'message':
                                       'The interface is not configured on this site.'}},
                            status=503)
    response._has_been_logged = True
    return response


def signed_endpoint(view):
    """The view runs only for a request signed by a known peer for this site; the
    verified claims are in `request.interface_claims`."""
    @csrf_exempt
    @never_cache
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return _checked(request, *args, **kwargs)
        except Exception:
            _outcome(request, False, ERROR)
            raise

    def _checked(request, *args, **kwargs):
        try:
            interface.signing_key_setting()
            ring = keyring()
        except interface.InterfaceError as exc:
            return _unavailable(request, exc)
        try:
            claims = interface.verify_django_request(request, ring)
        except interface.VerificationFailed as exc:
            _outcome(request, False, exc.code, exc.iss, exc.kid)
            return JsonResponse(interface.refusal_body(exc.code), status=401)
        _outcome(request, True, '', claims['iss'], claims.kid)
        request.interface_claims = claims
        return view(request, *args, **kwargs)
    return wrapped


def record_call(request, response):
    """Write the request's InterfaceCall; log a warning line when it was refused."""
    outcome = getattr(request, 'interface_outcome', None) or {
        'accepted': False, 'reason': NOT_CHECKED, 'iss': '', 'kid': ''}
    path = request.path[:512]
    if not outcome['accepted']:
        logger.warning('interface call refused: %s (iss %s, kid %s) %s %r -> %s',
                       outcome['reason'], outcome['iss'] or '-', outcome['kid'] or '-',
                       request.method, path[:200], response.status_code)
    else:
        logger.info('interface call accepted: iss %s, kid %s %s %r -> %s', outcome['iss'],
                    outcome['kid'], request.method, path[:200], response.status_code)
    try:
        InterfaceCall.objects.create(
            iss=outcome['iss'], kid=outcome['kid'], method=request.method[:10], path=path,
            accepted=outcome['accepted'], reason=outcome['reason'],
            status=response.status_code, ip=client_ip(request)[:64])
    except Exception as error:  # noqa: BLE001 - logging must never break the response
        logger.error('interface call log write failed (%s)', type(error).__name__)


class InterfaceCallMiddleware:
    """Logs every request to /api/ (an InterfaceCall row each)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(API_PREFIX):
            return self.get_response(request)
        response = self.get_response(request)
        record_call(request, response)
        return response
