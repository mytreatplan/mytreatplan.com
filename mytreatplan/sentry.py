"""Sentry error reporting (story 1.9).

Initialised from settings only when SENTRY_DSN is set, so local development and CI
send nothing. Events never carry personal data: the SDK is told not to collect it
(`send_default_pii=False`, no request bodies, no local variables) and `scrub_event`
removes whatever request and user details an integration still attaches.
"""
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

import sentry_sdk

# Request keys that may hold personal data (form fields, cookies, headers, query
# strings, the client IP in `env`). Only the method and the URL path are kept.
_REQUEST_KEYS_KEPT = {'method', 'url'}
# User keys kept: the numeric id only (no email, username or IP).
_USER_KEYS_KEPT = {'id'}


# Loggers whose records carry verification codes (signup.verification.FakeVerificationProvider);
# the Twilio provider (signup.twilio_verify) logs there too, and its failures reach Sentry
# through the callers' `signup.steps` errors instead.
CODE_LOGGERS = ('signup.verification',)

REDACTED = '[redacted]'
_EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}')
# Candidates only; each is confirmed with `ipaddress`, so times like 03:30:00 stay.
_IP_CANDIDATE_RE = re.compile(r'(?<![\w:.])[0-9A-Fa-f:.]*[.:][0-9A-Fa-f:.]*(?![\w:])')
# Phone numbers: 9 to 15 digits (E.164's longest), either after a `+`, with spaces,
# hyphens or brackets between them (`+34 622 918 374`, `+1 (415) 555-2671`), or all
# together (`622918374`). Shorter numbers (prices, IDs, HTTP statuses), groups without a
# `+` (`200 301 404`), and digits inside a word, a decimal or an identifier stay.
_PHONE_RE = re.compile(r'(?<![\w+])\+\d(?:[ \-()]{0,2}\d){8,14}(?!\w)'
                       r'|(?<![\w.+-])\d{9,15}(?!\w|\.\d)')


def _redact_ip(match):
    text = match.group(0)
    core = text.rstrip('.')
    try:
        ipaddress.ip_address(core)
    except ValueError:
        return text
    return REDACTED + text[len(core):]


def redact(text):
    """Replace email addresses, IPv4/IPv6 addresses and phone numbers in free text."""
    if not isinstance(text, str):
        return text
    text = _IP_CANDIDATE_RE.sub(_redact_ip, _EMAIL_RE.sub(REDACTED, text))
    return _PHONE_RE.sub(REDACTED, text)


def _strip_query(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


def scrub_event(event, hint=None):
    """`before_send`: drop request data, cookies, headers, query strings, user email
    and IP from an event, and redact emails, IPs and phone numbers in messages, exception
    values and breadcrumbs, before it leaves the server."""
    request = event.get('request')
    if isinstance(request, dict):
        scrubbed = {k: v for k, v in request.items() if k in _REQUEST_KEYS_KEPT}
        if isinstance(scrubbed.get('url'), str):
            scrubbed['url'] = _strip_query(scrubbed['url'])
        event['request'] = scrubbed

    user = event.get('user')
    if isinstance(user, dict):
        kept = {k: v for k, v in user.items() if k in _USER_KEYS_KEPT}
        if kept:
            event['user'] = kept
        else:
            event.pop('user', None)

    # Free text: exception values (e.g. a database error's DETAIL line with an email),
    # log messages and their arguments.
    for exc in (event.get('exception') or {}).get('values', []) or []:
        if isinstance(exc, dict) and 'value' in exc:
            exc['value'] = redact(exc['value'])
    logentry = event.get('logentry')
    if isinstance(logentry, dict):
        logentry.pop('params', None)
        for key in ('message', 'formatted'):
            if key in logentry:
                logentry[key] = redact(logentry[key])
    if isinstance(event.get('message'), str):
        event['message'] = redact(event['message'])

    # HTTP breadcrumbs (outgoing requests) can carry query strings too.
    for crumb in (event.get('breadcrumbs') or {}).get('values', []) or []:
        if isinstance(crumb, dict) and 'message' in crumb:
            crumb['message'] = redact(crumb['message'])
        data = crumb.get('data') if isinstance(crumb, dict) else None
        if isinstance(data, dict):
            data.pop('http.query', None)
            data.pop('http.fragment', None)
            if isinstance(data.get('url'), str):
                data['url'] = _strip_query(data['url'])
    return event


def init_sentry(dsn, environment, release=None):
    """Turn on Sentry. Called from settings only when SENTRY_DSN is set."""
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import ignore_logger, ignore_logger_for_sentry_logs

    # The verification providers log on these loggers, the fake with the signup codes;
    # those records stay on the server (no breadcrumbs, events or Sentry Logs).
    for logger_name in CODE_LOGGERS:
        ignore_logger(logger_name)
        ignore_logger_for_sentry_logs(logger_name)

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release or None,
        integrations=[DjangoIntegration()],
        send_default_pii=False,
        max_request_body_size='never',
        include_local_variables=False,
        before_send=scrub_event,
        # Errors only; no performance tracing.
        traces_sample_rate=0,
    )
