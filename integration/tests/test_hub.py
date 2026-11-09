"""Hub lookups (epic-admin-interface entry 7): `integration.hub.lookup` and `hub_lookup`
against a fake admin site at mytp_core.interface._open that checks the portal's
signature (fixed, non-secret test seeds). Every matrix row: an answer, the admin site
down, a region mismatch, the setting off, a bad answer, dev-ae. No test reaches the
network or a KMS."""
import base64
import io
import json
import logging
import threading
import time
import urllib.error
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.checks import run_checks
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.utils import CursorWrapper
from django.core.management import CommandError, call_command

from integration import checks, hub
from integration.hub import HubLookupFailed, HubRefused, HubUnavailable, lookup
from mytp_core import interface
from mytp_core.contracts import SampleAvailability, error_body, sample_availability
from mytp_core.contracts.hub import SAMPLE_AVAILABILITY_PATH

PORTAL_SEED = b'test-only-interface-seed-portal!'
PORTAL_AE_SEED = b'test-only-interface-seed-portal2'
PORTAL = interface.SigningKey('p1', PORTAL_SEED)
PORTAL_AE = interface.SigningKey('q1', PORTAL_AE_SEED)
ADMIN_URL = 'https://admindev.example.test'
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
SLOT_LABEL = 'Sample specialist EU-1'


@pytest.fixture(autouse=True)
def interface_settings(settings):
    settings.SITE = 'dev'
    settings.REGION = 'eu'
    settings.INTERFACE_SIGNING_KEY = f'p1:{base64.b64encode(PORTAL_SEED).decode()}'
    settings.INTERFACE_PEERS = f'admindev={ADMIN_URL}'
    settings.INTERFACE_PEER_KEYS = ''
    settings.INTERFACE_ADMIN_SITE = ''
    settings.HUB_LOOKUP_TIMEOUT = 3
    interface.reset_signing_key()
    yield settings
    interface.reset_signing_key()


def answer_for(region):
    return sample_availability(region, NOW, [
        (NOW + timedelta(days=1), NOW + timedelta(days=1, minutes=30),
         f'Sample specialist {region.upper()}-1')])


class ReplayCache:
    """The admin's replay cache, in memory (the lookup runs in its own thread: no
    database there)."""

    def __init__(self):
        self.keys = set()
        self.lock = threading.Lock()

    def add(self, key, value, timeout=None):
        with self.lock:
            if key in self.keys:
                return False
            self.keys.add(key)
            return True


class FakeAdmin:
    """The admin's GET /api/v1/hub/sample-availability: verifies the portal's signature
    (dev: eu, dev-ae: ae), refuses another region (403 region_mismatch), answers 404
    with `setting_off`. `down`, `status`, `body` (raw bytes answered), `delay` (seconds)
    change it."""

    def __init__(self):
        self.ring = interface.Keyring.from_entries([('dev', 'p1', PORTAL.public_text),
                                                    ('dev-ae', 'q1', PORTAL_AE.public_text)])
        self.regions = {'dev': 'eu', 'dev-ae': 'ae'}
        self.setting_off = False
        self.down = False
        self.status = None
        self.body = None
        self.delay = 0
        self.calls = []
        self.replay = ReplayCache()

    def __call__(self, request, timeout):
        self.calls.append((request.full_url, timeout))
        if self.delay:
            time.sleep(self.delay)
        if self.down:
            raise urllib.error.URLError(ConnectionRefusedError(61, 'refused'))
        parts = urlsplit(request.full_url)
        assert f'{parts.scheme}://{parts.netloc}' == ADMIN_URL
        assert parts.path == SAMPLE_AVAILABILITY_PATH and request.method == 'GET'
        try:
            claims = interface.verify_request(
                request.get_header('Authorization'), 'GET',
                interface.request_target(parts.path, parts.query), b'',
                keyring=self.ring, audience='admindev', cache=self.replay)
        except interface.VerificationFailed as exc:
            return self._error(exc.code, 401, request)
        if self.status:
            raise urllib.error.HTTPError(request.full_url, self.status, 'x', {},
                                         io.BytesIO(b'<html>bad gateway</html>'))
        if self.setting_off:
            return self._error('not_found', 404, request)
        region = parse_qs(parts.query).get('region', [''])[0]
        if region != self.regions[claims['iss']]:
            return self._error('region_mismatch', 403, request)
        body = self.body if self.body is not None else json.dumps(answer_for(region)).encode()
        response = io.BytesIO(body)
        response.status = 200
        return response

    def _error(self, code, status, request):
        raise urllib.error.HTTPError(request.full_url, status, 'x', {}, io.BytesIO(
            json.dumps(error_body(code, 'x')).encode()))


@pytest.fixture
def admin(monkeypatch):
    fake = FakeAdmin()
    monkeypatch.setattr(interface, '_open', fake)
    return fake


def sample():
    return lookup(SAMPLE_AVAILABILITY_PATH, SampleAvailability)


def failure(kind=HubLookupFailed):
    with pytest.raises(kind) as caught:
        sample()
    return caught.value


def hub_lines(caplog):
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == 'integration.hub']


# --- Matrix -----------------------------------------------------------------------------

@pytest.fixture
def no_database(monkeypatch):
    """Any database use, in any thread (the lookup's own included), is recorded and
    fails: patched on the connection and cursor classes, not per connection."""
    used = []

    def refuse(name):
        def refused(*args, **kwargs):
            used.append(name)
            raise AssertionError(f'database used: {name}')
        return refused
    monkeypatch.setattr(BaseDatabaseWrapper, 'ensure_connection', refuse('connect'))
    monkeypatch.setattr(CursorWrapper, 'execute', refuse('execute'))
    monkeypatch.setattr(CursorWrapper, 'executemany', refuse('executemany'))
    return used


@pytest.mark.django_db
def test_a_lookup_answers_and_stores_nothing(admin, caplog, no_database):
    caplog.set_level(logging.INFO)
    result = sample()
    assert no_database == []
    assert isinstance(result, SampleAvailability)
    assert result.region == 'eu' and result.slots[0].specialist == SLOT_LABEL
    url, timeout = admin.calls[0]
    assert url == f'{ADMIN_URL}{SAMPLE_AVAILABILITY_PATH}?region=eu' and timeout == 3
    assert hub_lines(caplog) == [
        (logging.INFO, f'hub lookup {SAMPLE_AVAILABILITY_PATH}: answered')]
    assert SLOT_LABEL not in caplog.text
    # Not cached: each lookup asks again.
    sample()
    assert len(admin.calls) == 2


def test_dev_ae_gets_its_own_answer(admin, settings):
    settings.SITE = 'dev-ae'
    settings.REGION = 'ae'
    settings.INTERFACE_SIGNING_KEY = f'q1:{base64.b64encode(PORTAL_AE_SEED).decode()}'
    interface.reset_signing_key()
    result = sample()
    assert result.region == 'ae' and result.slots[0].specialist == 'Sample specialist AE-1'
    assert admin.calls[0][0].endswith('?region=ae')


def test_the_admin_site_down_is_unavailable(admin, caplog):
    caplog.set_level(logging.INFO)
    admin.down = True
    exc = failure(HubUnavailable)
    assert (exc.outcome, exc.code, str(exc)) == ('unavailable', 'network',
                                                  'unavailable (network)')
    assert hub_lines(caplog) == [
        (logging.WARNING, f'hub lookup {SAMPLE_AVAILABILITY_PATH}: unavailable (network)')]


def lookup_threads():
    return [t for t in threading.enumerate() if t.name == 'hub-lookup']


def test_a_hanging_admin_site_stops_at_the_overall_cap(admin, monkeypatch):
    monkeypatch.setattr(hub, 'OVERALL_CAP', 0.1)
    admin.delay = 0.4
    started = time.monotonic()
    exc = failure(HubUnavailable)
    assert exc.code == 'timeout'
    # Returned at the cap, well before the peer answered.
    assert time.monotonic() - started < 0.35
    # The leftover thread finishes by itself; wait for it so nothing runs after the test.
    for thread in lookup_threads():
        thread.join(5)
    assert lookup_threads() == []


def test_busy_when_every_slot_is_taken(admin, monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(hub, '_slots', slots)
    assert slots.acquire(blocking=False)  # one lookup still running
    exc = failure(HubUnavailable)
    assert exc.code == 'busy'
    assert admin.calls == [] and lookup_threads() == []
    slots.release()
    assert sample().region == 'eu'


def test_every_lookup_gives_its_slot_back(admin, monkeypatch):
    assert hub.MAX_RUNNING == 4
    slots = threading.BoundedSemaphore(hub.MAX_RUNNING)
    monkeypatch.setattr(hub, '_slots', slots)
    sample()
    admin.down = True
    failure()
    admin.down = False
    admin.body = b'not json'
    failure()
    monkeypatch.setattr(interface, 'signed_get', lambda *a, **k: 1 / 0)
    failure()
    # All four free again (a BoundedSemaphore would raise on a double release).
    assert all(slots.acquire(blocking=False) for _ in range(hub.MAX_RUNNING))
    assert not slots.acquire(blocking=False)


def test_a_thread_that_cannot_start_gives_its_slot_back(admin, monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(hub, '_slots', slots)

    def no_thread(self):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(threading.Thread, 'start', no_thread)
    assert failure(HubUnavailable).code == 'error'
    assert slots.acquire(blocking=False)


def test_a_region_mismatch_is_refused(admin, settings):
    settings.REGION = 'ae'  # dev signs for another region
    exc = failure(HubRefused)
    assert (exc.outcome, exc.code) == ('refused', 'http_403_region_mismatch')


def test_the_setting_off_is_refused(admin):
    admin.setting_off = True
    assert failure(HubRefused).code == 'http_404_not_found'


@pytest.mark.parametrize('status, kind, code', [
    (500, HubUnavailable, 'http_500'),
    (502, HubUnavailable, 'http_502'),
    (503, HubUnavailable, 'http_503'),
    (401, HubUnavailable, 'http_401'),
    (400, HubRefused, 'http_400'),
    (405, HubRefused, 'http_405'),
])
def test_http_errors(admin, status, kind, code):
    admin.status = status
    assert failure(kind).code == code


def test_a_refusal_of_the_signature_is_unavailable(admin, settings):
    settings.INTERFACE_SIGNING_KEY = f'p1:{base64.b64encode(PORTAL_AE_SEED).decode()}'
    interface.reset_signing_key()
    assert failure(HubUnavailable).code == 'http_401_signature'


@pytest.mark.parametrize('body', [
    b'not json',
    b'[1, 2]',
    json.dumps({'lookup': 'sample_availability'}).encode(),
    json.dumps({**answer_for('eu'), 'extra': 1}).encode(),       # closed model
    json.dumps(answer_for('ae')).encode(),                       # another region
    json.dumps({'error': {'code': 'x', 'message': 'y'}}).encode(),
    b'{"pad": "' + b'x' * (64 * 1024) + b'"}',                   # over 64 KB
])
def test_a_bad_answer_is_unavailable(admin, body, caplog):
    admin.body = body
    exc = failure(HubUnavailable)
    assert exc.code == 'bad_answer'
    assert 'pad' not in caplog.text and 'xxxx' not in caplog.text


# --- Never another exception ------------------------------------------------------------

def test_not_configured(admin, settings):
    settings.INTERFACE_PEERS = ''
    assert failure(HubUnavailable).code == 'not_configured'
    settings.INTERFACE_PEERS = f'admindev={ADMIN_URL}'
    settings.REGION = ''
    assert failure(HubUnavailable).code == 'not_configured'
    assert admin.calls == []


def test_no_signing_key(admin, settings):
    settings.INTERFACE_SIGNING_KEY = ''
    interface.reset_signing_key()
    assert failure(HubUnavailable).code == 'signing_key'
    assert admin.calls == []


def test_a_malformed_signing_key_is_not_configured(admin, settings):
    settings.INTERFACE_SIGNING_KEY = 'garbage'
    interface.reset_signing_key()
    assert failure(HubUnavailable).code == 'not_configured'


@pytest.mark.parametrize('path', ['/api/v1/config', '/api/v1/hub/', 'hub/x',
                                  SAMPLE_AVAILABILITY_PATH + '?region=ae', None, 42])
def test_a_path_outside_the_hub_is_refused_unsent(admin, path):
    with pytest.raises(HubRefused) as caught:
        lookup(path, SampleAvailability)
    assert caught.value.code == 'bad_path'
    assert admin.calls == []


def test_an_unexpected_error_is_unavailable(monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError('secret detail')
    monkeypatch.setattr(interface, '_open', broken)
    assert failure(HubUnavailable).code == 'error'
    monkeypatch.setattr(hub, 'admin_site_url', broken)
    assert failure(HubUnavailable).code == 'error'
    assert 'secret detail' not in caplog.text


def test_a_broken_reader_is_unavailable(admin):
    class Broken:
        @staticmethod
        def model_validate(data):
            raise TypeError('boom')
    with pytest.raises(HubUnavailable) as caught:
        lookup(SAMPLE_AVAILABILITY_PATH, Broken)
    assert caught.value.code == 'error'


def test_the_lookup_never_raises_anything_else(monkeypatch):
    for error in (KeyError('x'), ValueError('x'), OSError('x'), MemoryError()):
        def raising(*args, error=error, **kwargs):
            raise error
        monkeypatch.setattr(interface, 'signed_get', raising)
        assert isinstance(failure(), HubUnavailable)


def test_no_sentry_event_for_a_failed_lookup(admin, monkeypatch):
    import sentry_sdk
    captured = []
    monkeypatch.setattr(sentry_sdk, 'capture_message', lambda *a, **k: captured.append(a))
    monkeypatch.setattr(sentry_sdk, 'capture_exception', lambda *a, **k: captured.append(a))
    admin.down = True
    failure()
    assert captured == []


def test_concurrent_lookups_are_independent(admin):
    results = []

    def run():
        results.append(sample().region)
    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == ['eu'] * 4


# --- Command and check ------------------------------------------------------------------

def test_hub_lookup_sample_prints_the_answer(admin):
    out = io.StringIO()
    call_command('hub_lookup', 'sample', stdout=out)
    assert json.loads(out.getvalue()) == answer_for('eu')


def test_hub_lookup_sample_failure_is_one_line(admin):
    admin.down = True
    with pytest.raises(CommandError) as caught:
        call_command('hub_lookup', 'sample')
    assert str(caught.value) == 'hub_lookup sample: unavailable (network)'
    admin.down = False
    admin.setting_off = True
    with pytest.raises(CommandError) as caught:
        call_command('hub_lookup', 'sample')
    assert str(caught.value) == 'hub_lookup sample: refused (http_404_not_found)'


@pytest.mark.parametrize('value, ok', [(3, True), (0.5, True), (10, True), (0, False),
                                       (-1, False), (10.5, False), (float('nan'), False),
                                       (float('inf'), False), ('3', False), (True, False)])
def test_check_e007(settings, value, ok):
    settings.HUB_LOOKUP_TIMEOUT = value
    found = [e.id for e in run_checks() if e.id == checks.BAD_HUB_LOOKUP_TIMEOUT]
    assert found == ([] if ok else [checks.BAD_HUB_LOOKUP_TIMEOUT])
