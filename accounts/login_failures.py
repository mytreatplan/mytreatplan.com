"""The login failure counters (epic-login entry 4, CAP-3) and the lockout (entry 5).

The code lives in `mytp_core.lockout` (epic-admin-access entry 3), with the portal's
policy (`mytp_core.policy.PORTAL`: 5 failures within 24 hours lock for 10 minutes, no
notifier: accounts.login_guard records `account_locked` itself). This module keeps the
portal's names, cache keys and module-level functions; the core class's seams (`_now`,
`_sleep`, `_key`, `_clear`, `lock`, `is_locked`) go through this module's functions, so
what a test patches here applies.

Two counters per account: PASSWORD (wrong passwords) and CODE (wrong or no longer valid
second-factor codes). A failure for an email with no account counts under PASSWORD for
that email's blind index (accounts.crypto.email_index), never the plain email. Each count
lives in CACHES['default'] (the database cache, as signup.limits), one key per counter
and account (or email index), and runs out FAILURE_WINDOW_SECONDS after its last failure
(decision 2). A successful login resets both counts of its account (`reset`); a completed
password reset (entry 10) also lifts its lock (`unlock`).

Each count's read-then-write runs under a per-key lock (accounts.cache_mutex, as
signup.limits), waited for up to a second, so two simultaneous
failures are both counted. When a count reaches LOCK_THRESHOLD, its subject (the account,
or the typed email's blind index) is locked for LOCK_SECONDS (`lock`, `is_locked`) and
that count starts again from 0, so after the lock a new run of LOCK_THRESHOLD is needed.

Only counts and times are kept: never a password, a code or a contact.
"""
import time

from mytp_core.lockout import CODE, COUNTERS, PASSWORD, Failure, LoginFailures  # noqa: F401
from mytp_core.policy import PORTAL

from .crypto import email_index  # noqa: F401

POLICY = PORTAL

# A run of failures ends this long after its last failure (decision 2).
FAILURE_WINDOW_SECONDS = POLICY.failure_window_seconds

# The lockout (entry 5): this many failures in a row of one counter lock for LOCK_SECONDS.
LOCK_THRESHOLD = POLICY.lock_threshold
LOCK_SECONDS = POLICY.lock_seconds

# The per-key lock covers only a read-then-write; a request that finds it held tries
# again every MUTEX_PAUSE seconds, MUTEX_TRIES times (a second), then gives up
# (accounts.cache_mutex.CounterBusy, reported by the caller like any other counter error).
MUTEX_SECONDS = 5
MUTEX_PAUSE = 0.05
MUTEX_TRIES = 20


class PortalLoginFailures(LoginFailures):
    """The core class with the portal's keys and constants; its seams go through this
    module's functions."""
    failures_prefix = 'login:failures'
    lock_prefix = 'login:lock'
    mutex_seconds = MUTEX_SECONDS
    mutex_pause = MUTEX_PAUSE
    mutex_tries = MUTEX_TRIES

    def _now(self):
        return _now()

    def _sleep(self, seconds):
        _sleep(seconds)

    def _key(self, counter, account_id=None, email=None):
        return _key(counter, account_id, email)

    def _clear(self, key):
        _clear(key)

    def lock(self, *, account_id=None, email=None):
        lock(account_id=account_id, email=email)

    def is_locked(self, *, account_id=None, email=None):
        return is_locked(account_id=account_id, email=email)


_failures = PortalLoginFailures(POLICY)


def _now():
    """The current time (epoch seconds); a function so tests can move it."""
    return time.time()


def _sleep(seconds):
    time.sleep(seconds)


def _subject(account_id=None, email=None):
    """What a count or a lock belongs to: the account, else the email's blind index."""
    return LoginFailures._subject(account_id, email)


def _key(counter, account_id=None, email=None):
    return LoginFailures._key(_failures, counter, account_id, email)


def _lock_key(account_id=None, email=None):
    return _failures._lock_key(account_id, email)


def _current(key, now):
    """The stored count, or 0 when there is none or its run has expired."""
    return _failures._current(key, now)


def _store(key, count, now):
    _failures._store(key, count, now)


def _clear(key):
    LoginFailures._clear(_failures, key)


def record_failure(counter, *, account_id=None, email=None):
    """Count one failure of `counter` for the account `account_id` (a user pk) or, with
    no account, for `email`. Returns a Failure: the count in the current run (1 for the
    first), and whether it set the lock. The count that reaches LOCK_THRESHOLD locks the
    account (or the email) and starts that count again from 0. Setting the lock and
    clearing (or, without the lock, storing) the count are independent steps: one that
    fails is returned in `errors`, and does not undo or skip the other."""
    return _failures.record_failure(counter, account_id=account_id, email=email)


def failure_count(counter, *, account_id=None, email=None):
    """The current count of `counter` for the account (or the email with no account);
    0 when there is no run going. Only the tests read it (the login reads the lock,
    `is_locked`, never the count)."""
    return _failures.failure_count(counter, account_id=account_id, email=email)


def reset(account_id):
    """A successful login: both counts of the account start again from 0."""
    _failures.reset(account_id)


def lock(*, account_id=None, email=None):
    """Lock the account (or the email with no account) for LOCK_SECONDS from now."""
    LoginFailures.lock(_failures, account_id=account_id, email=email)


def unlock(*, account_id=None, email=None):
    """Lift the lock of the account (or of the email with no account) at once: a completed
    password reset (epic-login entry 10, decision 2)."""
    _failures.unlock(account_id=account_id, email=email)


def is_locked(*, account_id=None, email=None):
    """Whether the account, or the email (its blind index), is locked now. With both,
    either lock counts."""
    return LoginFailures.is_locked(_failures, account_id=account_id, email=email)
