"""The browser tests' shared helpers: the Turnstile stub, and the person's actions on the
real pages (fill step 1, press a button, type a code). Codes come from the fake provider
(signup.tests.helpers._current_code), never from the pages."""
import re

from playwright.sync_api import expect

from conftest import TURNSTILE_TEST_TOKEN
from eventlog.models import Event
from signup.verification import EMAIL, SMS

from ..helpers import (EMAIL_ADDRESS, FIRST_NAME, LAST_NAME, MOBILE, MOBILE_E164, NORMALIZED,
                       PASSWORD, _current_code)

# Stands in for Cloudflare's Turnstile script (signup entry 9) in Chromium: its explicit
# render puts the test token in the form and calls the widget's callback with it, as the
# always-pass test site key would. The test never depends on challenges.cloudflare.com;
# siteverify is faked in this process by the autouse `turnstile` fixture (conftest.py),
# which live_server's thread shares.
TURNSTILE_STUB = """
window.turnstile = {
  count: 0,
  render(container, options) {
    const token = '%s'
    const input = document.createElement('input')
    input.type = 'hidden'
    input.name = 'cf-turnstile-response'
    input.value = token
    container.appendChild(input)
    if (options && options.callback) setTimeout(() => options.callback(token), 0)
    this.count += 1
    return 'widget-' + this.count
  },
  remove() {},
}
""" % TURNSTILE_TEST_TOKEN

CHECK_EMAIL = re.compile(r'/signup/check-email/$')


def route_turnstile(context):
    """Serve TURNSTILE_STUB for Cloudflare's script in this browser context."""
    context.route('https://challenges.cloudflare.com/**', lambda route: route.fulfill(
        status=200, content_type='text/javascript', body=TURNSTILE_STUB))


def saved(step):
    """How many times the step was saved (event log)."""
    return Event.objects.filter(event='step_saved', step=step).count()


def url(lang, path=''):
    """A signup URL pattern, e.g. url('en', 'legal/') matches /en/signup/legal/."""
    return re.compile(rf'/{lang}/signup/{path}$')


def wait_for_token(page):
    """The CAPTCHA widget has put its token in step 1's form."""
    expect(page.locator('input[name="cf-turnstile-response"]')).to_have_value(
        TURNSTILE_TEST_TOKEN)


def fill_email(page, email):
    """Type the email and leave the field: the live check (htmx) runs; wait for it."""
    with page.expect_response(CHECK_EMAIL):
        page.fill('#id_email', email)
        page.press('#id_email', 'Tab')


def fill_step1(page, email=EMAIL_ADDRESS, phone=MOBILE, password=PASSWORD):
    """Fill a valid step 1 (as typed by the person), once the widget has its token."""
    wait_for_token(page)
    fill_email(page, email)
    page.select_option('#id_title', 'dr')
    page.fill('#id_first_name', FIRST_NAME)
    page.fill('#id_last_name', LAST_NAME)
    page.select_option('#id_country', 'ES')
    fill_passwords(page, password)
    page.select_option('#id_phone_prefix', 'ES')
    page.fill('#id_phone', phone)
    page.select_option('#id_how_found', 'linkedin')


def fill_passwords(page, password=PASSWORD):
    page.fill('#id_password1', password)
    page.fill('#id_password2', password)


def new_page(page, action):
    """Run `action` (a click) and wait until the next page has replaced this one, whether
    by an htmx swap (a boosted form) or a full load: every page has an h1, and the old
    ones are marked first. So the next step never acts on the page being replaced (the
    code pages share their field names)."""
    page.evaluate("() => document.querySelectorAll('h1').forEach((h) => { h.dataset.stale = '1' })")
    action()
    expect(page.locator('h1[data-stale]')).to_have_count(0)


def press(page, name):
    """Click the button with exactly this name and wait for the page it leads to."""
    new_page(page, page.get_by_role('button', name=name, exact=True).click)


def follow(page, name):
    """Click the link with exactly this name and wait for the page it leads to."""
    new_page(page, page.get_by_role('link', name=name, exact=True).click)


def accept_legal(page, button='Continue'):
    """Tick the Terms and the Conditions (only) and continue."""
    page.check('#id_terms')
    page.check('#id_conditions')
    press(page, button)


def enter_code(page, code, button='Continue'):
    page.fill('#id_code', code)
    press(page, button)


def email_code(email=NORMALIZED, purpose='signup'):
    return _current_code(EMAIL, email, purpose)


def mobile_code(phone=MOBILE_E164):
    # A mobile has one code, whichever channel (WhatsApp or SMS) carried it.
    return _current_code(SMS, phone)


def to_email_step(page, live_server, lang='en', **step1):
    """Steps 1 and 2 through the pages: the email code is sent."""
    page.goto(f'{live_server.url}/{lang}/signup/')
    fill_step1(page, **step1)
    press(page, 'Continue' if lang == 'en' else 'Continuar')
    expect(page).to_have_url(url(lang, 'legal/'))
    accept_legal(page, 'Continue' if lang == 'en' else 'Continuar')
    expect(page).to_have_url(url(lang, 'verify-email/'))


def to_mobile_step(page, live_server, lang='en', **step1):
    """Steps 1, 2 and 3 through the pages: the mobile code is sent."""
    to_email_step(page, live_server, lang=lang, **step1)
    enter_code(page, email_code(step1.get('email', EMAIL_ADDRESS).lower()),
               'Continue' if lang == 'en' else 'Continuar')
    expect(page).to_have_url(url(lang, 'verify-mobile/'))
