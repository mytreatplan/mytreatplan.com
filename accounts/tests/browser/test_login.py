"""Login tracer bullet (epic-login entry 1) in Chromium against live_server: a Doctor
created by signup through the real pages logs out from the nav, logs in through the login
page (the Turnstile widget holds the submit until its token arrives) and lands on the
welcome screen."""
import pytest

pytest.importorskip('pytest_playwright')

from playwright.sync_api import expect  # noqa: E402

from conftest import TURNSTILE_TEST_TOKEN  # noqa: E402
from signup.tests.browser.helpers import (enter_code, mobile_code, press,  # noqa: E402
                                          to_mobile_step)
from signup.forms import BLOCKED_MESSAGE  # noqa: E402
from signup.tests.helpers import EMAIL_ADDRESS, PASSWORD  # noqa: E402

pytestmark = [
    pytest.mark.browser,
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
    pytest.mark.usefixtures('portal_urls'),
]

# Like signup's stub (signup.tests.browser.helpers.TURNSTILE_STUB), but the token comes
# only after a delay, so a submit made before it must wait for it.
SLOW_TURNSTILE_STUB = """
window.turnstile = {
  count: 0,
  render(container, options) {
    setTimeout(() => {
      const input = document.createElement('input')
      input.type = 'hidden'
      input.name = 'cf-turnstile-response'
      input.value = '%s'
      container.appendChild(input)
      if (options && options.callback) options.callback(input.value)
    }, 1500)
    this.count += 1
    return 'widget-' + this.count
  },
  remove() {},
}
""" % TURNSTILE_TEST_TOKEN


def _signup(page, live_server):
    """A whole signup through the pages: the Doctor lands logged in on welcome."""
    to_mobile_step(page, live_server)
    enter_code(page, mobile_code(), 'Create account')
    expect(page).to_have_url(f'{live_server.url}/en/welcome/')


def test_signup_user_logs_out_and_logs_in(live_server, page):
    _signup(page, live_server)

    # Log out from the header's button (a POST form).
    expect(page.get_by_role('link', name='Client portal', exact=True)).to_be_visible()
    press(page, 'Log out')
    expect(page).to_have_url(f'{live_server.url}/en/login/')
    expect(page.get_by_role('status').filter(has_text='You have logged out.')).to_be_visible()
    page.goto(f'{live_server.url}/en/welcome/')
    expect(page).to_have_url(f'{live_server.url}/en/login/?next=/en/welcome/')

    # Log in, the email typed in another case, submitted before the token arrives.
    page.route('https://challenges.cloudflare.com/**', lambda route: route.fulfill(
        status=200, content_type='text/javascript', body=SLOW_TURNSTILE_STUB))
    page.goto(f'{live_server.url}/en/login/')
    page.fill('#id_email', EMAIL_ADDRESS.upper())
    page.fill('#id_password', PASSWORD)
    page.get_by_role('button', name='Log in', exact=True).click()
    expect(page.locator('[data-turnstile-pending]')).to_be_visible()
    # Held, not sent without a token (which would show the blocked page).
    expect(page.get_by_text(str(BLOCKED_MESSAGE))).to_have_count(0)

    expect(page).to_have_url(f'{live_server.url}/en/welcome/')
    expect(page.locator('h1')).to_have_text('Welcome to MyTreatPlan')


def test_wrong_password_shows_the_generic_error(live_server, page):
    _signup(page, live_server)
    press(page, 'Log out')

    page.fill('#id_email', EMAIL_ADDRESS)
    page.fill('#id_password', 'wrong-password-123')
    expect(page.locator('input[name="cf-turnstile-response"]')).to_have_value(
        TURNSTILE_TEST_TOKEN)
    press(page, 'Log in')

    expect(page.get_by_role('alert').filter(
        has_text='The email address or password is not correct.')).to_be_visible()
    expect(page.locator('#id_email')).to_have_value(EMAIL_ADDRESS)
    expect(page.locator('#id_password')).to_have_value('')
