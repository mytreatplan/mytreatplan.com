"""The region suggestion bar (epic-regions entry 5) in Chromium against live_server: with
the staging override header giving SA on the eu profile, the bar shows; "Stay here" comes
back to the same page without the bar, and the next page has none either. Headless
Chromium says "HeadlessChrome" (a bot to the bar), so the context sends a desktop UA."""
import pytest

pytest.importorskip('pytest_playwright')

from playwright.sync_api import expect  # noqa: E402

from integration.tests.snapshot_rows import put_country  # noqa: E402
from regions import geoip  # noqa: E402
from regions.countries import seed_state  # noqa: E402
from regions.profiles import get_profile  # noqa: E402

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

DESKTOP_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36')


@pytest.fixture(autouse=True)
def saudi_visitor(settings, tmp_path, transactional_db):
    # The map's SA row: an earlier transactional test may have flushed the seeded map.
    put_country('SA', seed_state('SA'))
    settings.REGION = 'eu'
    settings.REGION_PROFILE = get_profile('eu')
    settings.REGION_SITE_URLS = {'eu': 'https://eu.example.test', 'ae': 'https://ae.example.test'}
    settings.GEOIP_OVERRIDE_HEADER_ENABLED = True
    settings.GEOIP_DB_PATH = str(tmp_path / 'missing.mmdb')
    geoip.clear_cache()
    yield
    geoip.clear_cache()


@pytest.fixture
def saudi_page(browser):
    context = browser.new_context(user_agent=DESKTOP_UA,
                                  extra_http_headers={'X-MyTP-Geo-Country': 'SA'})
    yield context.new_page()
    context.close()


def test_stay_here_hides_the_bar(live_server, saudi_page):
    page = saudi_page
    page.goto(f'{live_server.url}/en/privacy/')
    bar = page.locator('[data-region-bar]')
    expect(bar).to_be_visible()
    expect(bar).to_contain_text("It looks like you're in Saudi Arabia.")
    expect(bar.locator('[data-region-go]')).to_have_attribute('href',
                                                              'https://ae.example.test/')

    bar.get_by_role('link', name='Stay here').click()

    expect(page).to_have_url(f'{live_server.url}/en/privacy/')
    expect(page.locator('[data-region-bar]')).to_have_count(0)

    page.goto(f'{live_server.url}/en/')
    expect(page.locator('h1')).to_be_visible()
    expect(page.locator('[data-region-bar]')).to_have_count(0)
