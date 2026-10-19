"""Our limits on sending verification codes (otp-verification.md), on top of the
provider's own code rules (signup.verification).

Per contact and purpose (signup, resume or login): a new send is allowed COOLDOWN_SECONDS
after the previous one, and at most HOURLY_SENDS sends (the first plus 3 resends) within
WINDOW_SECONDS. Only successful sends count (`record_send`); switching a mobile to SMS is
a send like any other. The times of the sends are kept in CACHES['default'] (the database
cache), keyed by the contact's blind index, never the plain address or number.

Plus the per-IP limits (signup entry 9, CAP-11), against one source cycling through
contacts: signup starts, live email checks and code sends (every purpose, successful
sends only), each at most SIGNUP_IP_LIMITS[counter] per client IP
(eventlog.service.client_ip) in fixed one-hour windows. A send must pass both the contact
and the IP limits. The counters are in the same cache, keyed by an HMAC of the IP with
SECRET_KEY, never the plain IP; an IPv6 address counts as its /64 network (one client
usually holds a whole /64). A request reserves its count first (`ip_reserve`: the
read-check-increment under a per-IP-and-counter lock held for milliseconds, waited for
up to a second), so parallel requests cannot overshoot, and gives it back if its action
then fails (`ip_refund`). A block is logged once per IP, counter and window
(`first_block`).
"""
import hashlib
import hmac
import ipaddress
import math
import time
from collections import namedtuple

from django.conf import settings
from django.core.cache import cache

from eventlog.service import client_ip

from .verification import EMAIL, MOBILE, contact_index

COOLDOWN_SECONDS = 60
HOURLY_SENDS = 4
WINDOW_SECONDS = 3600

# `allowed`; else `wait_seconds` until the next send is allowed, and whether the hourly
# limit (not the cooldown) is the reason.
SendCheck = namedtuple('SendCheck', 'allowed wait_seconds hourly_blocked')


def _now():
    return time.time()


def _lock(key, timeout):
    """Take the lock `key` for at most `timeout` seconds; False if it is held. `cache.add`
    is atomic in the database cache."""
    return cache.add(key, 1, timeout=timeout)


def _unlock(key):
    cache.delete(key)


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
    return _lock(_lock_key(contact, purpose), LOCK_SECONDS)


def release_send_lock(contact, purpose):
    _unlock(_lock_key(contact, purpose))


def seconds_since_last_send(contact, purpose):
    """Seconds since the last successful send within the hour, or None."""
    now = _now()
    sends = _sends(contact, purpose, now)
    return now - sends[-1] if sends else None


# The per-IP counters (the keys of settings.SIGNUP_IP_LIMITS).
SIGNUP_STARTS = 'signup_starts'
EMAIL_CHECKS = 'email_checks'
CODE_SENDS = 'code_sends'
IP_COUNTERS = (SIGNUP_STARTS, EMAIL_CHECKS, CODE_SENDS)
IP_WINDOW_SECONDS = 3600


def _ip_subject(ip):
    """What is counted for `ip`: an IPv4 address itself, an IPv6 address its /64
    network; a value that is not an address, as is."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if address.version == 6:
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f'{address}/64', strict=False))
    return str(address)


def _ip_hash(ip):
    """HMAC-SHA256 of the IP (its /64 for IPv6) with SECRET_KEY, truncated: the key
    never holds the IP."""
    digest = hmac.new(settings.SECRET_KEY.encode(), _ip_subject(ip).encode(),
                      hashlib.sha256)
    return digest.hexdigest()[:32]


def _ip_base(request, counter):
    return f'signup:ip:{counter}:{_ip_hash(client_ip(request))}'


def _ip_key(request, counter):
    window = int(_now() // IP_WINDOW_SECONDS)
    return f'{_ip_base(request, counter)}:{window}'


# A count's key lives a little over its window: the key names its window, so a later
# one never reads it.
IP_COUNT_TIMEOUT = IP_WINDOW_SECONDS + 60
# The IP lock covers only a read-check-increment; a request that finds it held tries
# again every IP_LOCK_PAUSE seconds, IP_LOCK_TRIES times (a second), then is blocked.
IP_LOCK_SECONDS = 5
IP_LOCK_PAUSE = 0.05
IP_LOCK_TRIES = 20


def _sleep(seconds):
    time.sleep(seconds)


def acquire_ip_lock(request, counter):
    """Take the per-IP-and-counter lock (one try); False if another request holds it."""
    return _lock(_ip_base(request, counter) + ':lock', IP_LOCK_SECONDS)


def release_ip_lock(request, counter):
    _unlock(_ip_base(request, counter) + ':lock')


def _wait_ip_lock(request, counter):
    """The lock, waiting up to IP_LOCK_TRIES * IP_LOCK_PAUSE for it; False if not got."""
    for attempt in range(IP_LOCK_TRIES + 1):
        if acquire_ip_lock(request, counter):
            return True
        if attempt < IP_LOCK_TRIES:
            _sleep(IP_LOCK_PAUSE)
    return False


def ip_reserve(request, counter):
    """Count one `counter` for the request's IP in this hour's window, if under its limit.
    Returns the reservation (for `ip_refund`), or None if blocked: over the limit, or
    the lock not got within the wait."""
    if not _wait_ip_lock(request, counter):
        return None
    try:
        key = _ip_key(request, counter)
        count = cache.get(key) or 0
        if count >= settings.SIGNUP_IP_LIMITS[counter]:
            return None
        cache.set(key, count + 1, timeout=IP_COUNT_TIMEOUT)
        return key
    finally:
        release_ip_lock(request, counter)


def ip_refund(request, counter, reservation):
    """Give back a reservation whose action failed (in the window it was made in). Best
    effort: without the lock within the wait, the count stays one too high."""
    if not _wait_ip_lock(request, counter):
        return
    try:
        count = cache.get(reservation) or 0
        if count > 0:
            cache.set(reservation, count - 1, timeout=IP_COUNT_TIMEOUT)
    finally:
        release_ip_lock(request, counter)


def first_block(request, counter):
    """True for the first block of this IP and counter in this hour's window (it is
    logged); later blocks in the window are not, so blocked traffic writes nothing."""
    return cache.add(_ip_key(request, counter) + ':blocked', 1, timeout=IP_COUNT_TIMEOUT)
