"""The login failure counters (epic-login entry 4, CAP-3) and the lockout (entry 5).

Two counters per account: PASSWORD (wrong passwords) and CODE (wrong or no longer valid
second-factor codes). A failure for an email with no account counts under PASSWORD for
that email's blind index (accounts.crypto.email_index), never the plain email. Each count
lives in CACHES['default'] (the database cache, as signup.limits), one key per counter
and account (or email index), and runs out FAILURE_WINDOW_SECONDS after its last failure
(decision 2). A successful login resets both counts of its account (`reset`); a completed
password reset (entry 10) also lifts its lock (`unlock`).

Each count's read-then-write runs under a per-key lock (`cache.add`, atomic in the
database cache, as signup.limits), waited for up to a second, so two simultaneous
failures are both counted. When a count reaches LOCK_THRESHOLD, its subject (the account,
or the typed email's blind index) is locked for LOCK_SECONDS (`lock`, `is_locked`) and
that count starts again from 0, so after the lock a new run of LOCK_THRESHOLD is needed.

Only counts and times are kept: never a password, a code or a contact.
"""
import time
from collections import namedtuple

from django.core.cache import cache

from .crypto import email_index

PASSWORD = 'password'
CODE = 'code'
COUNTERS = (PASSWORD, CODE)

# A run of failures ends this long after its last failure (decision 2).
FAILURE_WINDOW_SECONDS = 24 * 60 * 60

# The lockout (entry 5): this many failures in a row of one counter lock for LOCK_SECONDS.
LOCK_THRESHOLD = 5
LOCK_SECONDS = 10 * 60

# The per-key lock covers only a read-then-write; a request that finds it held tries
# again every MUTEX_PAUSE seconds, MUTEX_TRIES times (a second), then gives up
# (CounterBusy, reported by the caller like any other counter error).
MUTEX_SECONDS = 5
MUTEX_PAUSE = 0.05
MUTEX_TRIES = 20


# What `record_failure` did: the count in the run, whether it set the lock, and the
# (action, error) pairs of the lock steps that failed, for the caller to report.
Failure = namedtuple('Failure', 'count locked errors')


class CounterBusy(Exception):
    """A counter's lock was not got within the wait."""


def _now():
    """The current time (epoch seconds); a function so tests can move it."""
    return time.time()


def _sleep(seconds):
    time.sleep(seconds)


def _subject(account_id=None, email=None):
    """What a count or a lock belongs to: the account, else the email's blind index."""
    if account_id is not None:
        return f'account:{account_id}'
    if email is None:
        raise ValueError('a login failure counts for an account or an email')
    return f'email:{email_index(email)}'


def _key(counter, account_id=None, email=None):
    if counter not in COUNTERS:
        raise ValueError(f'unknown login failure counter: {counter}')
    return f'login:failures:{counter}:{_subject(account_id, email)}'


def _lock_key(account_id=None, email=None):
    return f'login:lock:{_subject(account_id, email)}'


def _acquire(key):
    """The per-key mutex, waiting up to MUTEX_TRIES * MUTEX_PAUSE for it."""
    for attempt in range(MUTEX_TRIES + 1):
        if cache.add(key + ':mutex', 1, timeout=MUTEX_SECONDS):
            return
        if attempt < MUTEX_TRIES:
            _sleep(MUTEX_PAUSE)
    raise CounterBusy(key)


def _release(key):
    cache.delete(key + ':mutex')


def _current(key, now):
    """The stored count, or 0 when there is none or its run has expired."""
    entry = cache.get(key)
    if not isinstance(entry, dict) or now - entry.get('last', 0) >= FAILURE_WINDOW_SECONDS:
        return 0
    return entry.get('count', 0)


def _store(key, count, now):
    # The entry outlives the window a little; `_current` checks the time itself.
    cache.set(key, {'count': count, 'last': now}, timeout=FAILURE_WINDOW_SECONDS + 60)


def _clear(key):
    cache.delete(key)


def record_failure(counter, *, account_id=None, email=None):
    """Count one failure of `counter` for the account `account_id` (a user pk) or, with
    no account, for `email`. Returns a Failure: the count in the current run (1 for the
    first), and whether it set the lock. The count that reaches LOCK_THRESHOLD locks the
    account (or the email) and starts that count again from 0. Setting the lock and
    clearing (or, without the lock, storing) the count are independent steps: one that
    fails is returned in `errors`, and does not undo or skip the other."""
    key = _key(counter, account_id, email)
    _acquire(key)
    try:
        now = _now()
        count = _current(key, now) + 1
        if count < LOCK_THRESHOLD:
            _store(key, count, now)
            return Failure(count, False, ())
        errors = []
        locked = False
        try:
            lock(account_id=account_id, email=email)
            locked = True
        except Exception as error:  # noqa: BLE001 - reported by the caller
            errors.append(('lock', error))
        try:
            if locked:
                _clear(key)
            else:  # not locked: keep counting, so the next failure tries the lock again
                _store(key, count, now)
        except Exception as error:  # noqa: BLE001 - reported by the caller
            errors.append(('clear_count' if locked else 'store_count', error))
        return Failure(count, locked, tuple(errors))
    finally:
        _release(key)


def failure_count(counter, *, account_id=None, email=None):
    """The current count of `counter` for the account (or the email with no account);
    0 when there is no run going."""
    return _current(_key(counter, account_id, email), _now())


def reset(account_id):
    """A successful login: both counts of the account start again from 0."""
    cache.delete_many([_key(counter, account_id) for counter in COUNTERS])


def lock(*, account_id=None, email=None):
    """Lock the account (or the email with no account) for LOCK_SECONDS from now."""
    now = _now()
    cache.set(_lock_key(account_id, email), {'until': now + LOCK_SECONDS},
              timeout=LOCK_SECONDS + 60)


def unlock(*, account_id=None, email=None):
    """Lift the lock of the account (or of the email with no account) at once: a completed
    password reset (epic-login entry 10, decision 2)."""
    cache.delete(_lock_key(account_id, email))


def is_locked(*, account_id=None, email=None):
    """Whether the account, or the email (its blind index), is locked now. With both,
    either lock counts."""
    keys = []
    if account_id is not None:
        keys.append(_lock_key(account_id=account_id))
    if email is not None:
        keys.append(_lock_key(email=email))
    now = _now()
    for entry in cache.get_many(keys).values():
        if isinstance(entry, dict) and now < entry.get('until', 0):
            return True
    return False
