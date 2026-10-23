"""Epic-login entry 12: the login capabilities that a browser shows differently from the
Django client tests, in Chromium against live_server, through the real pages, with the
fake verification provider. One test per row of the story's matrix; each docstring
names its capability. The map of capabilities to tests is in this package's __init__.py.

Time moves in-process (no sleeping), through fixtures (accounts/tests/conftest.py) whose
patches live_server's thread shares: `lock_clock` moves the `_now` of signup.limits,
signup.verification (the fake provider), accounts.views and accounts.login_failures;
`idle_clock` that of signup.limits, signup.verification and accounts.sessions. The
counters, keys and event details are the client tests' (test_lockout.py,
test_sessions.py, test_login_records.py, test_page_records.py); these check what the
person sees, and the database for the activity records.
"""
import pytest

pytest.importorskip('pytest_playwright')

from django.contrib.auth import get_user_model  # noqa: E402
from django.utils import timezone  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from accounts.login_failures import LOCK_SECONDS, LOCK_THRESHOLD  # noqa: E402
from accounts.sessions import IDLE_LIMIT  # noqa: E402
from eventlog.models import Attempt, Event  # noqa: E402
from signup.forms import BLOCKED_MESSAGE  # noqa: E402
from signup.tests.browser.helpers import (enter_code, follow,  # noqa: E402
                                          route_turnstile)
from signup.tests.helpers import NORMALIZED, _wrong  # noqa: E402
from signup.verification import FAKE_NO_WHATSAPP_NUMBER  # noqa: E402

from ..helpers import WRONG_PASSWORD, _right_code  # noqa: E402
from .helpers import (at, choose, log_in, log_out, sign_up, status,  # noqa: E402
                      submit_password, success_journey)

pytestmark = [
    pytest.mark.browser,
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
    pytest.mark.usefixtures('portal_urls'),
]

User = get_user_model()

WRONG_CODE = 'This code is not correct. Check it, or send a new one.'
WRONG_LOGIN = 'The email address or password is not correct.'
SMS_FALLBACK = 'WhatsApp is not available for this number, so we sent the code by SMS.'
REPLACED = 'You were logged out because your account was logged in on another device.'
IDLE = 'Your session ended after 15 minutes without activity. Please log in again.'


def _alert(page, text):
    return page.get_by_role('alert').filter(has_text=text)


# --- The second factor (CAP-7) ------------------------------------------------------------

def test_mobile_code_by_sms_without_whatsapp(live_server, page):
    """CAP-7, "the code by mobile": the Doctor chooses the mobile; WhatsApp cannot reach
    the number, so the code goes by SMS and the page says so; that code logs in."""
    sign_up(page, live_server, phone=FAKE_NO_WHATSAPP_NUMBER)
    log_out(page, live_server)

    submit_password(page)
    at(page, live_server, 'en', 'login/verify/')
    expect(page.locator('button[value="mobile"]')).to_contain_text(
        '+34 ••• ••• 001')
    choose(page, 'mobile')
    at(page, live_server, 'en', 'login/code/')
    expect(page.locator('[data-sms-fallback]')).to_have_text(SMS_FALLBACK)
    expect(page.locator('[data-sent="sms"]')).to_contain_text('by SMS to')
    enter_code(page, _right_code('mobile', phone=FAKE_NO_WHATSAPP_NUMBER), 'Log in')
    at(page, live_server, 'en', 'welcome/')
    expect(page.locator('h1')).to_have_text('Welcome to MyTreatPlan')


def test_wrong_code_then_the_right_one(live_server, page):
    """CAP-7, "only the right code completes the login": a wrong code is refused on the
    code page, then the right one lands on welcome."""
    sign_up(page, live_server)
    log_out(page, live_server)

    submit_password(page)
    choose(page, 'email')
    at(page, live_server, 'en', 'login/code/')
    enter_code(page, _wrong(_right_code()), 'Log in')
    at(page, live_server, 'en', 'login/code/')
    expect(page.get_by_text(WRONG_CODE)).to_be_visible()

    enter_code(page, _right_code(), 'Log in')
    at(page, live_server, 'en', 'welcome/')
    expect(page.locator('h1')).to_have_text('Welcome to MyTreatPlan')


# --- One session and the idle limit (CAP-9) -----------------------------------------------

def test_single_session_across_two_browsers(live_server, browser, page):
    """CAP-9, "one active session": logged in in browser A, the Doctor logs in in browser
    B; A's next page is the login page with the message, and B stays logged in."""
    sign_up(page, live_server)  # A: logged in by signup
    log_out(page, live_server)
    log_in(page, live_server)

    other = browser.new_context()
    try:
        route_turnstile(other)
        b = other.new_page()
        b.goto(f'{live_server.url}/en/login/')
        # The code to the mobile: the email's login code went to A a moment ago (its
        # one-minute resend cooldown runs).
        log_in(b, live_server, contact='mobile')

        page.reload()
        at(page, live_server, 'en', 'login/?next=/en/welcome/')
        expect(status(page, REPLACED)).to_be_visible()
        page.goto(f'{live_server.url}/en/welcome/')  # the message shows once
        at(page, live_server, 'en', 'login/?next=/en/welcome/')
        expect(status(page, REPLACED)).to_have_count(0)

        b.reload()
        at(b, live_server, 'en', 'welcome/')
    finally:
        other.close()


def test_idle_session_ends(live_server, page, idle_clock):
    """CAP-9, "an idle session ends": 15 minutes and a second without a request (the
    clock moved), the next page is the login page with the idle message."""
    sign_up(page, live_server)
    log_out(page, live_server)
    log_in(page, live_server)

    idle_clock.advance(IDLE_LIMIT - 1)
    page.reload()  # still in, and the activity clock starts again
    at(page, live_server, 'en', 'welcome/')

    idle_clock.advance(IDLE_LIMIT + 1)
    page.reload()
    at(page, live_server, 'en', 'login/?next=/en/welcome/')
    expect(status(page, IDLE)).to_be_visible()


# --- The lockout (CAP-5) --------------------------------------------------------------------

def test_lockout_after_five_wrong_passwords(live_server, page, lock_clock):
    """CAP-5, "5 wrong passwords lock the account for 10 minutes": the first four get the
    login error, the fifth the block message, which the right password then gets too;
    10 minutes later (the clock moved) the right password and the code log in."""
    sign_up(page, live_server)
    log_out(page, live_server)

    for _attempt in range(LOCK_THRESHOLD - 1):
        submit_password(page, password=WRONG_PASSWORD)
        expect(_alert(page, WRONG_LOGIN)).to_be_visible()
    submit_password(page, password=WRONG_PASSWORD)
    expect(_alert(page, str(BLOCKED_MESSAGE))).to_be_visible()

    submit_password(page)  # the right password, refused while locked
    at(page, live_server, 'en', 'login/')
    expect(_alert(page, str(BLOCKED_MESSAGE))).to_be_visible()
    expect(page.locator('#id_password')).to_have_value('')

    lock_clock.advance(LOCK_SECONDS)
    log_in(page, live_server)


# --- The records (CAP-3, CAP-4) -----------------------------------------------------------

def test_records_after_the_journey(live_server, page, mailoutbox, clock, settings):
    """CAP-3 and CAP-4, "logins and the pages opened are on record": after the English
    success journey the account has its login records (with this browser's IP and
    User-Agent) and page records of the welcome page."""
    settings.SITE_URL = live_server.url
    started = timezone.now()
    success_journey(page, live_server, mailoutbox, clock)
    ended = timezone.now()
    agent = page.evaluate('() => navigator.userAgent')
    account_id = User.objects.by_email(NORMALIZED).get().pk

    logins = list(Event.objects.filter(attempt__kind=Attempt.LOGIN, event='login'))
    # The code, the remembered device, and the code again after the reset.
    assert len(logins) == 3
    for event in logins:
        assert event.attempt.account_id == account_id
        assert started <= event.created_at <= ended
        assert event.data['ip'] == '127.0.0.1'
        assert event.data['user_agent'] == agent

    pages = Event.objects.filter(attempt__kind=Attempt.PAGE, step='welcome',
                                 attempt__account_id=account_id)
    # Signup's landing, then each of the three logins.
    assert pages.count() == 4


# --- The way to the reset (CAP-2) ---------------------------------------------------------

def test_forgot_password_link(live_server, page):
    """CAP-2, the request page from the login page: "Forgot your password?" opens it."""
    page.goto(f'{live_server.url}/en/login/')
    follow(page, 'Forgot your password?')
    at(page, live_server, 'en', 'password-reset/')
    expect(page.locator('h1')).to_have_text('Reset your password')
