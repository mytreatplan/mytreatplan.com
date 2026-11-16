import hashlib
import http.client
import socket

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

# Fixed, non-secret field keys for tests (the same as in .github/workflows/ci.yml). They
# replace whatever a developer's settings.env holds, so tests never touch real keys.
# base64 of b'test-only-field-key-not-secret!!' and b'test-only-blind-index-key-32byte'.
TEST_FIELD_ENCRYPTION_KEYS = 'test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE='
TEST_BLIND_INDEX_KEY = 'dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU='

# The external checks' endpoints in tests: hosts that cannot exist (RFC 2606), so even a
# call that slipped past the guard below could not reach Have I Been Pwned or Debounce.
TEST_PWNED_PASSWORDS_API_URL = 'https://pwned.test.invalid/range/'
TEST_DISPOSABLE_EMAIL_API_URL = 'https://disposable.test.invalid/'
TEST_TURNSTILE_VERIFY_URL = 'https://turnstile.test.invalid/siteverify'
TEST_TWILIO_VERIFY_BASE_URL = 'https://verify.test.invalid/v2'

# Cloudflare's public Turnstile test keys: the site key whose widget always passes, and
# the secrets siteverify always accepts or always refuses. The always-pass pair is the
# settings default; tests use the always-fail secret for the blocked case.
TURNSTILE_PASS_SITE_KEY = '1x00000000000000000000AA'
TURNSTILE_PASS_SECRET = '1x0000000000000000000000000000000AA'
TURNSTILE_FAIL_SECRET = '2x0000000000000000000000000000000AA'
# The token the test site keys produce; test helpers post it as the widget would.
TURNSTILE_TEST_TOKEN = 'XXXX.DUMMY.TOKEN.XXXX'


def pytest_configure(config):
    from django.conf import settings

    from accounts.crypto import DEFAULT_KEY_PROVIDER
    from signup.verification import FAKE_PROVIDER

    settings.FIELD_ENCRYPTION_KEYS = TEST_FIELD_ENCRYPTION_KEYS
    settings.BLIND_INDEX_KEY = TEST_BLIND_INDEX_KEY
    settings.FIELD_KEY_PROVIDER = DEFAULT_KEY_PROVIDER
    # No KMS-wrapped keys either (a developer's settings.env must not make tests call a KMS).
    settings.FIELD_KMS_KEYS = ''
    settings.BLIND_INDEX_KMS_KEY = ''
    settings.PWNED_PASSWORDS_API_URL = TEST_PWNED_PASSWORDS_API_URL
    settings.DISPOSABLE_EMAIL_API_URL = TEST_DISPOSABLE_EMAIL_API_URL
    settings.TURNSTILE_VERIFY_URL = TEST_TURNSTILE_VERIFY_URL
    settings.TURNSTILE_SITE_KEY = TURNSTILE_PASS_SITE_KEY
    settings.TURNSTILE_SECRET_KEY = TURNSTILE_PASS_SECRET
    # The fake verification provider, whatever settings.env selects, and no Twilio
    # credentials (a developer's settings.env must not make tests call Twilio, nor carry
    # its keys into a test).
    settings.VERIFICATION_PROVIDER = FAKE_PROVIDER
    settings.TWILIO_VERIFY_BASE_URL = TEST_TWILIO_VERIFY_BASE_URL
    settings.TWILIO_ACCOUNT_SID = ''
    settings.TWILIO_API_KEY_SID = ''
    settings.TWILIO_API_KEY_SECRET = ''
    settings.TWILIO_VERIFY_SERVICE_SID = ''
    settings.TWILIO_WHATSAPP_ENABLED = False
    # No interface signing key or peers (a developer's settings.env must not make tests
    # call the KMS or a peer); the integration tests set fixed test keys.
    settings.INTERFACE_SIGNING_KMS_KEY = ''
    settings.INTERFACE_SIGNING_KEY = ''
    settings.INTERFACE_PEERS = ''
    settings.INTERFACE_PEER_KEYS = ''

    # django-tasks-db's table has JSON fields, so Django's system checks ask the database
    # whether it supports them. PostgreSQL (CI, servers) answers without a connection;
    # SQLite (local runs) opens one, which tests without database access may not. Every
    # SQLite Python 3.13 ships has JSON support, so say so up front (for every connection
    # object the suite creates, hence on the class).
    from django.db import connections
    if any(connections[alias].vendor == 'sqlite' for alias in connections):
        from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteWrapper
        SQLiteWrapper.features_class.supports_json_field = True


class NetworkAccessInTests(RuntimeError):
    """A test tried to reach the network. Not an OSError, so no "check unavailable"
    fallback can swallow it: the test fails."""


LOOPBACK_HOSTS = {None, '', 'localhost', '127.0.0.1', '::1'}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail any real network call: HTTP(S) connections, sockets opened by host name, and
    name lookups (except loopback and the test database's host)."""
    from django.conf import settings

    allowed_hosts = LOOPBACK_HOSTS | {db.get('HOST') for db in settings.DATABASES.values()}
    real_getaddrinfo = socket.getaddrinfo

    def refuse(*args, **kwargs):
        raise NetworkAccessInTests('tests must not reach the network')

    def getaddrinfo(host, *args, **kwargs):
        if isinstance(host, bytes):
            host = host.decode()
        if host not in allowed_hosts:
            refuse()
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, 'getaddrinfo', getaddrinfo)
    monkeypatch.setattr(socket, 'create_connection', refuse)
    monkeypatch.setattr(http.client.HTTPConnection, 'connect', refuse)
    monkeypatch.setattr(http.client.HTTPSConnection, 'connect', refuse)


class FakeExternalChecks:
    """Stands in for Have I Been Pwned and Debounce, at their HTTP fetch functions (so the
    parsing and the safe failure still run). Set `breached` (passwords) and `disposable`
    (domains); set `pwned_error` / `debounce_error` to an exception to make a check fail.
    `pwned_prefixes` and `debounce_domains` record what was sent."""

    def __init__(self):
        self.breached = set()
        self.disposable = set()
        self.pwned_error = None
        self.debounce_error = None
        self.pwned_prefixes = []
        self.debounce_domains = []

    def pwned_range(self, prefix):
        self.pwned_prefixes.append(prefix)
        if self.pwned_error:
            raise self.pwned_error
        lines = ['00000000000000000000000000000000000:0']  # a padding entry
        for password in self.breached:
            digest = hashlib.sha1(password.encode()).hexdigest().upper()
            if digest[:5] == prefix:
                lines.append(f'{digest[5:]}:42')
        return '\r\n'.join(lines)

    def debounce(self, domain):
        self.debounce_domains.append(domain)
        if self.debounce_error:
            raise self.debounce_error
        return {'disposable': 'true' if domain in self.disposable else 'false'}


@pytest.fixture(autouse=True)
def external_checks(monkeypatch):
    """Every test gets the fakes: no password is breached, no domain disposable."""
    fake = FakeExternalChecks()
    monkeypatch.setattr('accounts.password_validation.fetch_pwned_range', fake.pwned_range)
    monkeypatch.setattr('signup.disposable.fetch_disposable', fake.debounce)
    return fake


class FakeTurnstile:
    """Stands in for Cloudflare's siteverify, at its HTTP fetch function (so the answer's
    parsing and the safe failure still run), as Cloudflare answers for its test secrets:
    the always-fail secret refuses every token, any other accepts it. Set `error` to an
    exception to make siteverify unreachable, or `answer` to force a reply. `calls`
    records what was sent."""

    def __init__(self):
        self.error = None
        self.answer = None
        self.calls = []

    def siteverify(self, data):
        self.calls.append(dict(data))
        if self.error:
            raise self.error
        if self.answer is not None:
            return self.answer
        if data.get('secret') == TURNSTILE_FAIL_SECRET:
            return {'success': False, 'error-codes': ['invalid-input-response']}
        return {'success': True, 'error-codes': [], 'hostname': 'example.com'}


@pytest.fixture(autouse=True)
def turnstile(monkeypatch):
    """Every test gets the fake siteverify: with the default (test) keys it passes."""
    fake = FakeTurnstile()
    monkeypatch.setattr('signup.captcha.post_siteverify', fake.siteverify)
    return fake


@pytest.fixture
def portal_urls(settings):
    """Serve the portal routes (signup, welcome) as with PORTAL_ENABLED=True."""
    settings.ROOT_URLCONF = 'signup.tests.urls_portal'


# --- Shared by several apps' tests ----------------------------------------------------------

@pytest.fixture
def ae_portal(settings):
    """This portal is the ae region's (dev-ae): its profile (company, country map,
    legal documents of its own region) is the ae one."""
    from regions.profiles import get_profile

    settings.REGION = 'ae'
    settings.REGION_PROFILE = get_profile('ae')
    return settings


def publish_site_documents():
    """A published version of each kind of legal document in this instance's region, for
    the kinds that have none (the migration's drafts are the default region's only, and an
    earlier transactional test may have flushed them)."""
    from django.utils import timezone

    from legal.models import LegalDocument

    now = timezone.now()
    for kind in LegalDocument.Kind.values:
        if LegalDocument.objects.current(kind, now) is None:
            LegalDocument.objects.create(
                kind=kind, title_en=f'{kind} (test)', title_es=f'{kind} (prueba)',
                body_en='Test text.', body_es='Texto de prueba.', published_at=now)


@pytest.fixture
def release_jobs(settings, tmp_path):
    """runjob (mytp-core) reads the release's deploy/jobs.conf (no provisioned copy)."""
    settings.MYTP_INSTALLED_JOBS_FILE = tmp_path / 'missing.conf'


@pytest.fixture
def interface_settings(settings):
    """dev (region eu) with its plain test key and admindev as its peer, for the
    configuration snapshot's pull (integration/tests/snapshot_rows.py). Not autouse here:
    catalogue/tests/conftest.py makes it autouse for the catalogue tests, and
    integration's test_config and test_snapshot_tables and regions' test_applier use it
    module-wide (`pytest.mark.usefixtures`). Other modules define their own."""
    import base64

    from django.core.cache import cache

    from integration.tests.snapshot_rows import ADMIN, PORTAL_SEED
    from mytp_core import interface

    settings.SITE = 'dev'
    settings.REGION = 'eu'
    settings.INTERFACE_SIGNING_KEY = f'p1:{base64.b64encode(PORTAL_SEED).decode()}'
    settings.INTERFACE_PEERS = 'admindev=https://admindev.example.test'
    settings.INTERFACE_PEER_KEYS = f'admindev:a1:{ADMIN.public_text}'
    settings.INTERFACE_ADMIN_SITE = ''
    interface.reset_signing_key()
    cache.clear()
    yield settings
    interface.reset_signing_key()
    cache.clear()


@pytest.fixture
def admin(monkeypatch):
    """The fake admin site answering the configuration snapshot
    (integration/tests/snapshot_rows.py: `FakeAdmin`)."""
    from integration.tests.snapshot_rows import FakeAdmin
    from mytp_core import interface

    fake = FakeAdmin()
    monkeypatch.setattr(interface, '_open', fake)
    return fake


class _FakeSentry:
    """sentry_sdk for runjob in tests (`_runjob_with`): active or not, counting the
    captured exceptions and recording the flush."""

    def __init__(self, active):
        self.active = active
        self.exceptions = 0
        self.flushed = False

    def get_client(self):
        return self

    def is_active(self):
        return self.active

    def capture_exception(self):
        self.exceptions += 1

    def flush(self, timeout=None):
        self.flushed = True


def _runjob_with(monkeypatch, sentry):
    """runjob (mytp-core) with `sentry` as its sentry_sdk, the release's jobs.conf, and its
    check-ins recorded; returns the list they are recorded in."""
    from mytp_core import jobs
    from mytp_core.management.commands import runjob

    checkins = []

    def capture_checkin(**kwargs):
        checkins.append(kwargs)
        return 'checkin-1'
    monkeypatch.setattr(runjob, 'sentry_sdk', sentry)
    monkeypatch.setattr(runjob, 'capture_checkin', capture_checkin)
    monkeypatch.setattr(runjob, 'load_jobs', lambda: jobs.load_jobs(jobs.release_jobs_file()))
    return checkins


class _CaptureTransport(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.events = []

    def capture_envelope(self, envelope):
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)


@pytest.fixture
def sentry_capture(monkeypatch):
    """A real Sentry client (via mytp-core's init_sentry, with the portal's settings) whose
    events stay in memory."""
    from django.conf import settings

    from mytp_core import sentry as core_sentry

    transport = _CaptureTransport()
    real_init = sentry_sdk.init
    monkeypatch.setattr(sentry_sdk, 'init',
                        lambda **kwargs: real_init(transport=transport, **kwargs))
    # Restored afterwards.
    monkeypatch.setattr(core_sentry, '_site_sensitive_fields', ())
    monkeypatch.setattr(core_sentry, '_safe_keys', core_sentry.DEFAULT_SAFE_KEYS)
    core_sentry.init_sentry('https://public@o1.ingest.de.sentry.io/2', 'test', 'abc123',
                            ignored_loggers=settings.MYTP_SENTRY_IGNORED_LOGGERS,
                            sensitive_fields=settings.MYTP_SENTRY_SENSITIVE_FIELDS,
                            safe_keys=settings.MYTP_SENTRY_SAFE_KEYS)
    yield transport
    sentry_sdk.get_global_scope().set_client(None)


def _assert_no_secrets(secrets, *texts, show_leak=False):
    """None of `secrets` appears in any of `texts` (each taken as str). With `show_leak`,
    a failure names the secret and the text it leaked into."""
    for text in texts:
        for secret in secrets:
            if show_leak:
                assert secret not in str(text), f'{secret!r} leaked into {text!r}'
            else:
                assert secret not in str(text)


def _env_values(path):
    """The KEY=value lines of an env file (comments and blank lines skipped)."""
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if line.strip() and not line.startswith('#'))


# --- The object storage fakes (storage, and accounts' profile photo) ----------------------
#
# Fake, non-secret bucket settings: a developer's settings.env must never carry real
# bucket keys into a test. Nothing here reaches the network (presigning is local; the S3
# calls go to a botocore Stubber; clamd is a fake on a unix socket).

STORAGE_FAKE_ACCESS_KEY = 'STORAGEACCESSKEYTEST1'
STORAGE_FAKE_SECRET_KEY = 'storage-secret-key-for-tests-only-0123456789'
STORAGE_BUCKET = 'mytp-dev-files'


def use_fake_bucket(settings):
    """The fake bucket's STORAGE_* settings (clamd at a path where nothing listens: a
    test that scans starts `fake_clamd`)."""
    settings.STORAGE_ENDPOINT = 'https://s3.gra.io.cloud.ovh.net'
    settings.STORAGE_REGION = 'gra'
    settings.STORAGE_BUCKET = STORAGE_BUCKET
    settings.STORAGE_ACCESS_KEY = STORAGE_FAKE_ACCESS_KEY
    settings.STORAGE_SECRET_KEY = STORAGE_FAKE_SECRET_KEY
    settings.STORAGE_CORS_ORIGINS = ['https://dev.mytpdso.com']
    settings.STORAGE_ABORT_INCOMPLETE_DAYS = 7
    settings.STORAGE_CLAMD_SOCKET = '/nonexistent/clamd.ctl'
    return settings


@pytest.fixture
def fake_bucket(settings):
    """The fake bucket's settings (autouse in the storage tests)."""
    return use_fake_bucket(settings)


@pytest.fixture
def stubber(monkeypatch):
    """The S3 client every storage call gets, with a botocore Stubber on it."""
    from botocore.stub import Stubber

    from storage import s3

    client = s3.make_client()
    stub = Stubber(client)
    monkeypatch.setattr(s3, 'make_client', lambda **kwargs: client)
    with stub:
        yield stub
    stub.assert_no_pending_responses()


class FakeClamd:
    """clamd on a unix socket in a thread: reads one INSTREAM (or VERSION) per connection
    and answers `reply`. `reply` may be a callable of the received data; `close_after`
    stops reading after that many data bytes, answers and closes (StreamMaxLength);
    `reply=None` closes without an answer."""

    def __init__(self, path):
        import threading

        self.path = path
        self.reply = b'stream: OK\0'
        self.close_after = None
        self._raw = []          # per connection: the raw pieces read, before any answer
        self.data = []          # the data each INSTREAM carried (before its answer)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(5)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.running = True
        self.thread.start()

    def _recv_exact(self, conn, n, raw):
        buf = b''
        while len(buf) < n:
            part = conn.recv(n - len(buf))
            if not part:
                raise ConnectionError('client closed')
            buf += part
        raw.append(buf)
        return buf

    def _serve(self):
        while self.running:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                raw = []
                self._raw.append(raw)
                try:
                    self._handle(conn, raw)
                except (ConnectionError, OSError):
                    pass

    @property
    def received(self):
        """The raw bytes of each connection."""
        return [b''.join(raw) for raw in self._raw]

    def _answer(self, conn, data):
        reply = self.reply(data) if callable(self.reply) else self.reply
        if reply is not None:
            conn.sendall(reply)

    def _handle(self, conn, raw):
        import struct

        command = b''
        while not command.endswith(b'\0'):
            command += self._recv_exact(conn, 1, raw)
        if command == b'zVERSION\0':
            self._answer(conn, b'')
            return
        assert command == b'zINSTREAM\0', command
        data = b''
        while True:
            (length,) = struct.unpack('!L', self._recv_exact(conn, 4, raw))
            if length == 0:
                break
            data += self._recv_exact(conn, length, raw)
            if self.close_after is not None and len(data) > self.close_after:
                self.data.append(data)
                self._answer(conn, data)
                conn.shutdown(socket.SHUT_RDWR)
                return
        self.data.append(data)
        self._answer(conn, data)

    def stop(self):
        self.running = False
        self.server.close()


@pytest.fixture
def fake_clamd(settings):
    """A FakeClamd, and STORAGE_CLAMD_SOCKET pointing at it."""
    import os
    import shutil
    import tempfile

    # A short path: unix socket paths are limited to ~100 bytes (macOS's temp dir is long).
    base = '/tmp' if os.access('/tmp', os.W_OK) else None
    folder = tempfile.mkdtemp(prefix='clamd', dir=base)
    fake = FakeClamd(os.path.join(folder, 'clamd.ctl'))
    settings.STORAGE_CLAMD_SOCKET = fake.path
    yield fake
    fake.stop()
    shutil.rmtree(folder, ignore_errors=True)


def s3_body(data):
    """A GetObject answer carrying `data`."""
    import io

    from botocore.response import StreamingBody

    return {'Body': StreamingBody(io.BytesIO(data), len(data)), 'ContentLength': len(data)}


def expect_get(stubber, obj, data):
    """The scan's (or the content check's) GetObject of `quarantine/<uuid>`."""
    stubber.add_response('get_object', s3_body(data),
                         {'Bucket': STORAGE_BUCKET, 'Key': f'quarantine/{obj.pk}'})


def expect_promotion(stubber, obj, *, clean_exists=False):
    """The promotion's HeadObject (and CopyObject) of `clean/<uuid>`, then the quarantine
    object's deletion."""
    clean = {'Bucket': STORAGE_BUCKET, 'Key': f'clean/{obj.pk}'}
    if clean_exists:
        stubber.add_response('head_object', {'ContentLength': obj.size}, clean)
    else:
        stubber.add_client_error('head_object', service_error_code='404', http_status_code=404,
                                 expected_params=clean)
        stubber.add_response('copy_object', {}, {
            **clean, 'CopySource': {'Bucket': STORAGE_BUCKET, 'Key': f'quarantine/{obj.pk}'}})
    stubber.add_response('delete_object', {},
                         {'Bucket': STORAGE_BUCKET, 'Key': f'quarantine/{obj.pk}'})


def expect_rejection(stubber, obj):
    """A rejection's (or a deletion's) DeleteObject of both keys."""
    for key in (f'quarantine/{obj.pk}', f'clean/{obj.pk}'):
        stubber.add_response('delete_object', {}, {'Bucket': STORAGE_BUCKET, 'Key': key})
