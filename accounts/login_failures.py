"""The login failure counters (epic-login entry 4, CAP-3; read by the lockout, entry 5).

Two counters per account: PASSWORD (wrong passwords) and CODE (wrong or no longer valid
second-factor codes). A failure for an email with no account counts under PASSWORD for
that email's blind index (accounts.crypto.email_index), never the plain email. Each count
lives in CACHES['default'] (the database cache, as signup.limits), one key per counter
and account (or email index), and runs out FAILURE_WINDOW_SECONDS after its last failure
(decision 2). A successful login resets both counts of its account (`reset`).

Only counts and times are kept: never a password, a code or a contact.
"""
import time

from django.core.cache import cache

from .crypto import email_index

PASSWORD = 'password'
CODE = 'code'
COUNTERS = (PASSWORD, CODE)

# A run of failures ends this long after its last failure (decision 2).
FAILURE_WINDOW_SECONDS = 24 * 60 * 60


def _now():
    """The current time (epoch seconds); a function so tests can move it."""
    return time.time()


def _key(counter, account_id=None, email=None):
    if counter not in COUNTERS:
        raise ValueError(f'unknown login failure counter: {counter}')
    if account_id is not None:
        return f'login:failures:{counter}:account:{account_id}'
    if email is None:
        raise ValueError('a login failure counts for an account or an email')
    return f'login:failures:{counter}:email:{email_index(email)}'


def _current(key, now):
    """The stored count, or 0 when there is none or its run has expired."""
    entry = cache.get(key)
    if not isinstance(entry, dict) or now - entry.get('last', 0) >= FAILURE_WINDOW_SECONDS:
        return 0
    return entry.get('count', 0)


def record_failure(counter, *, account_id=None, email=None):
    """Count one failure of `counter` for the account `account_id` (a user pk) or, with
    no account, for `email`; returns the count in the current run (1 for the first)."""
    now = _now()
    key = _key(counter, account_id, email)
    count = _current(key, now) + 1
    # The cache entry outlives the window a little; `_current` checks the time itself.
    cache.set(key, {'count': count, 'last': now}, timeout=FAILURE_WINDOW_SECONDS + 60)
    return count


def failure_count(counter, *, account_id=None, email=None):
    """The current count of `counter` for the account (or the email with no account);
    0 when there is no run going. For the lockout (entry 5)."""
    return _current(_key(counter, account_id, email), _now())


def reset(account_id):
    """A successful login: both counts of the account start again from 0."""
    cache.delete_many([_key(counter, account_id) for counter in COUNTERS])
