"""Breached-password check (signup-fields.md: "rejected if in a breached-password list").

Have I Been Pwned's range API, with k-anonymity: only the first 5 hex characters of the
password's SHA-1 leave the server, the request asks for padding (`Add-Padding: true`), and
the password is breached when the rest of its hash is in the response (with a count above
0: padding entries have count 0).

`BreachedPasswordValidator` is in AUTH_PASSWORD_VALIDATORS, so signup, `createsuperuser`
and later password changes all use it. If the API times out or fails, the password is
accepted (when the other validators pass): signup is never blocked by the check being
down. Code that wants to know (the signup event log) wraps the validation in
`collect_unavailable_checks()`.
"""
import contextlib
import contextvars
import hashlib
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from .http import FETCH_ERRORS, get_text

logger = logging.getLogger(__name__)

BREACHED_PASSWORD_CHECK = 'breached_password'

_unavailable = contextvars.ContextVar('unavailable_checks', default=None)


@contextlib.contextmanager
def collect_unavailable_checks():
    """Yield a list that gets the name of each check found unavailable inside the block."""
    found = []
    token = _unavailable.set(found)
    try:
        yield found
    finally:
        _unavailable.reset(token)


def note_unavailable(check):
    """Record that `check` could not run (for collect_unavailable_checks)."""
    found = _unavailable.get()
    if found is not None:
        found.append(check)


def fetch_pwned_range(prefix):
    """The HIBP range response for a 5-character hash prefix (raises FETCH_ERRORS)."""
    return get_text(settings.PWNED_PASSWORDS_API_URL + prefix, {'Add-Padding': 'true'})


def pwned_count(password):
    """How many times the password appears in HIBP (0: not breached), or None if the
    check could not be made. Neither the password nor its full hash leaves the server."""
    digest = hashlib.sha1(password.encode('utf-8')).hexdigest().upper()  # noqa: S324 - HIBP
    prefix, suffix = digest[:5], digest[5:]
    try:
        body = fetch_pwned_range(prefix)
    except FETCH_ERRORS as error:
        logger.warning('Breached-password check unavailable (%s)', type(error).__name__)
        return None
    entries = 0
    for line in body.splitlines():
        candidate, sep, count = line.strip().partition(':')
        if not sep:
            continue
        entries += 1
        if candidate.upper() == suffix:
            try:
                return int(count)
            except ValueError:
                return 1
    if not entries:
        logger.warning('Breached-password check unavailable (empty response)')
        return None
    return 0


class BreachedPasswordValidator:
    """Reject a password found in Have I Been Pwned's breached passwords."""

    def validate(self, password, user=None):
        count = pwned_count(password)
        if count is None:
            note_unavailable(BREACHED_PASSWORD_CHECK)
        elif count > 0:
            raise ValidationError(
                _('This password has appeared in a data breach, so it is not safe to use. '
                  'Please choose another one.'),
                code='password_breached',
            )

    def get_help_text(self):
        return _('Your password must not appear in a known data breach.')
