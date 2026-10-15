"""Disposable email domains (signup-fields.md, step 1; registration-failure-policies.md:
"email disposable"), and the signup's system check (signup.E001, at the end).

Debounce's free API: `<DISPOSABLE_EMAIL_API_URL>?email=check@<domain>`. Only the domain
leaves the server, never the person's address. On a timeout, an error or an unexpected
body the email is accepted: signup is never blocked by the check being down (the caller
logs `check_unavailable`).
"""
import json
import logging
import urllib.parse

from django.conf import settings
from django.core.checks import Error, register

from accounts.http import FETCH_ERRORS, get_text

logger = logging.getLogger(__name__)

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


TWILIO_CREDENTIALS_MISSING = 'signup.E001'
TWILIO_PROVIDER = 'signup.twilio_verify.TwilioVerifyProvider'


@register('signup')
def check_twilio_credentials(app_configs, **kwargs):
    """Fail every `check` (and `migrate`) while Twilio Verify is the verification provider
    and one of its credentials is empty. Names the settings only, never a value."""
    if getattr(settings, 'VERIFICATION_PROVIDER', '') != TWILIO_PROVIDER:
        return []
    from .twilio_verify import CREDENTIAL_SETTINGS

    missing = [name for name in CREDENTIAL_SETTINGS
               if not str(getattr(settings, name, '') or '').strip()]
    if not missing:
        return []
    return [Error(
        f"Twilio Verify is the verification provider but {', '.join(missing)} "
        f"{'is' if len(missing) == 1 else 'are'} empty: no signup code can be sent.",
        hint='Type the Twilio credentials into settings.env on the server '
             '(deploy/README.md, "Twilio Verify").',
        id=TWILIO_CREDENTIALS_MISSING,
    )]
