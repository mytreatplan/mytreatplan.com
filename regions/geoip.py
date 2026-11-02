"""The visitor's country from the local geo-IP database (epic-regions entry 4, AD-14).

`request_country(request)` answers the visitor's ISO alpha-2 country, or None. It looks
the client IP (eventlog.service.client_ip) up in the DB-IP Lite country database at
settings.GEOIP_DB_PATH (CC BY 4.0), on this instance: no IP is sent anywhere. The monthly
`geoipupdate` job (manage.py update_geoip) installs and replaces that file.

None means "not known": no database yet (before the job's first run) or an unreadable
one, an address the database does not cover, a malformed one, or a private, loopback or
reserved address.

The reader is opened once per process and reopened when the file's inode or mtime
changes, so the workers pick up the job's atomic swap without a restart.

Staging only: with GEOIP_OVERRIDE_HEADER_ENABLED, a request header
`X-MyTP-Geo-Country: <ISO-2>` (any case) wins over the lookup; an invalid value is
ignored. regions.checks refuses the setting on a public site.

Nothing here logs an IP or a country.
"""
import ipaddress
import logging
import os
import threading

import maxminddb
from django.conf import settings
from django_countries import countries

from eventlog.service import client_ip

logger = logging.getLogger('regions.geoip')

OVERRIDE_HEADER = 'X-MyTP-Geo-Country'
OVERRIDE_META_KEY = 'HTTP_' + OVERRIDE_HEADER.upper().replace('-', '_')

_lock = threading.Lock()
# path -> (file signature (st_dev, st_ino, st_mtime_ns, st_size), reader or None)
_readers = {}


def _signature(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)


def get_reader(path=None):
    """The cached maxminddb reader of `path` (default settings.GEOIP_DB_PATH), reopened when
    the file changed; None when the file is missing or cannot be read as a database."""
    path = str(path or settings.GEOIP_DB_PATH)
    signature = _signature(path)
    with _lock:
        cached = _readers.get(path)
        if signature is None:
            _readers.pop(path, None)
            return None
        if cached and cached[0] == signature:
            return cached[1]
        try:
            reader = maxminddb.open_database(path)
        except Exception as error:  # noqa: BLE001 - a bad file must not break a page
            logger.warning('Geo-IP database unreadable (%s); no country lookups until it '
                           'is replaced', type(error).__name__)
            reader = None
        # The previous reader is dropped, not closed: another thread may still be reading
        # it; it is freed with its last reference.
        _readers[path] = (signature, reader)
        return reader


def clear_cache():
    """Forget every cached reader (tests)."""
    with _lock:
        _readers.clear()


def _public_address(ip):
    """`ip` as an ipaddress object when it is a global address, else None."""
    try:
        address = ipaddress.ip_address((ip or '').strip())
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    if (address.is_private or address.is_loopback or address.is_reserved
            or address.is_link_local or address.is_multicast or address.is_unspecified
            or not address.is_global):
        return None
    return address


def ip_country(ip, path=None):
    """The ISO alpha-2 country of address `ip` (a string) in the database, or None."""
    address = _public_address(ip)
    if address is None:
        return None
    reader = get_reader(path)
    if reader is None:
        return None
    try:
        record = reader.get(address)
    except Exception as error:  # noqa: BLE001 - a bad file must not break a page
        logger.warning('Geo-IP lookup failed (%s)', type(error).__name__)
        return None
    return record_country(record)


def record_country(record):
    """`record['country']['iso_code']`, uppercased, or None."""
    if not isinstance(record, dict):
        return None
    country = record.get('country')
    code = country.get('iso_code') if isinstance(country, dict) else None
    if not isinstance(code, str) or not code.strip():
        return None
    return code.strip().upper()


def override_country(request):
    """The country of the staging override header, when it is enabled and names a valid
    ISO alpha-2 code; otherwise None."""
    if not settings.GEOIP_OVERRIDE_HEADER_ENABLED:
        return None
    value = (request.META.get(OVERRIDE_META_KEY) or '').strip().upper()
    if len(value) != 2 or not value.isascii() or not value.isalpha():
        return None
    return value if countries.alpha2(value) else None


def request_country(request):
    """The visitor's ISO alpha-2 country, or None (see the module docstring)."""
    return override_country(request) or ip_country(client_ip(request))
