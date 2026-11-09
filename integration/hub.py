"""Hub lookups (epic-admin-interface entry 7, AD-3 channel 4, AD-21, interface I10): live
reads of admin-owned data (specialist availability and slot holds, with
epic-appointments). When the admin site is down a lookup fails cleanly and nothing else
on this site is affected.

`lookup(path, reader)` signs ``GET <admin site><path>?region=<REGION>`` for the admin site
(`outbox.admin_site_url`: INTERFACE_ADMIN_SITE in INTERFACE_PEERS), waits at most
HUB_LOOKUP_TIMEOUT seconds per socket operation and `OVERALL_CAP` seconds in all, reads at
most mytp-core's `MAX_LOOKUP_BYTES` (64 KB), and returns the answer read with `reader`
(the lookup's closed model in `mytp_core.contracts.LOOKUP_READERS`). Nothing of it is
cached or stored: hub data is live, so each lookup asks again.

Every failure is one of two exceptions (`HubLookupFailed`), with a short code and no
detail, and no other exception leaves `lookup`:

- `HubUnavailable` (outcome ``unavailable``): no answer (``network``; also a peer that
  hangs without sending anything, after HUB_LOOKUP_TIMEOUT), the overall cap reached
  (``timeout``), every lookup slot taken by earlier lookups still running (``busy``, no
  call made), a 5xx or a 401 (``http_<status>[_<peer code>]``, as the outbox's
  `_unavailable`), an answer that is too large, not JSON or not the reader's model, or for
  another region (``bad_answer``), the interface not set up here (``not_configured``,
  ``signing_key``, ``peer_url`` for an admin URL `signed_get` refuses), or anything
  unexpected (``error``);
- `HubRefused` (outcome ``refused``): any other 4xx (``http_404_not_found``,
  ``http_403_region_mismatch``, ...), or a path outside ``/api/v1/hub/`` (``bad_path``,
  nothing sent).

Each lookup writes one log line (logger ``integration.hub``): INFO when answered, WARNING
with the outcome and code when not, never the answer. No Sentry event per failed lookup
(a warning is not one); a caller that needs an alert raises its own.

The overall cap: urllib's timeout applies to each socket operation, not to the whole call
(a peer answering one byte at a time could hold it far longer), so the call runs in a
daemon thread joined for at most `OVERALL_CAP` seconds; past it the lookup is ``timeout``
and the thread is left to finish, its result dropped. It ends when the peer stops
sending (one socket timeout later) or finishes; a slow-drip answer (a byte every few
seconds, under the socket timeout) keeps it running past its socket timeout, up to the
64 KB read limit. So such threads are bounded: at most `MAX_RUNNING` lookup threads run
at once in a process (`_slots`, a BoundedSemaphore released when the thread ends); with
none free a lookup is ``busy`` at once, without a thread or a call.

The first lookup in a process (a gunicorn worker) may unwrap this site's signing key
through the KMS (`interface.get_signing_key`, once per process) inside that thread: a
hung KMS then shows as ``timeout`` (or ``signing_key`` if the KMS refuses), and the next
lookup tries again.
"""
import logging
import re
import threading
from urllib.parse import urlencode

from django.conf import settings
from pydantic import ValidationError

from mytp_core import interface
from mytp_core.contracts import MAX_LOOKUP_BYTES
from mytp_core.contracts.errors import BAD_ANSWER, HUB_REFUSED, HUB_UNAVAILABLE
from mytp_core.contracts.hub import HUB_PREFIX

from .outbox import _error_code, _unavailable, admin_site_url

logger = logging.getLogger('integration.hub')

# Seconds a lookup may take in all (decision 2026-11-09), whatever HUB_LOOKUP_TIMEOUT.
OVERALL_CAP = 10
# Lookup threads running at once in a process (those past the cap included).
MAX_RUNNING = 4
_slots = threading.BoundedSemaphore(MAX_RUNNING)
BUSY = 'busy'
NOT_CONFIGURED = 'not_configured'
SIGNING_KEY = 'signing_key'
TIMEOUT = 'timeout'
ERROR = 'error'
BAD_PATH = 'bad_path'
_REGION_RE = re.compile(r'^[a-z]{2}$')
_PATH_RE = re.compile(r'^' + re.escape(HUB_PREFIX) + r'[a-z0-9][a-z0-9/-]*$')


class HubLookupFailed(Exception):
    """A lookup without an answer: `outcome` (``unavailable`` or ``refused``) and a short
    `code`; the text is ``<outcome> (<code>)``."""

    outcome = ''

    def __init__(self, code):
        self.code = code
        super().__init__(f'{self.outcome} ({code})')


class HubUnavailable(HubLookupFailed):
    """The admin site cannot answer now (no answer, timeout, 5xx, 401, a bad answer, the
    interface not set up here)."""

    outcome = HUB_UNAVAILABLE


class HubRefused(HubLookupFailed):
    """The admin site refused this lookup (another 4xx), or the path is not a lookup."""

    outcome = HUB_REFUSED


def lookup(path, reader):
    """The admin site's answer to the lookup at `path` (``/api/v1/hub/<name>``, no query),
    read with `reader` (a contract model class); HubUnavailable or HubRefused otherwise,
    never another exception. Nothing is cached or stored."""
    try:
        answer = _lookup(path, reader)
    except HubLookupFailed as exc:
        _log_failure(path, exc)
        raise
    except Exception:  # noqa: BLE001 - no exception may reach the caller
        exc = HubUnavailable(ERROR)
        _log_failure(path, exc)
        raise exc from None
    try:
        logger.info('hub lookup %s: answered', _shown(path))
    except Exception:  # noqa: BLE001, S110 - a log handler must not fail the lookup
        pass
    return answer


def _log_failure(path, exc):
    try:
        logger.warning('hub lookup %s: %s (%s)', _shown(path), exc.outcome, exc.code)
    except Exception:  # noqa: BLE001, S110 - a log handler must not fail the lookup
        pass


def _shown(path):
    return path[:100] if isinstance(path, str) and _PATH_RE.fullmatch(path) else '?'


def _lookup(path, reader):
    if not isinstance(path, str) or not _PATH_RE.fullmatch(path):
        raise HubRefused(BAD_PATH)
    try:
        site, base_url = admin_site_url()
    except interface.InterfaceError:
        raise HubUnavailable(NOT_CONFIGURED) from None
    region = getattr(settings, 'REGION', '') or ''
    if not _REGION_RE.fullmatch(region):
        raise HubUnavailable(NOT_CONFIGURED)
    url = f'{base_url}{path}?{urlencode({"region": region})}'
    timeout = settings.HUB_LOOKUP_TIMEOUT
    answer = _within_cap(lambda: interface.signed_get(
        url, audience=site, timeout=timeout, max_bytes=MAX_LOOKUP_BYTES))
    try:
        result = reader.model_validate(answer.data)
    except ValidationError:
        raise HubUnavailable(BAD_ANSWER) from None
    if getattr(result, 'region', region) != region:
        raise HubUnavailable(BAD_ANSWER)
    return result


def _within_cap(call):
    """`call()`'s result within OVERALL_CAP seconds, run in a thread holding one of the
    `_slots` until it ends; its failure as HubLookupFailed (``busy`` when no slot is
    free: no thread, no call)."""
    if not _slots.acquire(blocking=False):
        raise HubUnavailable(BUSY)
    outcome = {}

    def run():
        try:
            outcome['answer'] = call()
        except BaseException as exc:  # noqa: BLE001 - handed to the caller's thread
            outcome['error'] = exc
        finally:
            _slots.release()

    thread = threading.Thread(target=run, name='hub-lookup', daemon=True)
    try:
        thread.start()
    except BaseException:
        _slots.release()  # the thread never ran, so it cannot release its slot
        raise
    thread.join(OVERALL_CAP)
    if thread.is_alive():
        raise HubUnavailable(TIMEOUT)
    if 'answer' in outcome:
        return outcome['answer']
    error = outcome.get('error')
    if isinstance(error, interface.SigningKeyUnavailable):
        raise HubUnavailable(SIGNING_KEY)
    if isinstance(error, interface.PeerCallFailed):
        code = _error_code(error)[:64]
        if error.status is not None and 200 <= error.status < 300:
            raise HubUnavailable(BAD_ANSWER)
        if _unavailable(error):
            raise HubUnavailable(code)
        raise HubRefused(code)
    if isinstance(error, interface.InterfaceError):
        raise HubUnavailable(NOT_CONFIGURED)
    raise HubUnavailable(ERROR)
