"""Minimal client for the OVHcloud KMS (OKMS) REST data plane (story 1.15).

    https://<region>.okms.ovh.net/api/<okmsId>/v1/servicekey/<keyId>[/datakey[/decrypt]]

The server authenticates with an access certificate (mutual TLS): KMS_CLIENT_CERT and
KMS_CLIENT_KEY are PEM files in /srv/mytp-<site>/shared/ (mode 600), KMS_CA_FILE optionally
replaces the system CAs. Only the standard library is used (urllib, ssl, json); the field
key provider accounts.crypto.KMSKeyProvider (story 2.12) builds on it.

Errors are `KMSError`s naming the step that failed (settings, connect, auth, get,
generate, unwrap) and, for HTTP errors, the status. Their text never contains a data
key (wrapped or plain) or credential material, and the original exception is never
chained, so Sentry gets only that text.
"""
import base64
import binascii
import errno
import http.client
import json
import os
import re
import socket
import ssl
import stat
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

REQUIRED_SETTINGS = (
    'KMS_ENDPOINT',
    'KMS_OKMS_ID',
    'KMS_SERVICE_KEY_ID',
    'KMS_CLIENT_CERT',
    'KMS_CLIENT_KEY',
)
TIMEOUT_SECONDS = 30
# Ids are UUIDs; anything else (a slash, a dot) would change the request path.
_ID_RE = re.compile(r'^[A-Za-z0-9-]{1,64}$')
# KMS response fields echoed in messages (state, error codes, request ids) must look
# like plain identifiers; anything else is replaced.
_SAFE_RE = re.compile(r'^[\w.:-]{1,80}$')
_MONTHS = {m: i for i, m in enumerate(
    ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'), 1)}


class KMSError(Exception):
    """A KMS call failed. `step` is one of settings, cert, connect, auth, get,
    generate, unwrap; `status` the HTTP status, if any."""

    def __init__(self, step, detail, status=None):
        self.step = step
        self.status = status
        self.detail = detail
        super().__init__(f'step {step} failed: {detail}')


def safe_text(value):
    text = str(value)
    return text if _SAFE_RE.match(text) else '?'


def _no_password():
    # An encrypted private key would otherwise make OpenSSL prompt on the terminal,
    # hanging a scheduled run. The key file is stored unencrypted (mode 600).
    return b''


class KMSClient:
    def __init__(self, endpoint, okms_id, cert_file, key_file, ca_file=None,
                 timeout=TIMEOUT_SECONDS):
        self.base_url = f'{endpoint.rstrip("/")}/api/{okms_id}/v1'
        self.cert_file = cert_file
        self.key_file = key_file
        self.ca_file = ca_file or None
        self.timeout = timeout
        self._opener = None

    @classmethod
    def from_settings(cls, settings=None):
        """A client for the KMS_* settings, after checking them. Raises
        KMSError('settings', ...) naming the first missing or invalid key."""
        if settings is None:
            from django.conf import settings
        check_settings(settings)
        return cls(settings.KMS_ENDPOINT.strip(), settings.KMS_OKMS_ID.strip(),
                   settings.KMS_CLIENT_CERT.strip(), settings.KMS_CLIENT_KEY.strip(),
                   (getattr(settings, 'KMS_CA_FILE', '') or '').strip() or None)

    # --- operations ----------------------------------------------------------------------

    def get_service_key(self, key_id):
        """The service key's metadata (id, name, type, state, ...)."""
        return self._request('GET', f'/servicekey/{_check_id(key_id)}', None, 'get')

    def generate_data_key(self, key_id, name, size=256):
        """A new data key: (wrapped key as returned by the KMS, plaintext bytes)."""
        data = self._request('POST', f'/servicekey/{_check_id(key_id)}/datakey',
                             {'name': name, 'size': size}, 'generate')
        wrapped = data.get('key')
        if not isinstance(wrapped, str) or not wrapped:
            raise KMSError('generate', 'unexpected response: no wrapped key')
        return wrapped, _b64('generate', data.get('plaintext'))

    def decrypt_data_key(self, key_id, wrapped):
        """The plaintext bytes of a data key wrapped by this service key."""
        data = self._request('POST', f'/servicekey/{_check_id(key_id)}/datakey/decrypt',
                             {'key': wrapped}, 'unwrap')
        return _b64('unwrap', data.get('plaintext'))

    # --- HTTP ------------------------------------------------------------------------------

    def _ssl_context(self):
        try:
            context = ssl.create_default_context(cafile=self.ca_file)
        except (OSError, ssl.SSLError) as exc:
            raise KMSError('connect', f'could not load KMS_CA_FILE ({_os_error(exc)})') from None
        try:
            context.load_cert_chain(self.cert_file, self.key_file, password=_no_password)
        except (OSError, ssl.SSLError) as exc:
            raise KMSError(
                'auth', f'could not load the client certificate or key ({_os_error(exc)})'
            ) from None
        return context

    def _open(self, request):
        if self._opener is None:
            self._opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=self._ssl_context()))
        return self._opener.open(request, timeout=self.timeout)

    def _request(self, method, path, body, step):
        data = None
        headers = {'Accept': 'application/json'}
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers,
                                         method=method)
        try:
            with self._open(request) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            detail = _http_detail(exc)
            exc.close()
            if status in (401, 403):
                raise KMSError('auth', f'HTTP {status} ({step}){detail}', status) from None
            raise KMSError(step, f'HTTP {status}{detail}', status) from None
        except urllib.error.URLError as exc:
            raise KMSError(_network_step(exc.reason), _reason(exc.reason)) from None
        except (OSError, http.client.HTTPException) as exc:
            raise KMSError(_network_step(exc), _reason(exc)) from None
        try:
            parsed = json.loads(raw)
        except ValueError:
            raise KMSError(step, 'unexpected response: not JSON') from None
        if not isinstance(parsed, dict):
            raise KMSError(step, 'unexpected response: not a JSON object')
        return parsed


def _check_id(value):
    if not _ID_RE.match(str(value)):
        raise KMSError('settings', 'the service key id must be a UUID')
    return value


def _b64(step, value):
    if not isinstance(value, str) or not value:
        raise KMSError(step, 'unexpected response: no plaintext')
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise KMSError(step, 'unexpected response: plaintext is not base64') from None


def _http_detail(exc):
    """Only the KMS's error code and request id (for OVHcloud support), never the body."""
    try:
        body = json.loads(exc.read(4096) or b'{}')
    except Exception:
        return ''
    if not isinstance(body, dict):
        return ''
    parts = []
    for key in ('error_code', 'request_id'):
        if body.get(key) not in (None, ''):
            parts.append(f'{key} {safe_text(body[key])}')
    return f': {", ".join(parts)}' if parts else ''


def _network_step(reason):
    """'auth' when the KMS refused our certificate in the TLS handshake (a TLS alert
    such as UNKNOWN_CA, BAD_CERTIFICATE, CERTIFICATE_EXPIRED, CERTIFICATE_REQUIRED,
    HANDSHAKE_FAILURE or ACCESS_DENIED),
    else 'connect'."""
    code = str(getattr(reason, 'reason', '') or '') if isinstance(reason, ssl.SSLError) else ''
    if 'ALERT' in code and any(word in code for word in (
            'CERTIFICATE', 'UNKNOWN_CA', 'HANDSHAKE_FAILURE', 'ACCESS_DENIED')):
        return 'auth'
    return 'connect'


def _reason(reason):
    """A description of a network/TLS failure: exception type, and the SSL reason or
    errno name, without free text."""
    if isinstance(reason, ssl.SSLError):
        return f'TLS error {safe_text(getattr(reason, "reason", None) or type(reason).__name__)}'
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return 'timed out'
    if isinstance(reason, socket.gaierror):
        return 'could not resolve the KMS host'
    if isinstance(reason, OSError):
        return _os_error(reason)
    return type(reason).__name__ if isinstance(reason, BaseException) else safe_text(reason)


def _os_error(exc):
    if isinstance(exc, ssl.SSLError):
        return f'{type(exc).__name__} {safe_text(getattr(exc, "reason", None) or "")}'
    if isinstance(exc, OSError) and exc.errno:
        return f'{type(exc).__name__} {errno.errorcode.get(exc.errno, exc.errno)}'
    return type(exc).__name__


# --- settings and credential checks --------------------------------------------------------

def check_settings(settings):
    """Raise KMSError('settings', ...) naming the first missing or invalid KMS_* key."""
    for key in REQUIRED_SETTINGS:
        if not str(getattr(settings, key, '') or '').strip():
            raise KMSError('settings', f'setting {key} is missing (shared/settings.env)')
    parts = urlsplit(settings.KMS_ENDPOINT.strip())
    if parts.scheme != 'https' or not parts.hostname or parts.path not in ('', '/') \
            or parts.query or parts.fragment or parts.username or parts.password:
        raise KMSError('settings', 'KMS_ENDPOINT must be https://<region>.okms.ovh.net')
    for key in ('KMS_OKMS_ID', 'KMS_SERVICE_KEY_ID'):
        if not _ID_RE.match(getattr(settings, key).strip()):
            raise KMSError('settings', f'{key} must be a UUID')
    files = [('KMS_CLIENT_CERT', False), ('KMS_CLIENT_KEY', True)]
    if (getattr(settings, 'KMS_CA_FILE', '') or '').strip():
        files.append(('KMS_CA_FILE', False))
    for key, private in files:
        path = getattr(settings, key).strip()
        try:
            info = os.stat(path)
        except OSError:
            raise KMSError('settings', f'{key} file {path} not found or not readable') from None
        if not stat.S_ISREG(info.st_mode):
            raise KMSError('settings', f'{key} {path} is not a file')
        if not os.access(path, os.R_OK):
            raise KMSError('settings', f'{key} file {path} is not readable by this user')
        if private and info.st_mode & 0o077:
            raise KMSError('settings', f'{key} file {path} must be mode 600 (owner only)')


def certificate_expiry(cert_file):
    """The certificate's notAfter as an aware UTC datetime, read with `openssl x509`
    (no Python dependency)."""
    try:
        result = subprocess.run(
            ['openssl', 'x509', '-noout', '-enddate', '-in', str(cert_file)],
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise KMSError('cert', 'openssl not found (install the openssl package)') from None
    except (OSError, subprocess.SubprocessError) as exc:
        raise KMSError('cert', f'could not run openssl ({type(exc).__name__})') from None
    line = result.stdout.strip()
    if result.returncode != 0 or not line.startswith('notAfter='):
        raise KMSError('cert', f'could not read the expiry of {cert_file} (openssl exit '
                               f'{result.returncode})')
    return parse_openssl_date(line.split('=', 1)[1])


def parse_openssl_date(text):
    """`Oct 12 10:00:00 2027 GMT` (as printed by openssl, day may be space-padded)."""
    fields = text.split()
    try:
        month, day, clock, year, zone = fields
        hour, minute, second = (int(x) for x in clock.split(':'))
        if zone != 'GMT':
            raise ValueError(zone)
        return datetime(int(year), _MONTHS[month], int(day), hour, minute, second,
                        tzinfo=timezone.utc)
    except (ValueError, KeyError):
        raise KMSError('cert', f'unexpected certificate date {safe_text(text)!r}') from None
