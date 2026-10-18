"""Disposable email domains (signup-fields.md, step 1; registration-failure-policies.md:
"email disposable").

Debounce's free API: `<DISPOSABLE_EMAIL_API_URL>?email=check@<domain>`. Only the domain
leaves the server, never the person's address. On a timeout, an error or an unexpected
body the email is accepted: signup is never blocked by the check being down (the caller
logs `check_unavailable`).
"""
import json
import logging
import urllib.parse

from django.conf import settings

from accounts.http import FETCH_ERRORS, get_text

# The logger keeps the name it had in signup.checks, where this code lived, so the log
# lines (and their Sentry events) are unchanged.
logger = logging.getLogger('signup.checks')

DISPOSABLE_EMAIL_CHECK = 'disposable_email'


def fetch_disposable(domain):
    """Debounce's answer for `domain`, decoded JSON (raises FETCH_ERRORS)."""
    query = urllib.parse.urlencode({'email': f'check@{domain}'})
    return json.loads(get_text(f'{settings.DISPOSABLE_EMAIL_API_URL}?{query}'))


def is_disposable_domain(domain):
    """True if Debounce says the domain is disposable, False if not, None if unknown."""
    try:
        answer = fetch_disposable(domain)
    except FETCH_ERRORS as error:
        logger.warning('Disposable-email check unavailable (%s)', type(error).__name__)
        return None
    value = answer.get('disposable') if isinstance(answer, dict) else None
    if isinstance(value, str):
        value = value.strip().lower()
    if value in (True, 'true'):
        return True
    if value in (False, 'false'):
        return False
    logger.warning('Disposable-email check unavailable (unexpected response)')
    return None


def email_domain(email):
    return (email or '').rpartition('@')[2].strip().lower()
