"""The case wizard's browser tests (marker `browser`): skipped where Playwright's Chromium was
not downloaded, with plain HTTP settings for live_server (as the other apps')."""
from storage.tests.browser.conftest import (_chromium_missing, plain_http)  # noqa: F401
import os

import pytest

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')


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
