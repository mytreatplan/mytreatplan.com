"""The CAPTCHA of signup step 1: Cloudflare Turnstile (signup entry 9, CAP-11).

The widget (static/js/signup-account.js) puts a single-use token in the step 1 form's
`cf-turnstile-response` field; `verify_turnstile` checks it with Cloudflare's siteverify
(TURNSTILE_VERIFY_URL), sending the token, TURNSTILE_SECRET_KEY and the client IP. Neither
the token nor the secret is ever logged.

When siteverify cannot be reached (a timeout, an HTTP or network error, a body that is
not its JSON) or answers that it could not decide (its own internal error, or our secret
refused: a configuration fault), the answer is UNAVAILABLE: the step goes through and the
caller logs `check_unavailable`, as for the other external checks (signup.disposable).
"""
import json
import logging

from django.conf import settings

from accounts.http import FETCH_ERRORS, post_form

logger = logging.getLogger(__name__)

PASSED, FAILED, UNAVAILABLE = 'passed', 'failed', 'unavailable'

# The form field the widget fills in.
TOKEN_FIELD = 'cf-turnstile-response'
# The check's name in `check_unavailable` events.
TURNSTILE_CHECK = 'turnstile'
# Cloudflare's tokens are at most 2048 characters.
MAX_TOKEN_LENGTH = 2048
# siteverify error codes that say nothing about the person: Cloudflare's own failure, or
# our secret (a configuration fault, reported as an error so Sentry alerts on it).
_SERVICE_ERRORS = {'internal-error'}
_SECRET_ERRORS = {'missing-input-secret', 'invalid-input-secret'}


def post_siteverify(data):
    """siteverify's answer for `data`, decoded JSON (raises FETCH_ERRORS)."""
    return json.loads(post_form(settings.TURNSTILE_VERIFY_URL, data))


def verify_turnstile(token, ip):
    """PASSED, FAILED or UNAVAILABLE for the widget's `token` sent from `ip`. A missing or
    oversized token fails without asking Cloudflare."""
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return FAILED
    data = {'secret': settings.TURNSTILE_SECRET_KEY, 'response': token}
    if ip:
        data['remoteip'] = ip
    try:
        answer = post_siteverify(data)
    except FETCH_ERRORS as error:
        logger.warning('Turnstile check unavailable (%s)', type(error).__name__)
        return UNAVAILABLE
    if not isinstance(answer, dict):
        logger.warning('Turnstile check unavailable (unexpected response)')
        return UNAVAILABLE
    if answer.get('success') is True:
        return PASSED
    codes = answer.get('error-codes')
    codes = {code for code in codes if isinstance(code, str)} if isinstance(codes, list) else set()
    if codes & _SECRET_ERRORS:
        logger.error('Turnstile secret refused by siteverify: check TURNSTILE_SECRET_KEY')
        return UNAVAILABLE
    if codes & _SERVICE_ERRORS:
        logger.warning('Turnstile check unavailable (internal-error)')
        return UNAVAILABLE
    return FAILED
