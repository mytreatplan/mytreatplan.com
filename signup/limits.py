"""Our limits on sending verification codes (otp-verification.md), on top of the
provider's own code rules (signup.verification).

Per contact and purpose (signup or resume): a new send is allowed COOLDOWN_SECONDS after
the previous one, and at most HOURLY_SENDS sends (the first plus 3 resends) within
WINDOW_SECONDS. Only successful sends count (`record_send`); switching a mobile to SMS is
a send like any other. The times of the sends are kept in CACHES['default'] (the database
cache), keyed by the contact's blind index, never the plain address or number.
"""
import math
import time
from collections import namedtuple

from django.core.cache import cache

from .verification import EMAIL, MOBILE, contact_index

COOLDOWN_SECONDS = 60
HOURLY_SENDS = 4
WINDOW_SECONDS = 3600

# `allowed`; else `wait_seconds` until the next send is allowed, and whether the hourly
# limit (not the cooldown) is the reason.
SendCheck = namedtuple('SendCheck', 'allowed wait_seconds hourly_blocked')


def _now():
    return time.time()


def _kind(contact):
    return EMAIL if '@' in contact else MOBILE


def _key(contact, purpose):
    kind = _kind(contact)
    return f'signup:otp-sends:{kind}:{contact_index(kind, contact)}:{purpose}'


def _sends(contact, purpose, now):
    """The times of the successful sends within the last hour, oldest first."""
    times = cache.get(_key(contact, purpose)) or []
    return [t for t in times if now - t < WINDOW_SECONDS]


def check_send(contact, purpose):
    """Whether a code may be sent to `contact` (email or E.164 mobile) now."""
    now = _now()
    sends = _sends(contact, purpose, now)
    if len(sends) >= HOURLY_SENDS:
        wait = sends[-HOURLY_SENDS] + WINDOW_SECONDS - now
        return SendCheck(False, max(1, math.ceil(wait)), True)
    if sends and now - sends[-1] < COOLDOWN_SECONDS:
        return SendCheck(False, max(1, math.ceil(sends[-1] + COOLDOWN_SECONDS - now)), False)
    return SendCheck(True, 0, False)


def record_send(contact, purpose):
    """Count a successful send."""
    now = _now()
    sends = _sends(contact, purpose, now) + [now]
    cache.set(_key(contact, purpose), sends, timeout=WINDOW_SECONDS + 60)


# How long a send may hold its lock (it is released as soon as the send is done).
LOCK_SECONDS = 30


def _lock_key(contact, purpose):
    return _key(contact, purpose) + ':lock'


def acquire_send_lock(contact, purpose):
    """Take the per-contact-and-purpose send lock; False if another send holds it.
    `cache.add` is atomic in the database cache, so two parallel requests (a double
    click) cannot both pass `check_send` and send."""
    return cache.add(_lock_key(contact, purpose), 1, timeout=LOCK_SECONDS)


def release_send_lock(contact, purpose):
    cache.delete(_lock_key(contact, purpose))


def seconds_since_last_send(contact, purpose):
    """Seconds since the last successful send within the hour, or None."""
    now = _now()
    sends = _sends(contact, purpose, now)
    return now - sends[-1] if sends else None
