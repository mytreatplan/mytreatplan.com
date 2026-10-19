"""Twilio Verify as the signup verification provider (signup entry 11, CAP-5, CAP-6, CAP-11).

Selected with VERIFICATION_PROVIDER=signup.twilio_verify.TwilioVerifyProvider (the
constant TWILIO_PROVIDER). Twilio generates, stores, expires and counts the codes (6
digits, 10 minutes, 5 wrong attempts: the Verify defaults, with the service's code length
set to 6 in the console). Email goes through Verify's email channel (the SendGrid
integration and its dynamic template, set in the console), mobile through WhatsApp or SMS.
Our database never holds a code.

The REST API (`<TWILIO_VERIFY_BASE_URL>/Services/<sid>/Verifications` and
`/VerificationCheck`) is called with urllib (no SDK, no `requests`), Basic auth with an
API key (TWILIO_API_KEY_SID / TWILIO_API_KEY_SECRET), and EXTERNAL_CHECK_TIMEOUT as the
socket timeout. An unreachable Twilio raises one of accounts.http.FETCH_ERRORS, which the
provider turns into SendFailed / CheckFailed. TWILIO_ACCOUNT_SID names the account the key
and the service belong to; it is required (signup.E001) but not sent, as an API key
already identifies its account.

One Verify service serves every purpose (signup, resume and login): codes for different
purposes to the same contact share Twilio's pending verification and its 5-attempt count.
A resume code and a signup code to the same email are interchangeable (either proves
control of the email); so are a login code and a pending signup or resume code to the
same contact, and wrong attempts at one count against the other. Accepted (epic-login
entry 2, decision 1): every purpose proves control of the same contact. `purpose` only
reaches our limits and logs.

Errors carry only Twilio's error code and the HTTP status: never the contact, the code or
the credentials. This module logs on `signup.verification`, which Sentry ignores; failures
reach Sentry through the callers' `signup.steps` / `signup.forms` errors.
"""
import base64
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from accounts.http import FETCH_ERRORS

from .verification import (APPROVED, CODE_LENGTH, NO_CODE, WHATSAPP, WRONG,
                           ChannelUnavailable, CheckFailed, SendFailed, VerificationProvider,
                           contact_kind)

# The provider's log lines go with the fake's, on the logger Sentry ignores.
logger = logging.getLogger('signup.verification')

# The dotted path of this provider, for VERIFICATION_PROVIDER.
TWILIO_PROVIDER = 'signup.twilio_verify.TwilioVerifyProvider'

DEFAULT_BASE_URL = 'https://verify.twilio.com/v2'
# The four credentials; signup.E001 names any left empty while this provider is selected.
CREDENTIAL_SETTINGS = ('TWILIO_ACCOUNT_SID', 'TWILIO_API_KEY_SID', 'TWILIO_API_KEY_SECRET',
                       'TWILIO_VERIFY_SERVICE_SID')

# Twilio error codes that mean "this channel cannot deliver to this contact": the caller
# falls back to SMS from WhatsApp. 60205: SMS is not supported by a landline; 60223: the
# delivery channel is disabled on the service (e.g. WhatsApp before the sender exists);
# 63003: the channel could not find the To address (no WhatsApp on the number).
CHANNEL_UNAVAILABLE_CODES = {60205, 60223, 63003}
# VerificationCheck: max check attempts reached (the code is invalidated).
MAX_ATTEMPTS_CODE = 60202
# VerificationCheck statuses meaning there is no valid code any more.
NO_CODE_STATUSES = {'canceled', 'expired', 'max_attempts_reached', 'deleted', 'failed'}
LOCALES = ('en', 'es')

# ASCII digits only (\d would take other scripts' digits too).
_CODE_RE = re.compile(rf'[0-9]{{{CODE_LENGTH}}}')
# Bodies are small JSON objects; never read more than this.
_MAX_BODY = 65536


def _json(raw):
    try:
        parsed = json.loads(raw or b'{}')
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def post_verify(path, data):
    """POST `data` form-encoded to the Verify service's `path` ('Verifications' or
    'VerificationCheck'). Returns (HTTP status, decoded JSON object, {} if none) for any
    HTTP answer, error statuses included; raises one of accounts.http.FETCH_ERRORS when
    Twilio cannot be reached. The single HTTP call of this module (tests patch it)."""
    base = (settings.TWILIO_VERIFY_BASE_URL or DEFAULT_BASE_URL).rstrip('/')
    service = urllib.parse.quote(settings.TWILIO_VERIFY_SERVICE_SID, safe='')
    credentials = f'{settings.TWILIO_API_KEY_SID}:{settings.TWILIO_API_KEY_SECRET}'
    request = urllib.request.Request(
        f'{base}/Services/{service}/{path}',
        data=urllib.parse.urlencode(data).encode('utf-8'), method='POST', headers={
            'User-Agent': settings.EXTERNAL_CHECK_USER_AGENT,
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
            'Authorization': 'Basic ' + base64.b64encode(credentials.encode()).decode('ascii'),
        })
    try:
        with urllib.request.urlopen(request, timeout=settings.EXTERNAL_CHECK_TIMEOUT) as response:
            return response.status, _json(response.read(_MAX_BODY))
    except urllib.error.HTTPError as exc:
        try:
            body = _json(exc.read(_MAX_BODY))
        except FETCH_ERRORS:
            body = {}
        finally:
            exc.close()
        return exc.code, body


def _error_code(body):
    code = body.get('code')
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    if isinstance(code, str) and code.isdigit():
        return int(code)
    return None


def _detail(status, body):
    """Only Twilio's error code and the HTTP status: nothing else of the answer."""
    code = _error_code(body)
    return f'code {code if code is not None else "none"}, HTTP {status}'


def _locale(language):
    language = (language or '').split('-')[0].lower()
    return language if language in LOCALES else 'en'


class TwilioVerifyProvider(VerificationProvider):
    """Sends and checks the signup codes with Twilio Verify (see the module docstring)."""

    def send(self, channel, contact, purpose, language):
        contact_kind(channel)  # ValueError for an unknown channel
        if channel == WHATSAPP and not settings.TWILIO_WHATSAPP_ENABLED:
            # Until the WhatsApp sender is approved: the caller sends by SMS instead.
            logger.info('Twilio Verify send skipped: channel=whatsapp purpose=%s '
                        '(TWILIO_WHATSAPP_ENABLED is off)', purpose)
            raise ChannelUnavailable('twilio: WhatsApp is not enabled')
        data = {'To': contact, 'Channel': channel, 'Locale': _locale(language)}
        try:
            status, body = post_verify('Verifications', data)
        except FETCH_ERRORS as error:
            logger.warning('Twilio Verify send failed: channel=%s purpose=%s error=%s',
                           channel, purpose, type(error).__name__)
            raise SendFailed(f'twilio: send failed ({type(error).__name__})') from None
        if 200 <= status < 300:
            logger.info('Twilio Verify send: channel=%s purpose=%s', channel, purpose)
            return
        detail = _detail(status, body)
        if _error_code(body) in CHANNEL_UNAVAILABLE_CODES:
            logger.info('Twilio Verify channel unavailable: channel=%s purpose=%s (%s)',
                        channel, purpose, detail)
            raise ChannelUnavailable(f'twilio: channel unavailable ({detail})')
        logger.warning('Twilio Verify send refused: channel=%s purpose=%s (%s)',
                       channel, purpose, detail)
        raise SendFailed(f'twilio: send refused ({detail})')

    def check(self, channel, contact, purpose, code):
        contact_kind(channel)
        code = (code or '').strip()
        if not _CODE_RE.fullmatch(code):
            return WRONG  # malformed: not worth a call, and not counted by Twilio
        try:
            status, body = post_verify('VerificationCheck', {'To': contact, 'Code': code})
        except FETCH_ERRORS as error:
            logger.warning('Twilio Verify check failed: channel=%s purpose=%s error=%s',
                           channel, purpose, type(error).__name__)
            raise CheckFailed(f'twilio: check failed ({type(error).__name__})') from None
        if 200 <= status < 300:
            result = body.get('status')
            if result == 'approved':
                return APPROVED
            if result == 'pending':
                return WRONG
            if result in NO_CODE_STATUSES:
                return NO_CODE
            logger.warning('Twilio Verify check: unexpected answer: channel=%s purpose=%s',
                           channel, purpose)
            raise CheckFailed(f'twilio: check failed (unexpected answer, HTTP {status})')
        # 404: no pending verification (expired, used, or invalidated after 5 wrong
        # attempts); 60202: max check attempts reached.
        if status == 404 or _error_code(body) == MAX_ATTEMPTS_CODE:
            return NO_CODE
        detail = _detail(status, body)
        logger.warning('Twilio Verify check refused: channel=%s purpose=%s (%s)',
                       channel, purpose, detail)
        raise CheckFailed(f'twilio: check failed ({detail})')

