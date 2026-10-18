"""Signup entry 14: every row of registration-failure-policies.md (CAP-11) in Chromium
against live_server, through the real pages, with the fake verification provider. One
test per row (row 11, "User loses connectivity", is test_connection.py); each docstring
names its row. The map of rows to tests is in this package's __init__.py.

Time is moved in-process: the `clock` fixture (the resend limits and the fake
provider's codes; plus the views' idle clock for row 7), or backdated rows and the
retention commands (row 2). The rows' event log details and limit internals are the
Django client tests' (test_cancel_idle.py, test_duplicates.py, test_mobile_code.py,
test_abuse.py, test_retention.py); these check what the person sees, and the store or
the log where the policy is about data.
"""
import io
from datetime import timedelta

import pytest

pytest.importorskip('pytest_playwright')

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.http import HttpResponse  # noqa: E402
from django.urls import path  # noqa: E402
from django.utils import timezone  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from conftest import TURNSTILE_FAIL_SECRET, TURNSTILE_PASS_SECRET  # noqa: E402
from eventlog.models import Attempt, Event  # noqa: E402
from signup import limits  # noqa: E402
from signup.models import ACTIVE_FOR, PendingRegistration  # noqa: E402
from signup.tests import urls_portal  # noqa: E402
from signup.verification import (FAKE_NO_WHATSAPP_NUMBER,  # noqa: E402
                                 FAKE_SEND_FAILS_NUMBER, PURPOSE_RESUME)

from ..helpers import (EMAIL_ADDRESS, FIRST_NAME, LAST_NAME, MOBILE, MOBILE_E164,  # noqa: E402
                       NORMALIZED, PASSWORD, _legal, _step1, _wrong)
from .helpers import (email_code, enter_code, fill_email, fill_passwords,  # noqa: E402
                      fill_step1, follow, mobile_code, press, route_turnstile,
                      to_email_step, to_mobile_step, url, wait_for_token)

pytestmark = [
    pytest.mark.browser,
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
    pytest.mark.usefixtures('portal_urls'),
]

User = get_user_model()

# A URLconf with a `password_reset` route, as epic-login will add: the recovery note then
# shows its link (signup/_recovery_note.html).
urlpatterns = list(urls_portal.urlpatterns) + [
    path('password-reset/', lambda request: HttpResponse(), name='password_reset'),
]

RECOVERY_NOTE = 'If this account is yours, you can recover access to it'
PENDING_NOTE = 'A registration with this email address is already in progress.'
RESTORED = 'We have restored your registration. Review your details and continue.'
NEW_CODE_SENT = 'We have sent you a new code.'
WRONG_CODE = 'This code is not correct. Check it, or send a new one.'
NO_LONGER_VALID = 'This code is no longer valid. Send a new code.'
# The saved mobile as step 1 shows it again (national format).
MOBILE_SHOWN = '622 91 83 74'
GENERIC = "We couldn't process your request. Please try again later."

# Another signup's own details (rows 5 and 6), which must not show before the code.
OLD = {'first_name': 'Beatriz', 'last_name': 'Olmedo-Saavedra', 'country': 'PT'}


@pytest.fixture
def recovery_urls(settings, portal_urls):
    settings.ROOT_URLCONF = __name__


def _body(page):
    return page.locator('body')


def _support(page, settings):
    return page.locator(f'[data-support] a[href="mailto:{settings.SIGNUP_SUPPORT_EMAIL}"]')


def _resume_with_code(page, contact=NORMALIZED):
    """The offer's Resume button sends the resume code; enter it: step 1 again, with the
    saved data back."""
    press(page, 'Resume')
    expect(page).to_have_url(url('en', 'resume/'))
    expect(page.locator('h1')).to_have_text('Registration in progress')
    enter_code(page, email_code(contact, PURPOSE_RESUME))
    expect(page).to_have_url(url('en'))
    expect(_body(page)).to_contain_text(RESTORED)


# --- Row 1 ---------------------------------------------------------------------------------

def test_row1_user_cancels(live_server, page):
    """Policy row 1, "User cancels (Cancel Registration)": confirm, then the registration
    store is cleared at once and the form is reset; the log keeps its values."""
    page.goto(f'{live_server.url}/en/signup/')
    fill_step1(page)
    press(page, 'Continue')
    expect(page).to_have_url(url('en', 'legal/'))
    assert PendingRegistration.objects.count() == 1

    follow(page, 'Cancel registration')
    expect(page).to_have_url(url('en', r'cancel/\?from=legal'))
    expect(_body(page)).to_contain_text('Do you want to cancel your registration?')
    assert PendingRegistration.objects.count() == 1  # nothing removed before the confirm

    press(page, 'Cancel registration')
    expect(page).to_have_url(url('en'))
    expect(_body(page)).to_contain_text('Your registration was cancelled.')
    for field in ('#id_email', '#id_first_name', '#id_last_name', '#id_phone'):
        expect(page.locator(field)).to_have_value('')
    assert not PendingRegistration.objects.exists()
    # The log keeps its values until the 30-day anonymization (row 2 runs it).
    assert Attempt.objects.get().outcome == 'cancelled'
    assert Event.objects.get(event='step_saved').data['email'] == NORMALIZED


# --- Row 2 ---------------------------------------------------------------------------------

def test_row2_unfinished_after_24h(live_server, page):
    """Policy row 2, "Registration unfinished after 24h": purged automatically (the hourly
    purge command); the same browser starts afresh. With the success signal: once the
    attempt has ended 30 days ago, the daily retention command anonymizes its log."""
    page.goto(f'{live_server.url}/en/signup/')
    fill_step1(page)
    press(page, 'Continue')
    expect(page).to_have_url(url('en', 'legal/'))

    PendingRegistration.objects.update(
        created_at=timezone.now() - ACTIVE_FOR - timedelta(minutes=1))
    call_command('purge_pending_signups', stdout=io.StringIO())
    assert not PendingRegistration.objects.exists()

    # The browser's next request: a fresh, empty step 1.
    page.reload()
    expect(page).to_have_url(url('en'))
    expect(page.locator('h1')).to_have_text('Create your account')
    expect(page.locator('#id_email')).to_have_value('')
    expect(page.locator('#id_first_name')).to_have_value('')

    # The log keeps its personal values until 30 days after the attempt ended.
    attempt = Attempt.objects.get()
    assert attempt.outcome == 'timeout'
    assert attempt.events.get(event='step_saved').data['email'] == NORMALIZED
    call_command('eventlog_retention', stdout=io.StringIO())
    assert attempt.events.get(event='step_saved').data is not None

    Attempt.objects.filter(pk=attempt.pk).update(
        ended_at=timezone.now() - timedelta(days=30, minutes=1))
    call_command('eventlog_retention', stdout=io.StringIO())
    events = list(attempt.events.all())
    assert events and all(event.data is None for event in events)
    # Only the failure reason and the field type/name stay.
    assert [(e.event, e.reason) for e in events][-1] == ('purged', 'timeout')


# --- Rows 3 and 4 ----------------------------------------------------------------------------

@pytest.mark.usefixtures('recovery_urls')
def test_row3_email_already_registered(live_server, page):
    """Policy row 3, "Email already registered": blocked, with a link to password
    recovery; the email typed in another case."""
    User.objects.create_user(NORMALIZED, 'another-password-123')
    page.goto(f'{live_server.url}/en/signup/')
    fill_step1(page, email=EMAIL_ADDRESS)  # ana.garcia@Example.COM
    # The live check already shows the note when the email is left.
    expect(page.locator('#email-check [data-recovery-note]')).to_contain_text(RECOVERY_NOTE)

    press(page, 'Continue')
    expect(page).to_have_url(url('en'))
    expect(_body(page)).to_contain_text('This email address is already registered.')
    link = page.locator('[data-recovery-link]').first
    expect(link).to_have_text('Recover your password')
    expect(link).to_have_attribute('href', '/password-reset/')
    assert not PendingRegistration.objects.exists()


@pytest.mark.usefixtures('recovery_urls')
def test_row4_mobile_already_registered(live_server, page):
    """Policy row 4, "Mobile already registered": blocked, with a link to password
    recovery; the mobile typed in another format."""
    User.objects.create_user('someone@example.net', 'another-password-123', phone=MOBILE_E164)
    page.goto(f'{live_server.url}/en/signup/')
    fill_step1(page, phone='+34 622-91-83-74')

    press(page, 'Continue')
    expect(page).to_have_url(url('en'))
    expect(_body(page)).to_contain_text('This mobile phone number is already registered.')
    expect(_body(page)).to_contain_text(RECOVERY_NOTE)
    expect(page.locator('[data-recovery-link]')).to_have_attribute('href', '/password-reset/')
    assert not PendingRegistration.objects.exists()


# --- Row 5 ---------------------------------------------------------------------------------

def test_row5_email_in_a_pending_registration(live_server, page, client):
    """Policy row 5, "Email in a pending registration": a warning and the offer to resume
    after a code to that email. A wrong code reveals nothing of the other registration;
    the right one restores it into step 1."""
    # Another signup in progress (another browser) with this email and its own details.
    assert _step1(client, **OLD).status_code == 302
    assert _legal(client).status_code == 302
    other = PendingRegistration.objects.get()

    page.goto(f'{live_server.url}/en/signup/')
    wait_for_token(page)
    fill_email(page, EMAIL_ADDRESS)
    offer = page.locator('[data-resume-offer]')
    expect(offer).to_contain_text(PENDING_NOTE)
    expect(offer).to_contain_text('To continue it, we will send a code to this email address.')

    press(page, 'Resume')
    expect(page).to_have_url(url('en', 'resume/'))
    enter_code(page, _wrong(email_code(NORMALIZED, PURPOSE_RESUME)))
    expect(_body(page)).to_contain_text(WRONG_CODE)
    for value in (*OLD.values(), MOBILE_E164, 'Portugal'):
        expect(_body(page)).not_to_contain_text(value)
    assert PendingRegistration.objects.get() == other

    enter_code(page, email_code(NORMALIZED, PURPOSE_RESUME))
    expect(page).to_have_url(url('en'))
    expect(_body(page)).to_contain_text(RESTORED)
    expect(page.locator('#id_first_name')).to_have_value(OLD['first_name'])
    expect(page.locator('#id_last_name')).to_have_value(OLD['last_name'])
    expect(page.locator('#id_email')).to_have_value(NORMALIZED)
    restored = PendingRegistration.objects.get()
    assert restored.pk != other.pk and restored.email_verified_at is not None


# --- Row 6 ---------------------------------------------------------------------------------

def test_row6_page_closed_mid_wizard(live_server, browser, page):
    """Policy row 6, "User closes the page mid-wizard": within 24h, the same email in a
    new browser starts the resume flow, which restores the saved data; the signup then
    goes on to the account."""
    closed = browser.new_context()
    route_turnstile(closed)
    to_email_step(closed.new_page(), live_server)
    closed.close()

    page.goto(f'{live_server.url}/en/signup/')
    wait_for_token(page)
    fill_email(page, EMAIL_ADDRESS)
    expect(page.locator('[data-resume-offer]')).to_contain_text(PENDING_NOTE)
    _resume_with_code(page)
    expect(page.locator('#id_first_name')).to_have_value(FIRST_NAME)
    expect(page.locator('#id_last_name')).to_have_value(LAST_NAME)
    expect(page.locator('#id_phone')).to_have_value(MOBILE_SHOWN)

    # Continue (the saved password kept): the legal step and the email code are done.
    wait_for_token(page)
    press(page, 'Continue')
    expect(page).to_have_url(url('en', 'verify-mobile/'))
    enter_code(page, mobile_code(), 'Create account')
    expect(page.locator('h1')).to_have_text('Welcome to MyTreatPlan')
    assert User.objects.get().email == NORMALIZED


# --- Row 7 ---------------------------------------------------------------------------------

def test_row7_idle_for_30_minutes(live_server, page, clock, monkeypatch):
    """Policy row 7, "Idle for 30 minutes": after 31 minutes without a request the session
    ends; the data stays saved and a code to the email resumes it."""
    monkeypatch.setattr('signup.views._now', clock)
    to_email_step(page, live_server)

    clock.advance(31 * 60)
    page.reload()
    expect(page).to_have_url(url('en'))
    expect(_body(page)).to_contain_text(
        'Your session ended after 30 minutes without activity. Enter your email to '
        'continue where you left off.')
    expect(page.locator('#id_first_name')).to_have_value('')
    assert PendingRegistration.objects.count() == 1  # still saved

    wait_for_token(page)
    fill_email(page, EMAIL_ADDRESS)
    expect(page.locator('[data-resume-offer]')).to_contain_text(PENDING_NOTE)
    _resume_with_code(page)
    expect(page.locator('#id_first_name')).to_have_value(FIRST_NAME)
    expect(page.locator('#id_phone')).to_have_value(MOBILE_SHOWN)


# --- Row 8 ---------------------------------------------------------------------------------

def test_row8_code_not_received(live_server, page, clock):
    """Policy row 8, "Code not received": resend (after the one-minute cooldown), the SMS
    fallback for a mobile without WhatsApp, and editing the email and the mobile; each
    time the code that arrives works."""
    no_whatsapp = FAKE_NO_WHATSAPP_NUMBER.removeprefix('+34')
    to_email_step(page, live_server, phone=no_whatsapp)

    # Edit the email: the new address gets a code.
    follow(page, 'Change email')
    expect(page).to_have_url(url('en'))
    wait_for_token(page)
    fill_email(page, 'bea@example.com')
    press(page, 'Continue')
    expect(page).to_have_url(url('en', 'verify-email/'))  # the legal step is done
    expect(_body(page)).to_contain_text('bea@example.com')

    # Resend to the new address, after the cooldown; the code it sends works.
    clock.advance(60)
    press(page, 'Send a new code')
    expect(_body(page)).to_contain_text(NEW_CODE_SENT)
    enter_code(page, email_code('bea@example.com'))

    # No WhatsApp for this number: the code went by SMS.
    expect(page).to_have_url(url('en', 'verify-mobile/'))
    expect(page.locator('[data-sms-fallback]')).to_have_text(
        'WhatsApp is not available for this number, so we sent the code by SMS.')
    expect(_body(page)).to_contain_text(f'by SMS to {FAKE_NO_WHATSAPP_NUMBER}')

    # Edit the mobile: the new number gets a code (by WhatsApp), which creates the account.
    follow(page, 'Change mobile number')
    expect(page).to_have_url(url('en'))
    wait_for_token(page)
    page.fill('#id_phone', MOBILE)
    press(page, 'Continue')
    expect(page).to_have_url(url('en', 'verify-mobile/'))
    expect(_body(page)).to_contain_text(f'by WhatsApp to {MOBILE_E164}')
    enter_code(page, mobile_code(MOBILE_E164), 'Create account')
    expect(page.locator('h1')).to_have_text('Welcome to MyTreatPlan')
    user = User.objects.get()
    assert user.email == 'bea@example.com' and user.phone == MOBILE_E164


# --- Row 9 ---------------------------------------------------------------------------------

def test_row9_wrong_code_entered(live_server, page, clock, settings):
    """Policy row 9, "Wrong code entered": 5 wrong attempts invalidate the code; a new
    one, asked for within the resend limits, works."""
    to_email_step(page, live_server)
    code = email_code()
    for _attempt in range(4):
        enter_code(page, _wrong(code))
        expect(_body(page)).to_contain_text(WRONG_CODE)
    enter_code(page, _wrong(code))
    expect(_body(page)).to_contain_text(NO_LONGER_VALID)
    expect(_support(page, settings)).to_be_visible()

    enter_code(page, code)  # the right code, too late
    expect(_body(page)).to_contain_text(NO_LONGER_VALID)
    expect(page).to_have_url(url('en', 'verify-email/'))

    clock.advance(60)
    press(page, 'Send a new code')
    expect(_body(page)).to_contain_text(NEW_CODE_SENT)
    enter_code(page, email_code())
    expect(page).to_have_url(url('en', 'verify-mobile/'))


# --- Row 10 --------------------------------------------------------------------------------

def test_row10_resend_limit_reached(live_server, page, clock, settings):
    """Policy row 10, "Resend limit reached": after the first send and 3 resends within
    the hour, the next resend is refused, with the support contact."""
    to_email_step(page, live_server)  # the first send
    for _resend in range(3):
        clock.advance(60)
        press(page, 'Send a new code')
        expect(_body(page)).to_contain_text(NEW_CODE_SENT)

    clock.advance(60)
    press(page, 'Send a new code')
    # The first send was 240 s ago: the next is allowed in 3360 s, i.e. 56 minutes.
    expect(page.locator('[data-hourly-limit]')).to_have_text(
        'You have asked for too many codes. You can ask for a new one in 56 minutes.')
    expect(_body(page)).not_to_contain_text(NEW_CODE_SENT)
    expect(_support(page, settings)).to_be_visible()


# --- Row 12 --------------------------------------------------------------------------------

def test_row12_send_fails(live_server, page, settings):
    """Policy row 12, "Twilio or our server fails while sending a code": an error with a
    retry and the support contact; the registration stays pending."""
    to_mobile_step(page, live_server, phone=FAKE_SEND_FAILS_NUMBER.removeprefix('+34'))
    failed = page.locator('[data-send-failed]')
    expect(failed).to_contain_text("We couldn't send the code. Please try again.")
    expect(_body(page)).to_contain_text(f"We couldn't send a code to {FAKE_SEND_FAILS_NUMBER}")
    expect(_support(page, settings)).to_be_visible()

    press(page, 'Try again')  # fails again with this number
    expect(page).to_have_url(url('en', 'verify-mobile/'))
    expect(page.locator('[data-send-failed]')).to_be_visible()
    pending = PendingRegistration.objects.get()
    assert pending.step == 'verify-mobile'
    assert not User.objects.exists()


# --- Row 13 --------------------------------------------------------------------------------

def test_row13_captcha_or_rate_limit_blocks(live_server, page, settings):
    """Policy row 13, "CAPTCHA or rate limit blocks the attempt": a generic error that
    reveals nothing; what was typed stays (but the passwords)."""
    settings.TURNSTILE_SECRET_KEY = TURNSTILE_FAIL_SECRET
    page.goto(f'{live_server.url}/en/signup/')
    fill_step1(page)
    press(page, 'Continue')
    expect(page).to_have_url(url('en'))
    expect(page.get_by_role('alert').filter(has_text=GENERIC)).to_be_visible()
    expect(page.locator('#id_first_name')).to_have_value(FIRST_NAME)
    expect(page.locator('#id_email')).to_have_value(EMAIL_ADDRESS)
    expect(page.locator('#id_phone')).to_have_value(MOBILE)  # as typed
    expect(page.locator('#id_password1')).to_have_value('')
    assert not PendingRegistration.objects.exists()

    # The CAPTCHA passes now, but this IP may start no more signups this hour.
    settings.TURNSTILE_SECRET_KEY = TURNSTILE_PASS_SECRET
    settings.SIGNUP_IP_LIMITS = {**settings.SIGNUP_IP_LIMITS, limits.SIGNUP_STARTS: 0}
    wait_for_token(page)
    fill_passwords(page)
    press(page, 'Continue')
    expect(page).to_have_url(url('en'))
    expect(page.get_by_role('alert').filter(has_text=GENERIC)).to_be_visible()
    expect(page.locator('#id_first_name')).to_have_value(FIRST_NAME)
    expect(_body(page)).not_to_contain_text('rate limit')
    expect(_body(page)).not_to_contain_text(PASSWORD)
    assert not PendingRegistration.objects.exists()


# --- Row 14 --------------------------------------------------------------------------------

def test_row14_disposable_email_domain(live_server, page, external_checks):
    """Policy row 14, "Disposable email domain": rejected with a message asking for a
    professional email."""
    external_checks.disposable.add('mailinator.com')
    page.goto(f'{live_server.url}/en/signup/')
    fill_step1(page, email='ana.garcia@mailinator.com')
    press(page, 'Continue')
    expect(page).to_have_url(url('en'))
    expect(_body(page)).to_contain_text(
        'Please use your professional email address: disposable email addresses are not '
        'accepted.')
    expect(page.locator('#id_email')).to_have_value('ana.garcia@mailinator.com')
    assert not PendingRegistration.objects.exists()
