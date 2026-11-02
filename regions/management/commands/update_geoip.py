"""Download, check and install the DB-IP Lite country database (epic-regions entry 4).

    python manage.py update_geoip

Run monthly by the `geoipupdate` job in deploy/jobs.conf (through `manage.py runjob`).
It downloads https://download.db-ip.com/free/dbip-country-lite-YYYY-MM.mmdb.gz for the
current UTC month (the previous month while this month's is not out yet: a 404), writes
it to a temp file next to settings.GEOIP_DB_PATH, opens it, and requires a DB-IP country
database that answers the known lookups (8.8.8.8 is US; 2001:4860:4860::8888 maps to some
country, since DB-IP puts that block in CA). Only
then does os.replace swap it in, atomically; the workers' cached readers notice the new
file (regions.geoip).

Any failure (network, gzip, a file that is not a database, a failed lookup) removes the
temp file, keeps the installed database, logs an error (which reaches Sentry) and exits
non-zero. The download carries no visitor data; the job prints only the month, the size
and the result.
"""
import datetime
import gzip
import io
import os
import tempfile
import urllib.error
import zlib
from pathlib import Path

import maxminddb
from django.conf import settings
from django.core.management.base import BaseCommand

from accounts.http import FETCH_ERRORS, ResponseTooLarge, get_bytes
from regions.geoip import logger, record_country

DOWNLOAD_URL = 'https://download.db-ip.com/free/dbip-country-lite-{month}.mmdb.gz'
DOWNLOAD_TIMEOUT = 60  # seconds, urllib's socket timeout (each connect and read)
# The database's metadata `database_type` (DB-IP: "DBIP-Country-Lite").
DATABASE_TYPE_PREFIX = 'DBIP-Country'
# Lookups any real country database answers; a file failing one is not installed. None:
# any ISO-2 code will do (user, 2026-11-02: DB-IP maps Google's IPv6 DNS block to CA, so
# the IPv6 check only proves the IPv6 tree answers).
KNOWN_LOOKUPS = (('8.8.8.8', 'US'), ('2001:4860:4860::8888', None))
# The country database is about 8 MB; refuse anything far larger (a broken or hostile
# download) before writing it.
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_DATABASE_BYTES = 200 * 1024 * 1024


class UpdateFailed(Exception):
    """The new database was not installed; the message says why (no visitor data)."""


def utc_today():
    return datetime.datetime.now(datetime.UTC).date()


def months_to_try(today):
    """This UTC month, then the previous one, as YYYY-MM."""
    previous = today.replace(day=1) - datetime.timedelta(days=1)
    return [today.strftime('%Y-%m'), previous.strftime('%Y-%m')]


def download(months):
    """(month, gzip bytes) of the first month whose file exists (404: try the next)."""
    for month in months:
        try:
            return month, get_bytes(DOWNLOAD_URL.format(month=month), timeout=DOWNLOAD_TIMEOUT,
                                    max_bytes=MAX_DOWNLOAD_BYTES)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                continue
            raise UpdateFailed(f'download of {month} failed: HTTP {error.code}') from error
        except ResponseTooLarge as error:
            raise UpdateFailed(
                f'{month}: download larger than {MAX_DOWNLOAD_BYTES} bytes') from error
        except FETCH_ERRORS as error:
            raise UpdateFailed(
                f'download of {month} failed ({type(error).__name__})') from error
    raise UpdateFailed(f'no database published for {" or ".join(months)} (HTTP 404)')


def decompress(month, data):
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as archive:
            database = archive.read(MAX_DATABASE_BYTES + 1)
    except (OSError, EOFError, zlib.error) as error:
        raise UpdateFailed(f'{month}: not a valid gzip file ({type(error).__name__})') from error
    if len(database) > MAX_DATABASE_BYTES:
        raise UpdateFailed(f'{month}: database larger than {MAX_DATABASE_BYTES} bytes')
    if not database:
        raise UpdateFailed(f'{month}: empty database')
    return database


def validate(path, month):
    """Raise UpdateFailed unless `path` is a DB-IP country database answering the known
    lookups."""
    try:
        reader = maxminddb.open_database(str(path), maxminddb.MODE_MMAP)
    except Exception as error:  # noqa: BLE001 - any unreadable file is refused
        raise UpdateFailed(f'{month}: not a MaxMind DB file ({type(error).__name__})') from error
    try:
        database_type = str(reader.metadata().database_type or '')
        if not database_type.startswith(DATABASE_TYPE_PREFIX):
            raise UpdateFailed(f'{month}: not a DB-IP country database ({database_type!r})')
        for ip, expected in KNOWN_LOOKUPS:
            try:
                found = record_country(reader.get(ip))
            except Exception as error:  # noqa: BLE001
                raise UpdateFailed(
                    f'{month}: lookup of {ip} failed ({type(error).__name__})') from error
            if expected is None:
                if not (found and len(found) == 2 and found.isascii() and found.isalpha()):
                    raise UpdateFailed(f'{month}: {ip} gives {found}, expected a country')
            elif found != expected:
                raise UpdateFailed(f'{month}: {ip} gives {found}, expected {expected}')
    finally:
        reader.close()


def install(database, month, target):
    """Write `database` next to `target`, validate it, then swap it in atomically. The temp
    file is removed on any failure."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f'.{target.name}.',
                                    suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as tmp:
            tmp.write(database)
            tmp.flush()
            os.fsync(tmp.fileno())
        validate(tmp_name, month)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def update(target=None, today=None):
    """Install the newest database at `target` (default settings.GEOIP_DB_PATH); returns
    (month, size in bytes). Raises UpdateFailed and keeps the installed file on failure."""
    target = target or settings.GEOIP_DB_PATH
    month, data = download(months_to_try(today or utc_today()))
    database = decompress(month, data)
    try:
        install(database, month, target)
    except OSError as error:
        raise UpdateFailed(f'{month}: could not write the database ({type(error).__name__}: '
                           f'{error.strerror or error})') from error
    return month, len(database)


class Command(BaseCommand):
    help = 'Download, check and install the DB-IP Lite country database (geo-IP).'

    def handle(self, **options):
        try:
            month, size = update()
        except UpdateFailed as error:
            message = f'update_geoip: database not updated, the installed one is kept: {error}'
            # The ERROR goes to the journal (LOGGING) and reaches Sentry as one event;
            # exiting non-zero fails the job's check-in and its systemd unit without a
            # second event from runjob (which reports exceptions, not SystemExit).
            logger.error(message)
            raise SystemExit(1) from error
        self.stdout.write(f'update_geoip: installed DB-IP country lite {month} '
                          f'({size / 1024 / 1024:.1f} MB)')
