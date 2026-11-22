"""Small HTTP helpers for the external checks (breached passwords, disposable domains, the
signup CAPTCHA) and the geo-IP database download (regions update_geoip). Twilio Verify
(signup.twilio_verify) makes its own request but raises the same `FETCH_ERRORS`.

There is no `requests`: urllib with our own User-Agent and a socket timeout:
EXTERNAL_CHECK_TIMEOUT, except for `get_bytes`, which takes its own. That limit applies to
each socket operation (the connect, each read), not to the whole call, and the DNS lookup
has none. Callers catch `FETCH_ERRORS` and carry on without the check.
"""
import http.client
import json
import urllib.parse
import urllib.request

from django.conf import settings

# What a failed fetch can raise: URLError and HTTPError (4xx/5xx) are OSErrors, as are
# timeouts and resets; a broken response is an HTTPException; a bad body a ValueError.
FETCH_ERRORS = (OSError, http.client.HTTPException, ValueError)


class ResponseTooLarge(ValueError):
    """The body is longer than the caller's `max_bytes` (one of FETCH_ERRORS)."""


def _fetch_bytes(request, timeout=None, max_bytes=None):
    timeout = settings.EXTERNAL_CHECK_TIMEOUT if timeout is None else timeout
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError(f'unexpected HTTP status {response.status}')
        if max_bytes is None:
            return response.read()
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ResponseTooLarge(f'response larger than {max_bytes} bytes')
        return body


def _fetch_text(request, timeout=None):
    return _fetch_bytes(request, timeout=timeout).decode('utf-8')


def get_text(url, headers=None):
    """GET `url` and return the body as text; raises one of FETCH_ERRORS on any failure."""
    return _fetch_text(urllib.request.Request(url, headers={
        'User-Agent': settings.EXTERNAL_CHECK_USER_AGENT,
        **(headers or {}),
    }))


def get_bytes(url, timeout=None, max_bytes=None):
    """GET `url` and return the body as bytes, undecoded; `timeout` (seconds, default
    EXTERNAL_CHECK_TIMEOUT) is the socket timeout. With `max_bytes`, at most max_bytes + 1
    bytes are read and a longer body raises ResponseTooLarge. Raises one of FETCH_ERRORS
    on any failure; a 4xx/5xx answer is a urllib.error.HTTPError carrying its `code`."""
    return _fetch_bytes(urllib.request.Request(url, headers={
        'User-Agent': settings.EXTERNAL_CHECK_USER_AGENT,
    }), timeout=timeout, max_bytes=max_bytes)


def post_form(url, data):
    """POST `data` (a dict) form-encoded to `url` and return the body as text; raises one
    of FETCH_ERRORS on any failure."""
    return _fetch_text(urllib.request.Request(
        url, data=urllib.parse.urlencode(data).encode('ascii'), method='POST', headers={
            'User-Agent': settings.EXTERNAL_CHECK_USER_AGENT,
            'Content-Type': 'application/x-www-form-urlencoded',
        }))


def post_json(url, payload, timeout=None):
    """POST `payload` (a dict) as JSON to `url` and return the body as text; raises one of
    FETCH_ERRORS on any failure (an HTTP 4xx/5xx is an urllib.error.HTTPError). `timeout`
    (seconds, default EXTERNAL_CHECK_TIMEOUT) is the socket timeout."""
    return _fetch_text(urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'), method='POST', headers={
            'User-Agent': settings.EXTERNAL_CHECK_USER_AGENT,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }), timeout=timeout)


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # the 3xx then raises urllib.error.HTTPError


_NO_REDIRECT = urllib.request.build_opener(_RefuseRedirects)


def request_json(method, url, payload=None, headers=None, timeout=None, ok=(200, 201)):
    """Send `method` to `url` with an optional JSON `payload` and extra `headers` (the
    Revolut Merchant API: Bearer key, version header, GET/DELETE, 201); returns the body as
    text ('' when empty). `ok` are the accepted statuses; any other raises ValueError, and an
    HTTP 4xx/5xx is an urllib.error.HTTPError carrying its `code` (all FETCH_ERRORS).
    `timeout` (seconds, default EXTERNAL_CHECK_TIMEOUT) is the socket timeout."""
    request_headers = {
        'User-Agent': settings.EXTERNAL_CHECK_USER_AGENT,
        'Accept': 'application/json',
        **(headers or {}),
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        request_headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
    timeout = settings.EXTERNAL_CHECK_TIMEOUT if timeout is None else timeout
    # No redirect is followed: urllib would forward the Authorization header to the target.
    with _NO_REDIRECT.open(request, timeout=timeout) as response:
        if response.status not in ok:
            raise ValueError(f'unexpected HTTP status {response.status}')
        return response.read().decode('utf-8')
