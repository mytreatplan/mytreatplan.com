import pytest


@pytest.fixture
def portal_urls(settings):
    """Serve the portal routes (signup, welcome) as with PORTAL_ENABLED=True."""
    settings.ROOT_URLCONF = 'signup.tests.urls_portal'
