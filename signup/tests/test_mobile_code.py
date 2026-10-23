"""Signup entry 7 (story 2.7): the mobile code, resend limits and send failures. Every row
of the plan's I/O matrix, through the wizard and the resume page."""
import logging

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from eventlog.models import Attempt
from signup import limits
from signup.models import PendingRegistration
from signup.steps import CODE_STATE_SESSION_KEY
from signup.verification import (CODE_TTL_SECONDS, EMAIL, FAKE_NO_WHATSAPP_NUMBER,
                                 FAKE_SEND_FAILS_NUMBER, PURPOSE_RESUME, PURPOSE_SIGNUP, SMS,
                                 CheckFailed, FakeVerificationProvider, SendFailed)

from .helpers import (MOBILE_E164, NORMALIZED, _current_code, _email_code, _legal,
                      _mobile_code, _step1, _to_code_step, _to_mobile_step, _wrong)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()

MOBILE_URL = '/en/signup/verify-mobile/'
EMAIL_URL = '/en/signup/verify-email/'
RESUME_URL = '/en/signup/resume/'
SUPPORT = 'sayhi@mytreatplan.com'
# Typed after the +34 prefix at step 1.
NO_WHATSAPP = FAKE_NO_WHATSAPP_NUMBER.removeprefix('+34')
SEND_FAILS = FAKE_SEND_FAILS_NUMBER.removeprefix('+34')
OTHER_MOBILE = '611 223 344'
OTHER_MOBILE_E164 = '+34611223344'


def _events(*names):
    events = list(Attempt.objects.get().events.all())
    return [e for e in events if not names or e.event in names]


def _code_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == 'signup.verification']


def _html(client, url=MOBILE_URL):
    return client.get(url).content.decode()


# --- Both codes, email only ------------------------------------------------------------

def test_account_is_created_only_after_both_codes(client):
    _to_code_step(client)
    assert 'Continue' in _html(client, EMAIL_URL)  # the mobile step is still to do
    assert 'Create account' not in _html(client, EMAIL_URL)

    _email_code(client)
    pending = PendingRegistration.objects.get()
    assert pending.email_verified_at is not None and pending.phone_verified_at is None
    assert not User.objects.exists()

    assert _mobile_code(client)['Location'] == '/en/welcome/'
    user = User.objects.get()
    assert user.email == NORMALIZED and user.phone == MOBILE_E164
    assert not PendingRegistration.objects.exists()


def test_phone_verified_at_is_set_by_the_mobile_code(client):
    """Seen on the row kept when the account cannot be created (registered meanwhile)."""
    _to_mobile_step(client)
    User.objects.create_user(NORMALIZED, 'another-password-123')
    assert _mobile_code(client).status_code == 200
    pending = PendingRegistration.objects.get()
    assert pending.phone_verified_at is not None and pending.email_verified_at is not None


def test_email_only_shows_the_mobile_page_with_a_whatsapp_code(client, caplog):
    _to_mobile_step(client)
    assert not User.objects.exists()
    assert f'whatsapp {MOBILE_E164} (signup, en)' in _code_lines(caplog)[-1]

    html = _html(client)
    assert 'Step 4 of 4' in html and 'Check your mobile' in html
    assert f'by WhatsApp to <strong class="text-ink">{MOBILE_E164}</strong>' in html
    assert 'Create account' in html
    sent = _events('code_sent')[-1]
    assert (sent.step, sent.data['phone'], sent.data['channel'], sent.data['fallback']) == (
        'verify-mobile', MOBILE_E164, 'whatsapp', False)


def test_mobile_code_in_the_page_language(client, monkeypatch):
    sent = []
    _to_code_step(client, lang='es')
    monkeypatch.setattr(FakeVerificationProvider, 'send', lambda self, *args: sent.append(args))
    client.post('/es/signup/verify-email/',
                {'code': _current_code(EMAIL, NORMALIZED)})
    assert sent == [('whatsapp', MOBILE_E164, 'signup', 'es')]


def test_mobile_page_in_spanish(client):
    _to_mobile_step(client, lang='es')
    html = _html(client, '/es/signup/verify-mobile/')
    assert 'Revise su móvil' in html and 'por WhatsApp' in html
    assert 'Enviar por SMS' in html and 'Cambiar número de móvil' in html


# --- No WhatsApp, switch to SMS ----------------------------------------------------------

def test_no_whatsapp_number_gets_the_code_by_sms(client, caplog):
    _to_mobile_step(client, phone=NO_WHATSAPP)
    assert f'sms {FAKE_NO_WHATSAPP_NUMBER} (signup, en)' in _code_lines(caplog)[-1]

    html = _html(client)
    assert 'WhatsApp is not available for this number, so we sent the code by SMS.' in html
    assert 'by SMS to' in html
    assert 'Send by SMS instead' not in html  # it went by SMS already
    sent = _events('code_sent')[-1]
    assert (sent.data['channel'], sent.data['fallback']) == ('sms', True)

    assert _mobile_code(client, phone=FAKE_NO_WHATSAPP_NUMBER)['Location'] == '/en/welcome/'


def test_switch_to_sms_after_a_minute_counts_as_a_resend(client, caplog, clock):
    _to_mobile_step(client)
    whatsapp_code = _current_code(SMS, MOBILE_E164)
    clock.advance(60)

    response = client.post(MOBILE_URL, {'action': 'sms'})
    assert response['Location'] == MOBILE_URL
    assert f'sms {MOBILE_E164} (signup, en)' in _code_lines(caplog)[-1]
    html = _html(client)
    assert 'We have sent you the code again by SMS.' in html and 'by SMS to' in html
    assert 'WhatsApp is not available' not in html

    sent = _events('code_sent')[-1]
    assert (sent.data['channel'], sent.data['fallback'], sent.data['resend']) == (
        'sms', False, True)
    assert sent.reason == 'OTP not delivered'  # "Code not received" (QA A7)
    assert len(limits._sends(MOBILE_E164, PURPOSE_SIGNUP, clock())) == 2
    # One code per mobile, whichever channel carried it (as with Twilio).
    assert _current_code(SMS, MOBILE_E164) == whatsapp_code
    assert _mobile_code(client)['Location'] == '/en/welcome/'


def test_mobile_resend_is_logged_otp_not_delivered(client, clock):
    """QA A7: "Send a new code" on the mobile page (sent) logs `OTP not delivered`."""
    _to_mobile_step(client)
    clock.advance(60)

    client.post(MOBILE_URL, {'action': 'resend'})

    first, resent = _events('code_sent')[-2:]
    assert (first.step, first.reason) == ('verify-mobile', '')
    assert (resent.step, resent.reason) == ('verify-mobile', 'OTP not delivered')
    assert resent.data['resend'] is True


def test_resend_not_sent_logs_no_otp_not_delivered(client, clock):
    """Within the cooldown nothing is sent, so nothing says the code was not received."""
    _to_mobile_step(client)

    client.post(MOBILE_URL, {'action': 'resend'})

    assert [e.reason for e in _events('code_sent')] == ['', '']  # the email's, the mobile's


# --- Cooldown, hourly limit ------------------------------------------------------------

def test_resend_within_a_minute_is_not_sent(client, caplog, clock):
    _to_mobile_step(client)
    sent_before = len(_code_lines(caplog))
    clock.advance(20)

    assert client.post(MOBILE_URL, {'action': 'resend'})['Location'] == MOBILE_URL
    assert len(_code_lines(caplog)) == sent_before
    html = _html(client)
    assert 'You can ask for a new code in 40 seconds.' in html
    assert 'We have sent you the code again.' not in html and SUPPORT not in html
    # Shown once.
    assert 'You can ask for a new code in' not in _html(client)
    assert len(_events('code_sent')) == 2  # email and mobile


def test_sms_within_a_minute_is_not_sent_either(client, caplog, clock):
    _to_mobile_step(client)
    sent_before = len(_code_lines(caplog))
    client.post(MOBILE_URL, {'action': 'sms'})
    assert len(_code_lines(caplog)) == sent_before
    assert 'You can ask for a new code in 60 seconds.' in _html(client)


def test_cooldown_in_spanish(client, clock):
    _to_mobile_step(client, lang='es')
    clock.advance(59)
    client.post('/es/signup/verify-mobile/', {'action': 'resend'})
    assert 'Podrá solicitar un código nuevo dentro de 1 segundo.' in _html(
        client, '/es/signup/verify-mobile/')


def test_fourth_resend_within_an_hour_is_refused_with_the_support_contact(client, caplog,
                                                                          clock):
    _to_mobile_step(client)
    for _resend in range(3):
        clock.advance(60)
        client.post(MOBILE_URL, {'action': 'resend'})
    assert len([e for e in _events('code_sent') if e.step == 'verify-mobile']) == 4
    sent_before = len(_code_lines(caplog))

    clock.advance(60)
    client.post(MOBILE_URL, {'action': 'resend'})
    assert len(_code_lines(caplog)) == sent_before
    html = _html(client)
    # The first send was 240 s ago: the next is allowed in 3360 s, i.e. 56 minutes.
    assert 'You have asked for too many codes. You can ask for a new one in 56 minutes.' in html
    assert f'mailto:{SUPPORT}' in html
    rejected = _events('rejected')[-1]
    assert (rejected.step, rejected.field, rejected.reason) == (
        'verify-mobile', 'phone', 'OTP rate limited')
    assert rejected.data['phone'] == MOBILE_E164

    # Switching to SMS is a resend too.
    client.post(MOBILE_URL, {'action': 'sms'})
    assert len(_code_lines(caplog)) == sent_before

    # An hour after the first send, one more is allowed.
    clock.advance(3360)
    client.post(MOBILE_URL, {'action': 'resend'})
    assert len(_code_lines(caplog)) == sent_before + 1


def test_limits_are_per_contact_and_keyed_by_the_blind_index(client, clock):
    from django.db import connection

    _to_mobile_step(client)
    with connection.cursor() as cursor:
        cursor.execute('SELECT cache_key, value FROM django_cache')
        rows = cursor.fetchall()
    for key, value in rows:
        assert NORMALIZED not in key and MOBILE_E164 not in key
        assert NORMALIZED not in value and MOBILE_E164 not in value
    assert not limits.check_send(MOBILE_E164, PURPOSE_SIGNUP).allowed
    assert limits.check_send(OTHER_MOBILE_E164, PURPOSE_SIGNUP).allowed


# --- Wrong attempts --------------------------------------------------------------------

def test_five_wrong_codes_invalidate_the_code(client):
    _to_mobile_step(client)
    code = _current_code(SMS, MOBILE_E164)
    for _attempt in range(4):
        response = client.post(MOBILE_URL, {'code': _wrong(code)})
        assert response.context['form'].errors['code'] == [
            'This code is not correct. Check it, or send a new one.']
        assert SUPPORT not in response.content.decode()

    response = client.post(MOBILE_URL, {'code': _wrong(code)})
    assert response.context['form'].errors['code'] == [
        'This code is no longer valid. Send a new code.']
    assert f'mailto:{SUPPORT}' in response.content.decode()

    response = client.post(MOBILE_URL, {'code': code})
    assert response.context['form'].errors['code'] == [
        'This code is no longer valid. Send a new code.']
    assert not User.objects.exists()
    reasons = {e.reason for e in _events('rejected')}
    assert reasons == {'OTP verification failed'}
    assert len(_events('rejected')) == 6


def test_after_invalidation_a_new_code_works(client, clock):
    _to_mobile_step(client)
    code = _current_code(SMS, MOBILE_E164)
    for _attempt in range(5):
        client.post(MOBILE_URL, {'code': _wrong(code)})
    clock.advance(60)
    client.post(MOBILE_URL, {'action': 'resend'})
    assert _mobile_code(client)['Location'] == '/en/welcome/'


def test_wrong_email_codes_count_too(client):
    _to_code_step(client)
    code = _current_code(EMAIL, NORMALIZED)
    for _attempt in range(5):
        response = client.post(EMAIL_URL, {'code': _wrong(code)})
    assert response.context['form'].errors['code'] == [
        'This code is no longer valid. Send a new code.']
    assert client.post(EMAIL_URL, {'code': code}).status_code == 200


# --- Send fails ------------------------------------------------------------------------

def test_failed_send_offers_a_retry_and_the_support_contact(client, caplog):
    _to_code_step(client, phone=SEND_FAILS)
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger='signup.steps'):
        response = _email_code(client)
    assert response['Location'] == MOBILE_URL  # the registration stays pending
    assert PendingRegistration.objects.get().step == 'verify-mobile'

    html = _html(client)
    assert "We couldn't send the code. Please try again." in html
    assert 'value="resend"' in html and 'Try again' in html
    assert f'mailto:{SUPPORT}' in html
    assert "We couldn't send a code to" in html

    failed = _events('code_send_failed')[-1]
    assert (failed.step, failed.field, failed.reason) == (
        'verify-mobile', 'phone', 'OTP send failed')
    assert failed.data['phone'] == FAKE_SEND_FAILS_NUMBER
    assert not [e for e in _events('code_sent') if e.step == 'verify-mobile']

    [error] = [r for r in caplog.records if r.name == 'signup.steps']
    assert error.levelno == logging.ERROR
    assert FAKE_SEND_FAILS_NUMBER not in error.getMessage()
    assert NORMALIZED not in error.getMessage()

    # Not counted: a retry is allowed at once (and fails again here).
    assert limits.check_send(FAKE_SEND_FAILS_NUMBER, PURPOSE_SIGNUP).allowed
    client.post(MOBILE_URL, {'action': 'resend'})
    assert len(_events('code_send_failed')) == 2


def test_retry_after_a_failed_email_send(client, monkeypatch):
    def fail(self, *args):
        raise SendFailed('provider down')

    real_send = FakeVerificationProvider.send
    monkeypatch.setattr(FakeVerificationProvider, 'send', fail)
    _to_code_step(client)
    html = _html(client, EMAIL_URL)
    assert "We couldn't send the code." in html
    assert _events('code_send_failed')[-1].data['email'] == NORMALIZED

    monkeypatch.setattr(FakeVerificationProvider, 'send', real_send)
    client.post(EMAIL_URL, {'action': 'resend'})
    html = _html(client, EMAIL_URL)
    assert "We couldn't send" not in html and 'We have sent you the code again.' in html
    assert _email_code(client)['Location'] == MOBILE_URL


# --- Edit contact ----------------------------------------------------------------------

def test_change_mobile_number(client, caplog):
    _to_mobile_step(client)
    html = _html(client)
    assert 'href="/en/signup/"' in html and 'Change mobile number' in html
    assert 'Change email' in _html(client, EMAIL_URL)

    # The duplicate checks run again.
    User.objects.create_user('someone@example.net', 'another-password-123',
                             phone=OTHER_MOBILE_E164)
    response = _step1(client, password='', password2='', phone=OTHER_MOBILE)
    assert response.context['form'].errors == {
        'phone': ['This mobile phone number is already registered.']}
    User.objects.by_email('someone@example.net').delete()

    PendingRegistration.objects.update(phone_verified_at='2026-10-13T10:00:00Z')
    response = _step1(client, password='', password2='', phone=OTHER_MOBILE)
    assert response['Location'] == MOBILE_URL  # legal and email skipped
    pending = PendingRegistration.objects.get()
    assert pending.phone == OTHER_MOBILE_E164 and pending.phone_verified_at is None
    assert pending.email_verified_at is not None
    assert f'whatsapp {OTHER_MOBILE_E164}' in _code_lines(caplog)[-1]
    assert _mobile_code(client, phone=OTHER_MOBILE_E164)['Location'] == '/en/welcome/'


def test_same_mobile_keeps_its_verification(client):
    _to_mobile_step(client)
    PendingRegistration.objects.update(phone_verified_at='2026-10-13T10:00:00Z')
    response = _step1(client, password='', password2='')
    # Nothing left to do: the account is created.
    assert response['Location'] == '/en/welcome/'


def test_change_email_sends_a_code_to_the_new_one(client, caplog):
    _to_code_step(client)
    response = _step1(client, email='bea@example.com', password='', password2='')
    assert response['Location'] == EMAIL_URL
    assert 'email bea@example.com (signup' in _code_lines(caplog)[-1]


# --- Back and Continue -----------------------------------------------------------------

def test_back_and_continue_within_a_minute_sends_no_new_code(client, caplog, clock):
    _to_mobile_step(client)
    code = _current_code(SMS, MOBILE_E164)
    sent_before = len(_code_lines(caplog))
    clock.advance(30)

    assert _step1(client, password='', password2='')['Location'] == MOBILE_URL
    assert len(_code_lines(caplog)) == sent_before
    assert 'by WhatsApp' in _html(client)
    assert client.post(MOBILE_URL, {'code': code})['Location'] == '/en/welcome/'


def test_back_and_continue_after_a_minute_sends_the_same_code_again(client, caplog, clock):
    _to_mobile_step(client)
    code = _current_code(SMS, MOBILE_E164)
    sent_before = len(_code_lines(caplog))
    clock.advance(60)

    _step1(client, password='', password2='')
    assert len(_code_lines(caplog)) == sent_before + 1
    assert _code_lines(caplog)[-1].endswith(code)  # still valid: the same code


# --- Resume ----------------------------------------------------------------------------

@pytest.fixture
def first(client):
    """A signup in progress in another browser, at the email code."""
    _step1(client)
    _legal(client)
    return PendingRegistration.objects.get()


def _offer(second):
    second.post('/en/signup/check-email/', {'email': NORMALIZED})


def test_resumed_signup_asks_the_mobile_code_before_the_account(first):
    """After a resume: a password set again, the legal step again, then the mobile
    code (the email is verified by the resume code), and only then the account."""
    second = Client()
    _offer(second)
    second.post(RESUME_URL, {'action': 'send'})
    second.post(RESUME_URL, {'code': _current_code(EMAIL, NORMALIZED, PURPOSE_RESUME)})
    new = PendingRegistration.objects.get()
    assert new.email_verified_at is not None and new.phone_verified_at is None

    assert _step1(second)['Location'] == '/en/signup/legal/'
    assert _legal(second)['Location'] == MOBILE_URL
    assert not User.objects.exists()
    assert _mobile_code(second)['Location'] == '/en/welcome/'


def test_resume_does_not_copy_the_mobile_verification(first):
    PendingRegistration.objects.update(phone_verified_at='2026-10-13T10:00:00Z')
    first.refresh_from_db()
    new = first.copy_to_new_row()
    assert new.phone_verified_at is None


def test_resume_code_limits(first, caplog, clock):
    second = Client()
    _offer(second)
    second.post(RESUME_URL, {'action': 'send'})
    sent = len(_code_lines(caplog))

    second.post(RESUME_URL, {'action': 'resend'})
    assert len(_code_lines(caplog)) == sent
    assert 'You can ask for a new code in 60 seconds.' in _html(second, RESUME_URL)

    for _resend in range(3):
        clock.advance(60)
        second.post(RESUME_URL, {'action': 'resend'})
    assert len(_code_lines(caplog)) == sent + 3

    clock.advance(60)
    second.post(RESUME_URL, {'action': 'resend'})
    assert len(_code_lines(caplog)) == sent + 3
    html = _html(second, RESUME_URL)
    assert 'You have asked for too many codes.' in html and f'mailto:{SUPPORT}' in html
    attempt = Attempt.objects.get(pk=second.session['signup_attempt_id'])
    rejected = list(attempt.events.filter(event='rejected'))[-1]
    assert (rejected.step, rejected.field, rejected.reason) == (
        'resume', 'email', 'OTP rate limited')


def test_resume_offer_again_within_a_minute_sends_no_new_code(first, caplog):
    second = Client()
    _offer(second)
    second.post(RESUME_URL, {'action': 'send'})
    sent = len(_code_lines(caplog))
    _offer(second)
    second.post(RESUME_URL, {'action': 'send'})
    assert len(_code_lines(caplog)) == sent
    html = _html(second, RESUME_URL)
    assert 'We have sent a 6-digit code to' in html and 'You can ask' not in html


def test_resume_send_failure(first, monkeypatch):
    def fail(self, *args):
        raise SendFailed('provider down')

    monkeypatch.setattr(FakeVerificationProvider, 'send', fail)
    second = Client()
    _offer(second)
    second.post(RESUME_URL, {'action': 'send'})
    html = _html(second, RESUME_URL)
    assert "We couldn't send the code." in html and f'mailto:{SUPPORT}' in html
    attempt = Attempt.objects.get(pk=second.session['signup_attempt_id'])
    failed = attempt.events.get(event='code_send_failed')
    assert (failed.step, failed.reason) == ('resume', 'OTP send failed')


# --- Review fixes: lock, retry channel, intro after a refused send, Back -----------------

def test_a_held_send_lock_blocks_a_send(client, caplog, clock):
    """A parallel send (e.g. a double click) holds the lock: this one is not sent."""
    _to_mobile_step(client)
    clock.advance(60)
    sent_before = len(_code_lines(caplog))
    assert limits.acquire_send_lock(MOBILE_E164, PURPOSE_SIGNUP)

    client.post(MOBILE_URL, {'action': 'resend'})
    assert len(_code_lines(caplog)) == sent_before
    assert 'You can ask for a new code in' in _html(client)

    limits.release_send_lock(MOBILE_E164, PURPOSE_SIGNUP)
    client.post(MOBILE_URL, {'action': 'resend'})
    assert len(_code_lines(caplog)) == sent_before + 1


def test_retry_after_a_failed_send_uses_the_channel_tried(client, monkeypatch):
    calls = []
    failing = {'whatsapp'}
    real_send = FakeVerificationProvider.send

    def send(self, channel, *args):
        calls.append(channel)
        if channel in failing:
            raise SendFailed('provider down')
        return real_send(self, channel, *args)

    monkeypatch.setattr(FakeVerificationProvider, 'send', send)
    _to_code_step(client)
    _email_code(client)
    assert calls[-1] == 'whatsapp'
    html = _html(client)
    assert "We couldn't send the code." in html and 'value="sms"' in html  # SMS offered

    client.post(MOBILE_URL, {'action': 'resend'})  # Try again: WhatsApp, the channel tried
    assert calls[-1] == 'whatsapp'

    failing.add('sms')
    client.post(MOBILE_URL, {'action': 'sms'})
    assert calls[-1] == 'sms'
    html = _html(client)
    assert "We couldn't send the code." in html and 'value="sms"' not in html
    client.post(MOBILE_URL, {'action': 'resend'})  # Try again: SMS, the channel tried
    assert calls[-1] == 'sms'

    failing.clear()
    client.post(MOBILE_URL, {'action': 'resend'})
    assert calls[-1] == 'sms' and 'by SMS to' in _html(client)


def test_mobile_page_refused_on_entering_does_not_say_a_code_was_sent(client, clock):
    _to_mobile_step(client)
    for _resend in range(3):
        clock.advance(60)
        client.post(MOBILE_URL, {'action': 'resend'})
    clock.advance(600)  # the last code is no longer valid; the hour is not over

    assert _step1(client, password='', password2='')['Location'] == MOBILE_URL
    html = _html(client)
    assert 'You have asked for too many codes.' in html
    assert 'Ask for a new code below to continue.' in html
    assert 'We have sent a 6-digit code' not in html


def test_resume_send_refused_by_the_hourly_limit_does_not_say_a_code_was_sent(first, clock):
    second = Client()
    _offer(second)
    second.post(RESUME_URL, {'action': 'send'})
    for _resend in range(3):
        clock.advance(60)
        second.post(RESUME_URL, {'action': 'resend'})
    clock.advance(600)
    assert 'Ask for a new code below to continue.' in _html(second, RESUME_URL)

    _offer(second)  # a new offer: nothing sent for it yet
    second.post(RESUME_URL, {'action': 'send'})
    assert second.session['signup_resume']['sent'] is False
    html = _html(second, RESUME_URL)
    assert 'You have asked for too many codes.' in html
    assert 'We have sent a 6-digit code' not in html and 'value="send"' in html


def test_back_from_the_mobile_page_skips_the_verified_email_step(client):
    _to_mobile_step(client)
    html = _html(client)
    # Back goes to the legal step (never skipped), not to the used-up email code.
    assert 'href="/en/signup/legal/"' in html and 'href="/en/signup/verify-email/"' not in html


# --- The WhatsApp hint (signup entry 11) --------------------------------------------------

WHATSAPP_HINT = "Didn't get it on WhatsApp?"


def test_whatsapp_hint_next_to_send_by_sms_after_a_whatsapp_code(client):
    _to_mobile_step(client)

    html = _html(client)
    assert WHATSAPP_HINT in html and 'data-whatsapp-hint' in html
    assert html.index(WHATSAPP_HINT) < html.index('Send by SMS instead')


def test_whatsapp_hint_in_spanish(client):
    _to_mobile_step(client, lang='es')

    html = _html(client, '/es/signup/verify-mobile/')
    assert '¿No lo ha recibido por WhatsApp?' in html and 'Enviar por SMS' in html


def test_no_whatsapp_hint_after_an_sms_code(client, clock):
    _to_mobile_step(client)
    clock.advance(60)
    client.post(MOBILE_URL, {'action': 'sms'})

    assert WHATSAPP_HINT not in _html(client)


def test_no_whatsapp_hint_after_the_sms_fallback(client):
    _to_mobile_step(client, phone=NO_WHATSAPP)

    assert WHATSAPP_HINT not in _html(client)


def test_no_whatsapp_hint_on_the_email_page(client):
    _to_code_step(client)

    assert WHATSAPP_HINT not in _html(client, EMAIL_URL)


# --- A code check that fails (signup entry 11) ---------------------------------------------

CHECK_FAILED = "We couldn&#x27;t check your code right now. Please try again."


@pytest.fixture
def failing_check(monkeypatch):
    """The provider cannot check codes while `failing` is set."""
    state = {'failing': False, 'calls': 0}
    real_check = FakeVerificationProvider.check

    def check(self, *args):
        state['calls'] += 1
        if state['failing']:
            raise CheckFailed('provider down')
        return real_check(self, *args)

    monkeypatch.setattr(FakeVerificationProvider, 'check', check)
    return state


def test_check_failure_shows_an_error_and_is_not_counted(client, caplog, failing_check):
    _to_mobile_step(client)
    code = _current_code(SMS, MOBILE_E164)
    failing_check['failing'] = True

    for _ in range(6):  # more than the wrong attempts that invalidate a code
        response = client.post(MOBILE_URL, {'code': code})
        assert response.status_code == 200
        html = response.content.decode()
        assert CHECK_FAILED in html
        assert 'This code is not correct' not in html and 'no longer valid' not in html
    assert not User.objects.exists()

    failing_check['failing'] = False
    assert _mobile_code(client)['Location'] == '/en/welcome/'


def test_check_failure_is_logged_without_personal_values(client, caplog, failing_check):
    _to_mobile_step(client)
    code = _current_code(SMS, MOBILE_E164)
    failing_check['failing'] = True
    caplog.clear()

    client.post(MOBILE_URL, {'code': code})

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [(r.name, r.getMessage()) for r in errors] == [(
        'signup.steps', 'Signup code check failed: channel=sms purpose=signup error=provider down')]
    rejected = _events('rejected')[-1]
    assert (rejected.step, rejected.field, rejected.reason) == (
        'verify-mobile', 'code', 'OTP check failed')
    assert code not in str(rejected.data)


def test_check_failure_on_the_email_page_in_spanish(client, failing_check):
    _to_code_step(client, lang='es')
    code = _current_code(EMAIL, NORMALIZED)
    failing_check['failing'] = True

    html = client.post('/es/signup/verify-email/', {'code': code}).content.decode()

    assert 'No hemos podido comprobar su código en este momento. Inténtelo de nuevo.' in html
    assert _events('rejected')[-1].reason == 'OTP check failed'


def test_fake_provider_never_raises_check_failed(client):
    _to_mobile_step(client)

    assert FakeVerificationProvider().check(SMS, MOBILE_E164, PURPOSE_SIGNUP, 'garbage') in (
        'wrong', 'no_code')
    assert FakeVerificationProvider().check(SMS, '+34600000000', PURPOSE_SIGNUP, '123456') == (
        'no_code')


# --- When the support contact shows (QA A11) --------------------------------------------

def test_plain_expiry_shows_no_support_contact(client, clock):
    _to_mobile_step(client)
    code = _current_code(SMS, MOBILE_E164)
    clock.advance(CODE_TTL_SECONDS + 1)

    response = client.post(MOBILE_URL, {'code': code})

    assert response.context['form'].errors['code'] == [
        'This code is no longer valid. Send a new code.']
    assert SUPPORT not in response.content.decode()


def test_one_check_failure_shows_no_support_two_in_a_row_do(client, failing_check):
    _to_mobile_step(client)
    code = _current_code(SMS, MOBILE_E164)
    failing_check['failing'] = True

    assert SUPPORT not in client.post(MOBILE_URL, {'code': code}).content.decode()
    html = client.post(MOBILE_URL, {'code': code}).content.decode()
    assert CHECK_FAILED in html and f'mailto:{SUPPORT}' in html
    assert f'mailto:{SUPPORT}' in _html(client)  # until a check is made or a code sent

    # A check made ends the run: a wrong code, then one more failure.
    failing_check['failing'] = False
    assert SUPPORT not in client.post(MOBILE_URL, {'code': _wrong(code)}).content.decode()
    failing_check['failing'] = True
    assert SUPPORT not in client.post(MOBILE_URL, {'code': code}).content.decode()


def test_a_successful_send_resets_the_counts(client, clock, failing_check):
    _to_mobile_step(client)
    code = _current_code(SMS, MOBILE_E164)
    failing_check['failing'] = True
    client.post(MOBILE_URL, {'code': code})
    client.post(MOBILE_URL, {'code': code})
    assert f'mailto:{SUPPORT}' in _html(client)

    clock.advance(60)
    client.post(MOBILE_URL, {'action': 'resend'})
    assert SUPPORT not in _html(client)
    assert SUPPORT not in client.post(MOBILE_URL, {'code': code}).content.decode()

    # After 5 wrong codes too.
    failing_check['failing'] = False
    for _attempt in range(5):
        response = client.post(MOBILE_URL, {'code': _wrong(code)})
    assert f'mailto:{SUPPORT}' in response.content.decode()
    clock.advance(60)
    client.post(MOBILE_URL, {'action': 'resend'})
    assert SUPPORT not in _html(client)


def test_five_wrong_email_codes_show_the_support_contact(client):
    _to_code_step(client)
    code = _current_code(EMAIL, NORMALIZED)
    for _attempt in range(4):
        assert SUPPORT not in client.post(EMAIL_URL, {'code': _wrong(code)}).content.decode()
    assert f'mailto:{SUPPORT}' in client.post(
        EMAIL_URL, {'code': _wrong(code)}).content.decode()


def test_resume_page_support_rules(first, clock):
    second = Client()
    _offer(second)
    second.post(RESUME_URL, {'action': 'send'})
    code = _current_code(EMAIL, NORMALIZED, PURPOSE_RESUME)
    for _attempt in range(4):
        response = second.post(RESUME_URL, {'code': _wrong(code)})
        assert SUPPORT not in response.content.decode()
    response = second.post(RESUME_URL, {'code': _wrong(code)})
    assert 'This code is no longer valid. Send a new code.' in response.content.decode()
    assert f'mailto:{SUPPORT}' in response.content.decode()

    # A new code, then a plain expiry: no support contact.
    clock.advance(60)
    second.post(RESUME_URL, {'action': 'resend'})
    assert SUPPORT not in _html(second, RESUME_URL)
    code = _current_code(EMAIL, NORMALIZED, PURPOSE_RESUME)
    clock.advance(CODE_TTL_SECONDS + 1)
    response = second.post(RESUME_URL, {'code': code})
    assert 'This code is no longer valid. Send a new code.' in response.content.decode()
    assert SUPPORT not in response.content.decode()


def test_resend_messages_in_spanish(client, clock):
    _to_code_step(client, lang='es')
    _email_code(client, lang='es')
    clock.advance(60)
    client.post('/es/signup/verify-mobile/', {'action': 'resend'})
    assert 'Le hemos enviado el código de nuevo.' in _html(client, '/es/signup/verify-mobile/')
    clock.advance(60)
    client.post('/es/signup/verify-mobile/', {'action': 'sms'})
    assert 'Le hemos enviado el código de nuevo por SMS.' in _html(
        client, '/es/signup/verify-mobile/')


# --- How wrong codes are counted (plan decision 2) ---------------------------------------

def _wrong_count(client, page='verify-mobile'):
    return client.session[CODE_STATE_SESSION_KEY][page].get('wrong', 0)


def test_wrong_codes_count_across_a_resend_of_the_same_code(client, clock):
    _to_mobile_step(client)
    code = _current_code(SMS, MOBILE_E164)
    for _attempt in range(4):
        client.post(MOBILE_URL, {'code': _wrong(code)})
    clock.advance(60)
    client.post(MOBILE_URL, {'action': 'resend'})
    assert _current_code(SMS, MOBILE_E164) == code  # the same code, as with Twilio

    response = client.post(MOBILE_URL, {'code': _wrong(code)})
    assert response.context['form'].errors['code'] == [
        'This code is no longer valid. Send a new code.']
    assert f'mailto:{SUPPORT}' in response.content.decode()


def test_four_wrong_codes_then_an_expiry_shows_no_support(client, clock):
    _to_mobile_step(client)
    code = _current_code(SMS, MOBILE_E164)
    for _attempt in range(4):
        client.post(MOBILE_URL, {'code': _wrong(code)})
    clock.advance(CODE_TTL_SECONDS + 1)

    response = client.post(MOBILE_URL, {'code': code})
    assert response.context['form'].errors['code'] == [
        'This code is no longer valid. Send a new code.']
    assert SUPPORT not in response.content.decode()


def test_the_same_expired_code_five_times_shows_no_support(client, clock):
    _to_mobile_step(client)
    code = _current_code(SMS, MOBILE_E164)
    clock.advance(CODE_TTL_SECONDS + 1)
    for _attempt in range(5):
        response = client.post(MOBILE_URL, {'code': code})
        assert response.context['form'].has_error('code', 'code_expired')
        assert SUPPORT not in response.content.decode()


def test_malformed_entries_are_not_counted(client):
    _to_mobile_step(client)
    for entry in ('abc', '12345', '1234567'):
        response = client.post(MOBILE_URL, {'code': entry})
        assert response.context['form'].has_error('code', 'invalid_code')
    assert _wrong_count(client) == 0
    client.post(MOBILE_URL, {'code': _wrong(_current_code(SMS, MOBILE_E164))})
    assert _wrong_count(client) == 1


def test_a_send_after_the_code_validity_resets_the_count(client, clock):
    _to_mobile_step(client)
    code = _current_code(SMS, MOBILE_E164)
    for _attempt in range(4):
        client.post(MOBILE_URL, {'code': _wrong(code)})
    assert _wrong_count(client) == 4
    clock.advance(CODE_TTL_SECONDS + 1)
    client.post(MOBILE_URL, {'action': 'resend'})
    assert _wrong_count(client) == 0

    new_code = _current_code(SMS, MOBILE_E164)
    client.post(MOBILE_URL, {'code': _wrong(new_code)})
    clock.advance(CODE_TTL_SECONDS + 1)
    response = client.post(MOBILE_URL, {'code': new_code})
    assert response.context['form'].has_error('code', 'code_expired')
    assert SUPPORT not in response.content.decode()


def test_plain_expiry_on_the_email_step_shows_no_support(client, clock):
    _to_code_step(client)
    code = _current_code(EMAIL, NORMALIZED)
    clock.advance(CODE_TTL_SECONDS + 1)

    response = client.post(EMAIL_URL, {'code': code})
    assert response.context['form'].has_error('code', 'code_expired')
    assert SUPPORT not in response.content.decode()


def test_resume_page_check_failures(first, failing_check):
    second = Client()
    _offer(second)
    second.post(RESUME_URL, {'action': 'send'})
    code = _current_code(EMAIL, NORMALIZED, PURPOSE_RESUME)
    failing_check['failing'] = True

    assert SUPPORT not in second.post(RESUME_URL, {'code': code}).content.decode()
    html = second.post(RESUME_URL, {'code': code}).content.decode()
    assert CHECK_FAILED in html and f'mailto:{SUPPORT}' in html
