"""The browser tests (marker `browser`) skip, rather than error, where Playwright's
Chromium was not downloaded: checked at collection, before pytest-playwright's
session-scoped `browser` fixture would try to launch it."""
import os

import pytest


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
