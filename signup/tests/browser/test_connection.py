"""Signup entry 8, in Chromium (Playwright) against live_server: the "Connection lost" and
"Retry" rows of the plan's I/O matrix. A boosted submit that cannot be sent shows the
banner, keeps what was typed and stays on the page; the connection coming back (the
`online` event) or "Try again" sends the same form once.

Skipped when pytest-playwright (here) or its Chromium (conftest.py) is not installed (CI installs both:
.github/workflows/ci.yml). The end-to-end suite (2.14) reuses this setup.
"""
import os
import re

import pytest

pytest.importorskip('pytest_playwright')

from playwright.sync_api import expect  # noqa: E402

from django.utils import timezone  # noqa: E402

from eventlog.models import Event  # noqa: E402
from legal.models import LegalDocument  # noqa: E402
from signup.models import PendingRegistration  # noqa: E402

from ..test_wizard import EMAIL_ADDRESS, FIRST_NAME, LAST_NAME, MOBILE, PASSWORD  # noqa: E402

# Playwright's sync API runs an event loop in this thread, so Django's ORM (the test's
# own queries, the database teardown) would otherwise refuse to run here.
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [
    pytest.mark.browser,
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
    pytest.mark.usefixtures('portal_urls'),
]

BANNER = '[data-connection-lost]'


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
    now = timezone.now()
    for kind in LegalDocument.Kind.values:
        if not LegalDocument.objects.filter(kind=kind, published_at__lte=now).exists():
            LegalDocument.objects.create(
                kind=kind, title_en=f'{kind} (test)', title_es=f'{kind} (prueba)',
                body_en='Test text.', body_es='Texto de prueba.', published_at=now)


def _saved(step):
    return Event.objects.filter(event='step_saved', step=step).count()


def _fill_step1(page):
    # Leaving the email runs the live check (htmx); let it finish before going offline.
    with page.expect_response(re.compile(r'/signup/check-email/$')):
        page.fill('#id_email', EMAIL_ADDRESS)
        page.press('#id_email', 'Tab')
    page.select_option('#id_title', 'dr')
    page.fill('#id_first_name', FIRST_NAME)
    page.fill('#id_last_name', LAST_NAME)
    page.select_option('#id_country', 'ES')
    page.fill('#id_password1', PASSWORD)
    page.fill('#id_password2', PASSWORD)
    page.select_option('#id_phone_prefix', 'ES')
    page.fill('#id_phone', MOBILE)
    page.select_option('#id_how_found', 'linkedin')


def test_connection_lost_then_back(live_server, context, page):
    page.goto(f'{live_server.url}/en/signup/')
    _fill_step1(page)
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
    assert _saved('account') == 1
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
    assert _saved('legal') == 0

    context.set_offline(False)
    expect(page).to_have_url(re.compile(r'/en/signup/verify-email/$'))
    assert _saved('legal') == 1
    assert _saved('account') == 1
