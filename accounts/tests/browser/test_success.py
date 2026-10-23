"""Epic-login entry 12: the login spec's success signal in Chromium against live_server,
in English and in Spanish, as one journey (helpers.success_journey): a Doctor created by
signup logs in with the password and a code to the email, ticks "Remember me", logs in
next time without a code, logs out, recovers a forgotten password through the mailed link
from signup's recovery link, and logs in with the new one. The records part of the
signal is test_login_policies.py::test_records_after_the_journey."""
import pytest

pytest.importorskip('pytest_playwright')

from playwright.sync_api import expect  # noqa: E402

from .helpers import success_journey  # noqa: E402

pytestmark = [
    pytest.mark.browser,
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
    pytest.mark.usefixtures('portal_urls'),
]


@pytest.mark.parametrize('lang', ['en', 'es'])
def test_success_signal(live_server, page, mailoutbox, clock, settings, lang):
    """Success signal (spec), CAP-1, CAP-2, CAP-6, CAP-7, CAP-8: the whole journey in
    `lang`, its pages and its reset mail in that language; it ends logged in on welcome,
    which a reload keeps."""
    settings.SITE_URL = live_server.url
    success_journey(page, live_server, mailoutbox, clock, lang)

    expect(page.locator('html')).to_have_attribute('lang', lang)
    page.reload()
    expect(page).to_have_url(f'{live_server.url}/{lang}/welcome/')
    assert len(mailoutbox) == 1
