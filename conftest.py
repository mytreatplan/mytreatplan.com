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
def release_jobs(monkeypatch, tmp_path):
    """runjob reads the release's deploy/jobs.conf (no provisioned copy)."""
    from mytp_publicsite import jobs

    monkeypatch.setattr(jobs, 'INSTALLED_JOBS_FILE', tmp_path / 'missing.conf')


def _runjob_with(monkeypatch, sentry):
    """runjob with `sentry` as its sentry_sdk, the release's jobs.conf, and its check-ins
    recorded; returns the list they are recorded in."""
    from mytp_publicsite import jobs
    from mytp_publicsite.management.commands import runjob

    checkins = []

    def capture_checkin(**kwargs):
        checkins.append(kwargs)
        return 'checkin-1'
    monkeypatch.setattr(runjob, 'sentry_sdk', sentry)
    monkeypatch.setattr(runjob, 'capture_checkin', capture_checkin)
    monkeypatch.setattr(runjob, 'load_jobs', lambda: jobs.load_jobs(jobs.JOBS_FILE))
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
    """A real Sentry client (via init_sentry) whose events stay in memory."""
    from mytreatplan import sentry as mytp_sentry

    transport = _CaptureTransport()
    real_init = sentry_sdk.init
    monkeypatch.setattr(sentry_sdk, 'init',
                        lambda **kwargs: real_init(transport=transport, **kwargs))
    mytp_sentry.init_sentry('https://public@o1.ingest.de.sentry.io/2', 'test', 'abc123')
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
