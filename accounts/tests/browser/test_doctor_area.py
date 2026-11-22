"""The doctor area (epic-doctor-profile entry 1) in Chromium against live_server: a Doctor
created by signup logs in and lands on the home, opens an area ("coming soon") from the
header and comes back; on a narrow screen the header's links sit behind the menu button."""
import pytest

pytest.importorskip('pytest_playwright')

from playwright.sync_api import expect  # noqa: E402

from signup.tests.browser.helpers import follow, new_page  # noqa: E402
from signup.tests.helpers import FIRST_NAME  # noqa: E402

from .helpers import at, log_in, log_out, sign_up  # noqa: E402

pytestmark = [
    pytest.mark.browser,
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
    pytest.mark.usefixtures('portal_urls'),
]


def test_login_lands_on_home_and_an_area_leads_back(live_server, page):
    sign_up(page, live_server)
    log_out(page, live_server)
    log_in(page, live_server)  # lands on the home
    expect(page.locator('h1')).to_have_text(f'Hello, {FIRST_NAME}')
    expect(page.locator('[data-avatar]')).to_have_text('AG')

    follow(page, 'patients')
    at(page, live_server, 'en', 'patients/')
    expect(page.locator('h1')).to_have_text('Patients')
    expect(page.locator('[data-patient-form]')).to_be_visible()  # built in 9.1

    follow(page, 'Back to home')
    at(page, live_server, 'en', 'home/')

    # A tile and an icon lead to their areas too.
    follow(page, 'Planning Services Expert planning and support at every step')
    at(page, live_server, 'en', 'planning-services/')
    new_page(page, page.get_by_role('link', name='Cart', exact=True).click)
    at(page, live_server, 'en', 'cart/')
    expect(page.locator('h1')).to_have_text('Cart')


def test_narrow_screen_header_uses_the_menu_button(live_server, page):
    sign_up(page, live_server)
    log_out(page, live_server)
    log_in(page, live_server)
    page.set_viewport_size({'width': 390, 'height': 844})

    toggle = page.locator('#site-menu-toggle')
    expect(toggle).to_be_visible()
    support = page.get_by_role('link', name='support', exact=True)
    expect(support).to_be_hidden()

    toggle.click()
    expect(toggle).to_have_attribute('aria-expanded', 'true')
    expect(support).to_be_visible()
    expect(page.get_by_role('button', name='Log out', exact=True)).to_be_visible()
    new_page(page, support.click)
    at(page, live_server, 'en', 'support/')
    expect(page.locator('h1')).to_have_text('Support')

    # The tiles stack in one column.
    page.goto(f'{live_server.url}/en/home/')
    boxes = [page.locator(f'[data-tile="{name}"]').bounding_box()
             for name in ('virtuaortho', 'planning_services')]
    assert boxes[1]['y'] >= boxes[0]['y'] + boxes[0]['height']
    assert abs(boxes[1]['x'] - boxes[0]['x']) < 1
