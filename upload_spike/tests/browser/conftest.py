"""The upload spike's browser test (marker `browser`): skipped where Playwright's Chromium
was not downloaded, with plain HTTP settings for live_server (as the other apps)."""
import os

import pytest

# Playwright's sync API runs an event loop in this thread, so Django's ORM would otherwise
# refuse to run here.
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')


def _chromium_missing():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return 'Playwright is not installed'
    with sync_playwright() as playwright:
        path = playwright.chromium.executable_path
    if not path or not os.path.exists(path):
        return 'Chromium for Playwright is not installed'
    return None


def pytest_collection_modifyitems(config, items):
    here = os.path.dirname(__file__)
    browser_items = [item for item in items if item.get_closest_marker('browser')
                     and str(item.path).startswith(here)]
    if not browser_items:
        return
    reason = _chromium_missing()
    if reason:
        for item in browser_items:
            item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(autouse=True)
def plain_http(settings):
    """live_server speaks plain HTTP: no Secure cookies, no HTTPS redirect."""
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    settings.SECURE_SSL_REDIRECT = False
