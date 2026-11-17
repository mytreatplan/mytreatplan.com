"""The EU VIES check of a VAT number (epic-doctor-profile entry 6): the Commission's REST
API (`POST .../check-vat-number`, JSON). `fetch_vies(country, number)` answers VALID,
INVALID or UNAVAILABLE and never raises; an unavailable service (a timeout, a network error,
an HTTP 5xx or 429, a member state's outage or concurrency limit, an answer that cannot be
read) is UNAVAILABLE, never INVALID. A VIES answer never refuses a save.

An answer of `INVALID_INPUT` (HTTP 200, no `valid` key: VIES rejected the number) is INVALID.
Any other failure, an HTTP 4xx other than 429 included, is UNAVAILABLE and retried, so a wrong
`VIES_URL` shows up as the 3-day pending alert (accounts.fiscal.retry_due).

Never logs or raises the number: only the exception's type.
"""
import json
import logging

from django.conf import settings

from .http import FETCH_ERRORS, post_json

logger = logging.getLogger(__name__)

VALID, INVALID, UNAVAILABLE = 'valid', 'invalid', 'unavailable'

# The error codes VIES uses when it cannot answer (its own or the member state's).
UNAVAILABLE_CODES = frozenset({
    'MS_UNAVAILABLE', 'SERVICE_UNAVAILABLE', 'MS_MAX_CONCURRENT_REQ',
    'GLOBAL_MAX_CONCURRENT_REQ', 'TIMEOUT'})


def post_check(payload):
    """VIES's answer for `payload`, decoded JSON (raises FETCH_ERRORS)."""
    return json.loads(post_json(settings.VIES_URL, payload))


def _codes(answer):
    found = set()
    for key in ('userError', 'error'):
        if isinstance(answer.get(key), str):
            found.add(answer[key])
    wrappers = answer.get('errorWrappers')
    if isinstance(wrappers, list):
        found.update(item['error'] for item in wrappers
                     if isinstance(item, dict) and isinstance(item.get('error'), str))
    return found


def fetch_vies(country, number):
    """VALID, INVALID or UNAVAILABLE for the VAT `number` (without prefix) of VIES country
    code `country` (Greece: EL)."""
    try:
        answer = post_check({'countryCode': country, 'vatNumber': number})
    except FETCH_ERRORS as error:
        logger.warning('VIES check unavailable (%s)', type(error).__name__)
        return UNAVAILABLE
    if not isinstance(answer, dict):
        logger.warning('VIES check unavailable (unexpected response)')
        return UNAVAILABLE
    codes = _codes(answer)
    if codes & UNAVAILABLE_CODES:
        logger.warning('VIES check unavailable (service error)')
        return UNAVAILABLE
    if 'INVALID_INPUT' in codes and 'valid' not in answer:
        return INVALID
    valid = answer.get('valid')
    if valid is True:
        return VALID
    if valid is False:
        return INVALID
    logger.warning('VIES check unavailable (unexpected response)')
    return UNAVAILABLE
