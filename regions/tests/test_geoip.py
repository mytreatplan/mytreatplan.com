"""Epic-regions entry 4: the geo-IP country lookup (regions.geoip), the staging override
and its check, and the monthly update job (manage.py update_geoip). The test databases are
built here with mmdb-writer; the download is stubbed."""
import datetime
import gzip
import logging
import os
import re
import urllib.error
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from django.test import RequestFactory
from mmdb_writer import MMDBWriter
from mytp_core import jobs
from netaddr import IPSet

from accounts import http
from regions import checks, geoip
from regions.management.commands import update_geoip

REPO = Path(django_settings.BASE_DIR)
DEPLOY = REPO / 'deploy'

SPANISH_IP = '88.0.0.1'
SAUDI_IP = '2.88.0.1'
UNKNOWN_IP = '1.1.1.1'  # global, but not in the test database
RANGES = {
    'ES': ['88.0.0.0/11'],
    'SA': ['2.88.0.0/14'],
    'US': ['8.8.8.0/24', '2001:4860::/32'],
}


def build_mmdb(path, ranges=None, database_type='DBIP-Country-Lite'):
    """A tiny MMDB shaped like DB-IP's country database."""
    writer = MMDBWriter(ip_version=6, ipv4_compatible=True, database_type=database_type,
                        languages=['en'], description='test')
    for code, networks in (RANGES if ranges is None else ranges).items():
        writer.insert_network(IPSet(networks), {
            'country': {'iso_code': code, 'names': {'en': code}},
        })
    writer.to_db_file(str(path))
    return Path(path)


@pytest.fixture(autouse=True)
def fresh_readers():
    geoip.clear_cache()
    yield
    geoip.clear_cache()


@pytest.fixture
def db_path(settings, tmp_path):
    path = tmp_path / 'geoip' / 'dbip-country-lite.mmdb'
    path.parent.mkdir()
    settings.GEOIP_DB_PATH = str(path)
    settings.GEOIP_OVERRIDE_HEADER_ENABLED = False
    return path


@pytest.fixture
def database(db_path):
    return build_mmdb(db_path)


def request_from(ip=None, **headers):
    meta = {'REMOTE_ADDR': ip} if ip else {}
    return RequestFactory().get('/', **meta, **headers)


# --- Lookup ---------------------------------------------------------------------------------

def test_spanish_ip(database):
    assert geoip.request_country(request_from(SPANISH_IP)) == 'ES'


def test_saudi_ip(database):
    assert geoip.request_country(request_from(SAUDI_IP)) == 'SA'


def test_ipv6_and_lowercase_codes_are_uppercased(db_path):
    build_mmdb(db_path, {'us': ['2001:4860::/32']})
    assert geoip.request_country(request_from('2001:4860:4860::8888')) == 'US'


def test_uses_the_ip_nginx_appended(database):
    """eventlog.service.client_ip: the last X-Forwarded-For entry wins."""
    request = request_from('127.0.0.1', HTTP_X_FORWARDED_FOR=f'{SAUDI_IP}, {SPANISH_IP}')
    assert geoip.request_country(request) == 'ES'


@pytest.mark.parametrize('ip', ['10.0.0.1', '127.0.0.1', '::1', '192.168.1.10', '169.254.0.1',
                                '240.0.0.1', '0.0.0.0', '::ffff:10.0.0.1'])
def test_private_loopback_and_reserved_addresses_give_none(db_path, ip):
    # Even a database that maps them is not asked.
    build_mmdb(db_path, {'ES': ['0.0.0.0/1', '128.0.0.0/1', '::/1']})
    assert geoip.request_country(request_from(ip)) is None


@pytest.mark.parametrize('ip', [UNKNOWN_IP, 'not-an-ip', ''])
def test_unknown_or_malformed_address_gives_none(database, ip):
    assert geoip.request_country(request_from(ip)) is None


def test_ipv4_mapped_address_is_looked_up_as_ipv4(database):
    assert geoip.request_country(request_from(f'::ffff:{SPANISH_IP}')) == 'ES'


def test_no_database_gives_none(db_path):
    assert not db_path.exists()
    assert geoip.request_country(request_from(SPANISH_IP)) is None


def test_unreadable_database_gives_none_without_an_ip_in_the_log(db_path, caplog):
    db_path.write_bytes(b'not a database')
    with caplog.at_level(logging.WARNING, logger='regions.geoip'):
        assert geoip.request_country(request_from(SPANISH_IP)) is None
        assert geoip.request_country(request_from(SPANISH_IP)) is None
    assert len(caplog.records) == 1  # not retried until the file changes
    assert SPANISH_IP not in caplog.text


def test_the_reader_is_cached(database, monkeypatch):
    assert geoip.request_country(request_from(SPANISH_IP)) == 'ES'
    monkeypatch.setattr(geoip.maxminddb, 'open_database',
                        lambda *a, **k: pytest.fail('reopened an unchanged file'))
    assert geoip.request_country(request_from(SAUDI_IP)) == 'SA'


def test_a_swapped_file_is_picked_up_without_a_restart(database, tmp_path):
    assert geoip.request_country(request_from(SPANISH_IP)) == 'ES'

    new = build_mmdb(database.parent / 'new.tmp', {'PT': RANGES['ES'], 'US': RANGES['US']})
    os.replace(new, database)

    assert geoip.request_country(request_from(SPANISH_IP)) == 'PT'
    assert geoip.request_country(request_from(SAUDI_IP)) is None


def test_a_removed_file_gives_none(database):
    assert geoip.request_country(request_from(SPANISH_IP)) == 'ES'
    database.unlink()
    assert geoip.request_country(request_from(SPANISH_IP)) is None


# --- Override -----------------------------------------------------------------------------

@pytest.mark.parametrize('value', ['sa', 'SA', ' Sa '])
def test_override_on_wins_over_the_lookup(database, settings, value):
    settings.GEOIP_OVERRIDE_HEADER_ENABLED = True
    request = request_from(SPANISH_IP, HTTP_X_MYTP_GEO_COUNTRY=value)
    assert geoip.request_country(request) == 'SA'


@pytest.mark.parametrize('value', ['', 'S', 'SAU', 'S1', 'ZZ', 'ñx', '--'])
def test_an_invalid_override_is_ignored(database, settings, value):
    settings.GEOIP_OVERRIDE_HEADER_ENABLED = True
    request = request_from(SPANISH_IP, HTTP_X_MYTP_GEO_COUNTRY=value)
    assert geoip.request_country(request) == 'ES'


def test_override_off_ignores_the_header(database, settings):
    settings.GEOIP_OVERRIDE_HEADER_ENABLED = False
    request = request_from(SPANISH_IP, HTTP_X_MYTP_GEO_COUNTRY='SA')
    assert geoip.request_country(request) == 'ES'


def test_override_works_without_a_database(db_path, settings):
    settings.GEOIP_OVERRIDE_HEADER_ENABLED = True
    assert geoip.request_country(request_from('10.0.0.1', HTTP_X_MYTP_GEO_COUNTRY='sa')) == 'SA'


def test_override_off_by_default():
    assert geoip.OVERRIDE_META_KEY == 'HTTP_X_MYTP_GEO_COUNTRY'
    text = (REPO / 'mytreatplan' / 'settings.py').read_text()
    assert ("config('GEOIP_OVERRIDE_HEADER_ENABLED', default=False,\n"
            "                                       cast=bool)") in text


# --- The check ------------------------------------------------------------------------------

def test_check_fails_with_the_override_on_a_public_site(settings):
    settings.GEOIP_OVERRIDE_HEADER_ENABLED = True
    settings.ROBOTS_NOINDEX = False
    errors = checks.check_geoip_override(None)
    assert [error.id for error in errors] == ['regions.E001']


@pytest.mark.parametrize('override,noindex', [(True, True), (False, False), (False, True)])
def test_check_passes_otherwise(settings, override, noindex):
    settings.GEOIP_OVERRIDE_HEADER_ENABLED = override
    settings.ROBOTS_NOINDEX = noindex
    assert checks.check_geoip_override(None) == []


def test_manage_py_check_reports_it(settings):
    from django.core.management.base import SystemCheckError
    settings.GEOIP_OVERRIDE_HEADER_ENABLED = True
    settings.ROBOTS_NOINDEX = False
    with pytest.raises(SystemCheckError, match='regions.E001'):
        call_command('check')


def test_staging_sets_both():
    example = _env_values(DEPLOY / 'staging.env.example')
    assert example['GEOIP_OVERRIDE_HEADER_ENABLED'] == 'True'
    assert example['ROBOTS_NOINDEX'] == 'True'


def _env_values(path):
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if line.strip() and not line.startswith('#'))


# --- The update job -------------------------------------------------------------------------

class FakeDownloads:
    """Stands in for accounts.http.get_bytes: `files` maps a month to the bytes served or to
    an exception raised; any other month is a 404. `urls` records what was fetched."""

    def __init__(self):
        self.files = {}
        self.urls = []
        self.timeouts = []
        self.max_bytes = []

    def __call__(self, url, timeout=None, max_bytes=None):
        self.urls.append(url)
        self.timeouts.append(timeout)
        self.max_bytes.append(max_bytes)
        month = re.search(r'dbip-country-lite-(\d{4}-\d{2})\.mmdb\.gz$', url).group(1)
        served = self.files.get(month)
        if served is None:
            raise urllib.error.HTTPError(url, 404, 'Not Found', {}, None)
        if isinstance(served, Exception):
            raise served
        return served


TODAY = datetime.date(2026, 11, 3)


@pytest.fixture
def downloads(monkeypatch):
    fake = FakeDownloads()
    monkeypatch.setattr(update_geoip, 'get_bytes', fake)
    monkeypatch.setattr(update_geoip, 'utc_today', lambda: TODAY)
    return fake


def gz_database(tmp_path, name='source.mmdb', **kwargs):
    return gzip.compress(build_mmdb(tmp_path / name, **kwargs).read_bytes())


@pytest.fixture
def installed(db_path):
    """A previous database (ES range mapped to PT, so a new install is visible)."""
    build_mmdb(db_path, {'PT': RANGES['ES'], 'US': RANGES['US']})
    return db_path.read_bytes()


def leftovers(db_path):
    return sorted(p.name for p in db_path.parent.iterdir() if p.name != db_path.name)


def test_job_installs_this_months_database(db_path, downloads, tmp_path, capsys):
    downloads.files['2026-11'] = gz_database(tmp_path)

    call_command('update_geoip')

    assert downloads.urls == [
        'https://download.db-ip.com/free/dbip-country-lite-2026-11.mmdb.gz']
    assert downloads.timeouts == [60]
    assert downloads.max_bytes == [update_geoip.MAX_DOWNLOAD_BYTES]
    assert db_path.exists() and leftovers(db_path) == []
    out = capsys.readouterr().out
    assert re.fullmatch(r'update_geoip: installed DB-IP country lite 2026-11 \(\d+\.\d MB\)\n',
                        out)
    assert geoip.request_country(request_from(SPANISH_IP)) == 'ES'


def test_job_replaces_the_previous_file_atomically(installed, db_path, downloads, tmp_path,
                                                   monkeypatch):
    assert geoip.request_country(request_from(SPANISH_IP)) == 'PT'
    downloads.files['2026-11'] = gz_database(tmp_path)
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(update_geoip.os, 'replace',
                        lambda src, dst: (replaced.append((Path(src).parent, Path(dst))),
                                          real_replace(src, dst)))

    call_command('update_geoip')

    assert replaced == [(db_path.parent, db_path)]  # a rename inside the same folder
    assert db_path.read_bytes() != installed
    assert geoip.request_country(request_from(SPANISH_IP)) == 'ES'


def test_job_takes_the_previous_month_when_this_one_is_not_out(db_path, downloads, tmp_path,
                                                              capsys):
    downloads.files['2026-10'] = gz_database(tmp_path)

    call_command('update_geoip')

    assert [url.rsplit('-', 2)[-2:] for url in downloads.urls] == [
        ['2026', '11.mmdb.gz'], ['2026', '10.mmdb.gz']]
    assert 'country lite 2026-10' in capsys.readouterr().out
    assert db_path.exists()


def test_ipv6_known_lookup_may_be_any_country(installed, db_path, downloads, tmp_path):
    """DB-IP maps 2001:4860:4860::8888 to CA (user decision, 2026-11-02): accepted."""
    downloads.files['2026-11'] = gz_database(
        tmp_path, ranges={'ES': RANGES['ES'], 'US': ['8.8.8.0/24'], 'CA': ['2001:4860::/32']})

    call_command('update_geoip')

    assert db_path.read_bytes() != installed
    assert geoip.request_country(request_from('2001:4860:4860::8888')) == 'CA'


def test_months_to_try_crosses_the_year():
    assert update_geoip.months_to_try(datetime.date(2027, 1, 3)) == ['2027-01', '2026-12']


def run_failing(caplog):
    """Run the job expecting a failure; returns what it logged (one ERROR)."""
    with caplog.at_level(logging.ERROR, logger='regions.geoip'):
        with pytest.raises(SystemExit) as exit_info:
            call_command('update_geoip')
    assert exit_info.value.code == 1
    assert [record.levelname for record in caplog.records] == ['ERROR']
    return caplog.text


@pytest.mark.parametrize('served', [
    b'not gzip at all',
    gzip.compress(b'gzip, but not a MaxMind DB'),
    gzip.compress(b'x' * 10)[:-6],  # truncated
    b'',
])
def test_corrupt_download_keeps_the_installed_database(installed, db_path, downloads, served,
                                                       caplog):
    downloads.files['2026-11'] = served

    err = run_failing(caplog)

    assert db_path.read_bytes() == installed
    assert leftovers(db_path) == []
    assert 'database not updated, the installed one is kept' in err


@pytest.mark.parametrize('kwargs,reason', [
    ({'database_type': 'GeoIP2-City'}, 'not a DB-IP country database'),
    ({'ranges': {'ES': RANGES['ES'], 'US': ['8.8.8.0/24']}},
     '2001:4860:4860::8888 gives None, expected a country'),
    ({'ranges': {'ES': RANGES['ES'] + ['8.8.8.0/24'], 'US': ['2001:4860::/32']}},
     '8.8.8.8 gives ES'),
])
def test_wrong_data_keeps_the_installed_database(installed, db_path, downloads, tmp_path,
                                                 kwargs, reason, caplog):
    downloads.files['2026-11'] = gz_database(tmp_path, **kwargs)

    err = run_failing(caplog)

    assert reason in err
    assert db_path.read_bytes() == installed
    assert leftovers(db_path) == []


@pytest.mark.parametrize('error', [
    urllib.error.URLError('blocked'), TimeoutError('timed out'),
    urllib.error.HTTPError('u', 503, 'Unavailable', {}, None),
])
def test_network_failure_keeps_the_installed_database(installed, db_path, downloads, error,
                                                      caplog):
    downloads.files['2026-11'] = error

    err = run_failing(caplog)

    assert db_path.read_bytes() == installed
    assert leftovers(db_path) == []
    assert '2026-11' in err
    assert len(downloads.urls) == 1  # only a 404 falls back to the previous month


def test_oversized_download_keeps_the_installed_database(installed, db_path, downloads,
                                                          caplog):
    downloads.files['2026-11'] = http.ResponseTooLarge('too large')

    err = run_failing(caplog)

    assert f'download larger than {update_geoip.MAX_DOWNLOAD_BYTES} bytes' in err
    assert db_path.read_bytes() == installed
    assert leftovers(db_path) == []


class StubResponse:
    """An endless 200 body that fails the test if read without a bound or past `limit`."""
    status = 200

    def __init__(self, limit):
        self.limit = limit
        self.reads = []

    def read(self, amount=None):
        self.reads.append(amount)
        assert amount is not None and amount <= self.limit, 'read past the limit'
        return b'x' * amount

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_get_bytes_refuses_an_over_limit_body_without_reading_it_all(monkeypatch):
    response = StubResponse(limit=11)
    monkeypatch.setattr(http.urllib.request, 'urlopen', lambda request, timeout: response)

    with pytest.raises(http.ResponseTooLarge):
        http.get_bytes('https://download.test.invalid/x.gz', timeout=60, max_bytes=10)

    assert response.reads == [11]


def test_get_bytes_returns_a_body_within_the_limit(monkeypatch):
    class Small(StubResponse):
        def read(self, amount=None):
            super().read(amount)
            return b'12345'
    monkeypatch.setattr(http.urllib.request, 'urlopen', lambda request, timeout: Small(11))

    assert http.get_bytes('https://download.test.invalid/x.gz', max_bytes=10) == b'12345'


def test_neither_month_published(installed, db_path, downloads, caplog):
    err = run_failing(caplog)
    assert 'no database published for 2026-11 or 2026-10' in err
    assert db_path.read_bytes() == installed


def test_first_install_failure_leaves_no_file(db_path, downloads, caplog):
    downloads.files['2026-11'] = b'garbage'
    run_failing(caplog)
    assert not db_path.exists() and leftovers(db_path) == []


def test_failure_reaches_sentry_once(installed, downloads, sentry_capture, caplog):
    downloads.files['2026-11'] = b'garbage'

    run_failing(caplog)

    assert len(sentry_capture.events) == 1
    event = sentry_capture.events[0]
    assert event['level'] == 'error'
    assert 'database not updated' in event['logentry']['message']


def test_job_runs_through_runjob_and_exits_non_zero(installed, db_path, downloads,
                                                    release_jobs, capsys):
    downloads.files['2026-11'] = b'garbage'

    with pytest.raises(SystemExit):
        call_command('runjob', 'geoipupdate')

    assert 'job geoipupdate: error' in capsys.readouterr().out
    assert db_path.read_bytes() == installed


def test_job_creates_a_missing_folder(settings, downloads, tmp_path):
    target = tmp_path / 'new' / 'dbip-country-lite.mmdb'
    settings.GEOIP_DB_PATH = str(target)
    downloads.files['2026-11'] = gz_database(tmp_path)

    call_command('update_geoip')

    assert target.exists()


# --- Deploy files ---------------------------------------------------------------------------

def test_jobs_conf_schedules_the_update_monthly():
    job = jobs.load_jobs(jobs.release_jobs_file())['geoipupdate']
    assert (job.command, job.args) == ('update_geoip', ())
    assert job.crontab == '45 4 3 * *'


def test_default_path_is_in_the_shared_folder_provision_creates():
    text = (REPO / 'mytreatplan' / 'settings.py').read_text()
    assert ("config('GEOIP_DB_PATH',\n                       default=str(SITE_HOME / 'shared' "
            "/ 'geoip' / 'dbip-country-lite.mmdb'))") in text
    script = (DEPLOY / 'provision.sh').read_text()
    assert 'ensure_dir "$APP_HOME/shared/geoip" 0750 "$APP_USER:$APP_USER"' in script
