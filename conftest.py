import pytest

# Fixed, non-secret field keys for tests (the same as in .github/workflows/ci.yml). They
# replace whatever a developer's settings.env holds, so tests never touch real keys.
# base64 of b'test-only-field-key-not-secret!!' and b'test-only-blind-index-key-32byte'.
TEST_FIELD_ENCRYPTION_KEYS = 'test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE='
TEST_BLIND_INDEX_KEY = 'dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU='


def pytest_configure(config):
    from django.conf import settings

    settings.FIELD_ENCRYPTION_KEYS = TEST_FIELD_ENCRYPTION_KEYS
    settings.BLIND_INDEX_KEY = TEST_BLIND_INDEX_KEY
    settings.FIELD_KEY_PROVIDER = 'accounts.crypto.EnvKeyProvider'


@pytest.fixture
def portal_urls(settings):
    """Serve the portal routes (signup, welcome) as with PORTAL_ENABLED=True."""
    settings.ROOT_URLCONF = 'signup.tests.urls_portal'
