"""The password reset (epic-login entry 10) in Chromium against live_server: a Doctor created
by signup asks for the link on the request page, opens its fragment link from the mail
(sent by the real background thread to the test outbox), sets a new password with the strength meter, lands
on the login page with the message, and logs in with the new password (and the code)."""
import re

import pytest

pytest.importorskip('pytest_playwright')

from playwright.sync_api import expect  # noqa: E402

from signup.tests.browser.helpers import (email_code, enter_code, mobile_code,  # noqa: E402
                                          new_page, press, to_mobile_step, wait_for_token)
from signup.tests.helpers import EMAIL_ADDRESS  # noqa: E402
from signup.verification import PURPOSE_LOGIN  # noqa: E402

pytestmark = [
    pytest.mark.browser,
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
    pytest.mark.usefixtures('portal_urls'),
]

NEW_PASSWORD = 'amber-glacier-meadow-77'


def _wait_for_mail(page, mailoutbox):
    for _ in range(100):
        if mailoutbox:
            return mailoutbox[-1]
        page.wait_for_timeout(100)
    raise AssertionError('no reset mail arrived')


def test_reset_the_password_and_log_in_with_it(live_server, page, mailoutbox, settings):
    settings.SITE_URL = live_server.url
    to_mobile_step(page, live_server)
    enter_code(page, mobile_code(), 'Create account')
    expect(page).to_have_url(f'{live_server.url}/en/welcome/')
    press(page, 'Log out')

    # The request page, then the one answer.
    page.goto(f'{live_server.url}/en/password-reset/')
    expect(page.locator('h1')).to_have_text('Reset your password')
    wait_for_token(page)
    page.fill('#id_email', EMAIL_ADDRESS.upper())
    press(page, 'Send the link')
    expect(page).to_have_url(f'{live_server.url}/en/password-reset/sent/')
    expect(page.locator('h1')).to_have_text('Check your email')

    # The link from the mail (the uid and token in its fragment): the page's script posts
    # the fragment, and the new-password page follows, with neither in its URL.
    message = _wait_for_mail(page, mailoutbox)
    link = re.search(rf'{re.escape(live_server.url)}/en/reset/#\S+', message.body).group(0)
    with page.expect_request(lambda request: request.method == 'POST'
                             and request.url == f'{live_server.url}/en/reset/') as posted:
        page.goto(link)
    fragment = link.partition('#')[2]
    assert f'link={fragment}' in posted.value.post_data  # the POST body, not the URL
    expect(page).to_have_url(f'{live_server.url}/en/password-reset/new/')
    expect(page.locator('h1')).to_have_text('Choose a new password')
    page.fill('#id_password1', NEW_PASSWORD)
    expect(page.locator('#password-strength')).to_be_visible()
    expect(page.locator('#password-strength-label')).to_contain_text('Strength:')
    page.fill('#id_password2', NEW_PASSWORD)
    press(page, 'Change password')

    # Not logged in: the login page with the message; the new password logs in.
    expect(page).to_have_url(f'{live_server.url}/en/login/')
    expect(page.get_by_role('status').filter(
        has_text='Your password has been changed. Please log in.')).to_be_visible()
    page.fill('#id_email', EMAIL_ADDRESS)
    page.fill('#id_password', NEW_PASSWORD)
    wait_for_token(page)
    press(page, 'Log in')
    expect(page).to_have_url(f'{live_server.url}/en/login/verify/')
    new_page(page, page.locator('button[value="email"]').click)
    enter_code(page, email_code(purpose=PURPOSE_LOGIN), 'Log in')
    expect(page).to_have_url(f'{live_server.url}/en/welcome/')

    # The used link is no longer valid.
    page.goto(link)
    expect(page.locator('h1')).to_have_text('This link is no longer valid')
    expect(page).to_have_url(f'{live_server.url}/en/reset/')  # the fragment cleared
