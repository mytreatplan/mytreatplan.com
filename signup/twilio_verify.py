"""Twilio Verify as the signup verification provider (signup entry 11, CAP-5, CAP-6, CAP-11).

The provider moved to mytp-core 0.4.0 (`mytp_core.twilio_verify`, epic-admin-access);
this module keeps the portal's names: TWILIO_PROVIDER (the dotted path live settings.env
files name), the subclass on the portal's logger, and the module-level `post_verify`
(tests patch it; the subclass calls it).

Selected with VERIFICATION_PROVIDER=signup.twilio_verify.TwilioVerifyProvider (the
constant TWILIO_PROVIDER). Twilio generates, stores, expires and counts the codes (6
digits, 10 minutes, 5 wrong attempts: the Verify defaults, with the service's code length
set to 6 in the console). Email goes through Verify's email channel (the SendGrid
integration and its dynamic template, set in the console), mobile through WhatsApp or SMS.
Our database never holds a code.

The REST API (`<TWILIO_VERIFY_BASE_URL>/Services/<sid>/Verifications` and
`/VerificationCheck`) is called with urllib (no SDK, no `requests`), Basic auth with an
API key (TWILIO_API_KEY_SID / TWILIO_API_KEY_SECRET), and EXTERNAL_CHECK_TIMEOUT as the
socket timeout. An unreachable Twilio raises one of mytp_core.verification.FETCH_ERRORS
(re-exported here; the same errors as accounts.http.FETCH_ERRORS), which the provider
turns into SendFailed / CheckFailed. TWILIO_ACCOUNT_SID names the account the key and the
service belong to; it is required (signup.E001) but not sent, as an API key already
identifies its account.

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
import logging

from mytp_core import twilio_verify as _core
from mytp_core.twilio_verify import (  # noqa: F401 - re-exported
    _CODE_RE,
    _MAX_BODY,
    CHANNEL_UNAVAILABLE_CODES,
    CREDENTIAL_SETTINGS,
    DEFAULT_BASE_URL,
    LOCALES,
    MAX_ATTEMPTS_CODE,
    NO_CODE_STATUSES,
    _detail,
    _error_code,
    _json,
    _locale,
)
from mytp_core.verification import FETCH_ERRORS  # noqa: F401 - re-exported

# The provider's log lines go with the fake's, on the logger Sentry ignores.
logger = logging.getLogger('signup.verification')

# The dotted path of this provider, for VERIFICATION_PROVIDER.
TWILIO_PROVIDER = 'signup.twilio_verify.TwilioVerifyProvider'


def post_verify(path, data):
    """POST `data` form-encoded to the Verify service's `path` ('Verifications' or
    'VerificationCheck'). Returns (HTTP status, decoded JSON object, {} if none) for any
    HTTP answer, error statuses included; raises one of accounts.http.FETCH_ERRORS when
    Twilio cannot be reached. The single HTTP call of this module (tests patch it)."""
    return _core.post_verify(path, data)


class TwilioVerifyProvider(_core.TwilioVerifyProvider):
    """Sends and checks the signup codes with Twilio Verify (see the module docstring)."""

    logger_name = 'signup.verification'

    def _post_verify(self, path, data):
        return post_verify(path, data)  # this module's, looked up at call time
