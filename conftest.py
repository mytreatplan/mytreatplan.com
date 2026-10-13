import hashlib
import http.client
import socket

import pytest

# Fixed, non-secret field keys for tests (the same as in .github/workflows/ci.yml). They
# replace whatever a developer's settings.env holds, so tests never touch real keys.
# base64 of b'test-only-field-key-not-secret!!' and b'test-only-blind-index-key-32byte'.
TEST_FIELD_ENCRYPTION_KEYS = 'test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE='
TEST_BLIND_INDEX_KEY = 'dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU='

# The external checks' endpoints in tests: hosts that cannot exist (RFC 2606), so even a
# call that slipped past the guard below could not reach Have I Been Pwned or Debounce.
TEST_PWNED_PASSWORDS_API_URL = 'https://pwned.test.invalid/range/'
TEST_DISPOSABLE_EMAIL_API_URL = 'https://disposable.test.invalid/'


def pytest_configure(config):
    from django.conf import settings

    settings.FIELD_ENCRYPTION_KEYS = TEST_FIELD_ENCRYPTION_KEYS
    settings.BLIND_INDEX_KEY = TEST_BLIND_INDEX_KEY
    settings.FIELD_KEY_PROVIDER = 'accounts.crypto.EnvKeyProvider'
    settings.PWNED_PASSWORDS_API_URL = TEST_PWNED_PASSWORDS_API_URL
    settings.DISPOSABLE_EMAIL_API_URL = TEST_DISPOSABLE_EMAIL_API_URL


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
    monkeypatch.setattr('signup.checks.fetch_disposable', fake.debounce)
    return fake


@pytest.fixture
def portal_urls(settings):
    """Serve the portal routes (signup, welcome) as with PORTAL_ENABLED=True."""
    settings.ROOT_URLCONF = 'signup.tests.urls_portal'
