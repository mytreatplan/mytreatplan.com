"""Signup entry 8, in Chromium (Playwright) against live_server: the "Connection lost" and
"Retry" rows of the plan's I/O matrix, and the policy row "User loses connectivity"
(registration-failure-policies.md, row 11). A boosted submit that cannot be sent shows the
banner, keeps what was typed and stays on the page; the connection coming back (the
`online` event) or "Try again" sends the same form once.

Skipped when pytest-playwright (here) or its Chromium (conftest.py) is not installed (CI
installs both: .github/workflows/ci.yml). The shared setup is in conftest.py and
helpers.py.
"""
import re

import pytest

pytest.importorskip('pytest_playwright')

from playwright.sync_api import expect  # noqa: E402

from signup.models import PendingRegistration  # noqa: E402

from ..helpers import FIRST_NAME, PASSWORD  # noqa: E402
from .helpers import fill_step1, saved  # noqa: E402

pytestmark = [
    pytest.mark.browser,
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
    pytest.mark.usefixtures('portal_urls'),
]

BANNER = '[data-connection-lost]'


def test_connection_lost_then_back(live_server, context, page):
    """Policy row 11, "User loses connectivity": the open form keeps its input, the
    "connection lost" banner shows, and the submit is sent again when back online."""
    page.goto(f'{live_server.url}/en/signup/')
    fill_step1(page)
    banner = page.locator(BANNER)
    expect(banner).to_be_hidden()

    # Connection lost: the banner, the input kept, no navigation, nothing saved.
    context.set_offline(True)
    page.get_by_role('button', name='Continue').click()
    expect(banner).to_be_visible()
    expect(banner).to_contain_text(
        "Connection lost. We'll send your data again when you're back online.")
    expect(page).to_have_url(re.compile(r'/en/signup/$'))
    expect(page.locator('#id_first_name')).to_have_value(FIRST_NAME)
    expect(page.locator('#id_password1')).to_have_value(PASSWORD)
    assert not PendingRegistration.objects.exists()

    # Retry: the connection back (`online`) sends the same form once; the next step.
    context.set_offline(False)
    expect(page).to_have_url(re.compile(r'/en/signup/legal/$'))
    expect(page.locator('h1')).to_be_visible()
    expect(page.locator(BANNER)).to_be_hidden()
    assert saved('account') == 1
    assert PendingRegistration.objects.get().step == 'legal'

    # The legal step, by "Try again": offline it fails once more (the banner again), then
    # online the form goes through, once.
    page.check('#id_terms')
    page.check('#id_conditions')
    context.set_offline(True)
    page.get_by_role('button', name='Continue').click()
    expect(page.locator(BANNER)).to_be_visible()
    with page.expect_event('requestfailed'):
        page.locator('[data-connection-retry]').click()
    expect(page.locator(BANNER)).to_be_visible()
    expect(page.locator('#id_terms')).to_be_checked()
    assert saved('legal') == 0

    context.set_offline(False)
    expect(page).to_have_url(re.compile(r'/en/signup/verify-email/$'))
    assert saved('legal') == 1
    assert saved('account') == 1
