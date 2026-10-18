"""The browser tests' setup (marker `browser`).

They skip, rather than error, where Playwright's Chromium was not downloaded: checked at
collection, before pytest-playwright's session-scoped `browser` fixture would try to
launch it. Every browser test gets plain HTTP settings for live_server, published legal
documents and the Turnstile stub (helpers.TURNSTILE_STUB) in its browser context.
"""
import os

import pytest

# Playwright's sync API runs an event loop in this thread, so Django's ORM (the test's
# own queries, the database teardown) would otherwise refuse to run here.
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')


def _chromium_missing():
    """Why Chromium cannot run here, or None."""
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
    browser_items = [item for item in items if item.get_closest_marker('browser')]
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


@pytest.fixture(autouse=True)
def legal_documents():
    """A published version of each legal document. The migration's drafts may be gone:
    an earlier transactional test flushes the tables, and serialized_rollback restores
    nothing when the test database was not serialized."""
    from django.utils import timezone

    from legal.models import LegalDocument

    now = timezone.now()
    for kind in LegalDocument.Kind.values:
        if not LegalDocument.objects.filter(kind=kind, published_at__lte=now).exists():
            LegalDocument.objects.create(
                kind=kind, title_en=f'{kind} (test)', title_es=f'{kind} (prueba)',
                body_en='Test text.', body_es='Texto de prueba.', published_at=now)


@pytest.fixture(autouse=True)
def turnstile_script(context):
    """The Turnstile stub in the test's own browser context (another context a test
    opens gets it through helpers.route_turnstile)."""
    from .helpers import route_turnstile

    route_turnstile(context)
