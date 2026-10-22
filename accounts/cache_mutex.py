"""A short-lived lock in CACHES['default'] (the database cache), around a read-then-write
of a counter there: the login failure counters (accounts.login_failures), the password
reset's per-email limit (accounts.password_reset) and the per-IP limits (signup.limits).

`cache.add` is atomic in the database cache, so only one request holds a lock at a time.
A request that finds it held tries again every `pause` seconds, `tries` times, then gives
up. Each caller keeps its own constants and its own `sleep` (a function so tests can
replace it)."""
from contextlib import contextmanager

from django.core.cache import cache


class CounterBusy(Exception):
    """A counter's lock was not got within the wait."""


def acquire(key, *, seconds, pause, tries, sleep):
    """Take the lock `key` (held at most `seconds`), waiting up to `tries` * `pause` for
    it; False if it was not got."""
    for attempt in range(tries + 1):
        if cache.add(key, 1, timeout=seconds):
            return True
        if attempt < tries:
            sleep(pause)
    return False


@contextmanager
def cache_mutex(key, *, seconds, pause, tries, sleep):
    """Hold the per-key mutex (`key` + ':mutex', see `acquire`) for the block, and release
    it after; CounterBusy(key) when it is not got within the wait."""
    if not acquire(key + ':mutex', seconds=seconds, pause=pause, tries=tries, sleep=sleep):
        raise CounterBusy(key)
    try:
        yield
    finally:
        cache.delete(key + ':mutex')
