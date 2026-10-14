"""Small HTTP helpers for the external checks (breached passwords, disposable domains, the
signup CAPTCHA).

There is no `requests`: urllib with our own User-Agent and EXTERNAL_CHECK_TIMEOUT as the
socket timeout. That limit applies to each socket operation (the connect, each read), not
to the whole call, and the DNS lookup has none. Callers catch `FETCH_ERRORS` and carry on
without the check.
"""
import http.client
import urllib.parse
import urllib.request

from django.conf import settings

# What a failed fetch can raise: URLError and HTTPError (4xx/5xx) are OSErrors, as are
# timeouts and resets; a broken response is an HTTPException; a bad body a ValueError.
FETCH_ERRORS = (OSError, http.client.HTTPException, ValueError)


def _fetch_text(request):
    with urllib.request.urlopen(request, timeout=settings.EXTERNAL_CHECK_TIMEOUT) as response:
        if response.status != 200:
            raise ValueError(f'unexpected HTTP status {response.status}')
        return response.read().decode('utf-8')


def get_text(url, headers=None):
    """GET `url` and return the body as text; raises one of FETCH_ERRORS on any failure."""
    return _fetch_text(urllib.request.Request(url, headers={
        'User-Agent': settings.EXTERNAL_CHECK_USER_AGENT,
        **(headers or {}),
    }))


def post_form(url, data):
    """POST `data` (a dict) form-encoded to `url` and return the body as text; raises one
    of FETCH_ERRORS on any failure."""
    return _fetch_text(urllib.request.Request(
        url, data=urllib.parse.urlencode(data).encode('ascii'), method='POST', headers={
            'User-Agent': settings.EXTERNAL_CHECK_USER_AGENT,
            'Content-Type': 'application/x-www-form-urlencoded',
        }))
