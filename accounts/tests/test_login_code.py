"""The second factor at login (epic-login entry 2, CAP-7): every row of the plan's I/O
matrix. A right password opens a pending login; the Doctor chooses the email or the mobile,
and only the code sent there (signup's verification provider, purpose `login`) logs them
in. The fake provider throughout, and Twilio's own request function faked for the
WhatsApp switch."""
import logging
import re

import pytest
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore

from accounts import login_failures, login_guard, views
from accounts.forms import INVALID_LOGIN_MESSAGE, mask_email, mask_phone
from accounts.views import CODE_PAGE, PENDING_LOGIN_SESSION_KEY
from eventlog import service as eventlog
from eventlog.models import Attempt, Event
from signup import limits
from signup.steps import BLOCKED, CODE_STATE_SESSION_KEY, send_code
from signup.tests.helpers import MOBILE_E164, NORMALIZED, PASSWORD, _current_code, _wrong
from signup.verification import (CODE_TTL_SECONDS, EMAIL, FAKE_NO_WHATSAPP_NUMBER,
                                 FAKE_SEND_FAILS_NUMBER, MAX_CHECK_ATTEMPTS, NO_CODE,
                                 PURPOSE_LOGIN, PURPOSE_SIGNUP, SMS, CheckFailed,
                                 FakeVerificationProvider)

from .helpers import (CODE, VERIFY, _deactivate, _html, _logged_in, _login, _login_with_code,
                      _post_code, _right_code, _to_code)

pytestmark = [pytest.mark.django_db,
              pytest.mark.usefixtures('capture_codes', 'clear_cache', 'portal_urls')]

User = get_user_model()

MASKED_EMAIL = 'a•••@example.com'
MASKED_PHONE = '+34 ••• ••• 374'


def _pending(client):
    return client.session.get(PENDING_LOGIN_SESSION_KEY)


def _choose(client, contact='email', lang='en'):
    return client.post(f'/{lang}/login/verify/', {'contact': contact})


def _set_phone(doctor, phone):
    doctor.phone = phone
    doctor.save()


def _code_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == 'signup.verification']


# --- Password ok: the choice ---------------------------------------------------------------

def test_right_password_opens_a_pending_login_only(client, doctor):
    response = _login(client, query='?next=/es/welcome/')

    assert response.status_code == 302
    assert response['Location'] == VERIFY
    assert not _logged_in(client)
    pending = _pending(client)
    assert set(pending) == {'user', 'hash', 'started', 'next', 'email_index', 'phone_index',
                            'contact', 'sent'}
    assert pending['email_index'] == doctor.email_index
    assert pending['phone_index'] == doctor.phone_index
    assert NORMALIZED not in str(pending) and MOBILE_E164 not in str(pending)
    assert pending['user'] == str(doctor.pk)
    assert pending['hash'] == doctor.get_session_auth_hash()
    assert pending['next'] == '/es/welcome/'
    assert pending['contact'] is None
    # Nothing is sent before the choice.
    assert limits.seconds_since_last_send(NORMALIZED, PURPOSE_LOGIN) is None
    assert limits.seconds_since_last_send(MOBILE_E164, PURPOSE_LOGIN) is None
    # The welcome page still needs a login.
    assert client.get('/en/welcome/')['Location'] == '/en/login/?next=/en/welcome/'


def test_choice_page_shows_both_contacts_masked(client, doctor):
    _login(client)

    response = client.get(VERIFY)

    assert response.status_code == 200
    html = _html(response)
    assert "Confirm it&#x27;s you" in html or "Confirm it's you" in html
    assert MASKED_EMAIL in html and MASKED_PHONE in html
    assert 'name="contact" value="email"' in html and 'name="contact" value="mobile"' in html
    assert 'by WhatsApp, or by SMS' in html
    assert 'name="csrfmiddlewaretoken"' in html and 'noindex' in html
    # Never in full.
    assert NORMALIZED not in html and MOBILE_E164 not in html and '918 374' not in html


@pytest.mark.parametrize('url', [VERIFY, CODE])
def test_no_pending_login_goes_to_login(client, url):
    assert client.get(url)['Location'] == '/en/login/'
    assert client.post(url, {'contact': 'email', 'code': '123456'})['Location'] == '/en/login/'


def test_code_page_before_the_choice_goes_to_the_choice(client, doctor):
    _login(client)

    assert client.get(CODE)['Location'] == VERIFY
    assert client.post(CODE, {'code': '123456'})['Location'] == VERIFY


def test_an_unknown_choice_sends_nothing(client, doctor):
    _login(client)

    response = _choose(client, 'fax')

    assert response.status_code == 200
    assert _pending(client)['contact'] is None
    assert limits.seconds_since_last_send(NORMALIZED, PURPOSE_LOGIN) is None


@pytest.mark.parametrize('url', [VERIFY, CODE])
def test_logged_in_doctor_is_sent_on(client, doctor, url):
    client.force_login(doctor)

    assert client.get(url)['Location'] == '/en/welcome/'


# --- Choose email / mobile ----------------------------------------------------------------

def test_choose_email_sends_the_code_to_the_email(client, doctor, capture_codes):
    _to_code(client, 'email')

    assert _pending(client)['contact'] == 'email'
    assert _right_code('email')
    assert limits.seconds_since_last_send(NORMALIZED, PURPOSE_LOGIN) is not None
    assert '(login, en)' in _code_lines(capture_codes)[-1]
    html = _html(client.get(CODE))
    assert 'data-sent="email"' in html
    assert ('We have sent a 6-digit code to '
            f'<strong class="text-ink">{MASKED_EMAIL}</strong>') in html
    assert 'Enter your code' in html and 'autocomplete="one-time-code"' in html
    assert NORMALIZED not in html and MOBILE_E164 not in html
    # The email page offers no SMS.
    assert 'data-sms>' not in html
    assert f'href="{VERIFY}"' in html  # "Get the code another way"


def test_choose_mobile_sends_by_whatsapp(client, doctor, capture_codes):
    _to_code(client, 'mobile')

    assert _right_code('mobile')
    assert 'Verification code for whatsapp' in _code_lines(capture_codes)[-1]
    html = _html(client.get(CODE))
    assert 'data-sent="whatsapp"' in html and MASKED_PHONE in html
    assert 'data-whatsapp-hint' in html and 'data-sms>' in html
    assert MOBILE_E164 not in html and NORMALIZED not in html


def test_choose_mobile_falls_back_to_sms(client, doctor):
    _set_phone(doctor, FAKE_NO_WHATSAPP_NUMBER)

    _to_code(client, 'mobile')

    html = _html(client.get(CODE))
    assert 'data-sms-fallback' in html and 'data-sent="sms"' in html
    assert '+34 ••• ••• 001' in html
    assert 'data-sms>' not in html  # sent by SMS already
    response = _post_code(client, _right_code('mobile', phone=FAKE_NO_WHATSAPP_NUMBER))
    assert response['Location'] == '/en/welcome/'


def test_choosing_the_same_contact_again_within_the_cooldown_sends_nothing(client, doctor,
                                                                          login_clock):
    _to_code(client, 'email')
    code = _right_code('email')
    login_clock.advance(10)

    _choose(client, 'email')

    html = _html(client.get(CODE))
    assert 'data-cooldown' not in html and 'data-sent="email"' in html
    assert _right_code('email') == code


def test_switching_contact_sends_to_the_other(client, doctor):
    _to_code(client, 'email')
    _to_code_choice = _choose(client, 'mobile')

    assert _to_code_choice['Location'] == CODE
    assert _pending(client)['contact'] == 'mobile'
    assert 'data-sent="whatsapp"' in _html(client.get(CODE))
    assert _post_code(client, _right_code('mobile'))['Location'] == '/en/welcome/'


# --- Right code -----------------------------------------------------------------------------

@pytest.mark.parametrize('contact', ['email', 'mobile'])
def test_right_code_logs_in(client, doctor, contact):
    _login(client, next='/es/welcome/')
    _choose(client, contact)

    response = _post_code(client, _right_code(contact))

    assert response.status_code == 302
    assert response['Location'] == '/es/welcome/'
    assert int(client.session[SESSION_KEY]) == doctor.pk
    assert PENDING_LOGIN_SESSION_KEY not in client.session
    assert CODE_PAGE not in (client.session.get(CODE_STATE_SESSION_KEY) or {})
    assert client.get('/es/welcome/').status_code == 200
    # The code is used up, and the pages now send the Doctor on.
    assert FakeVerificationProvider.current_code(EMAIL if contact == 'email' else SMS,
                                                 NORMALIZED if contact == 'email'
                                                 else MOBILE_E164, PURPOSE_LOGIN) is None
    assert client.get(CODE)['Location'] == '/en/welcome/'


def test_right_code_without_next_goes_to_welcome(client, doctor):
    assert _login_with_code(client)['Location'] == '/en/welcome/'


def test_session_key_changes_at_login(client, doctor):
    _to_code(client)
    before = client.session.session_key

    _post_code(client, _right_code())

    assert client.session.session_key != before


def test_a_signup_code_does_not_log_in(client, doctor):
    """The login code has its own purpose: a code for another one fails."""
    _to_code(client, 'email')
    FakeVerificationProvider().send(EMAIL, NORMALIZED, 'signup', 'en')
    signup_code = _current_code(EMAIL, NORMALIZED, 'signup')
    if signup_code == _right_code('email'):  # one chance in a million
        pytest.skip('the two codes happen to match')

    response = _post_code(client, signup_code)

    assert response.status_code == 200
    assert not _logged_in(client)


# --- Wrong code ----------------------------------------------------------------------------

def test_wrong_code_is_refused(client, doctor, caplog):
    _to_code(client)

    with caplog.at_level(logging.INFO, logger='accounts.views'):
        response = _post_code(client, _wrong(_right_code()))

    assert response.status_code == 200
    assert response.context['form'].has_error('code', 'invalid_code')
    assert 'This code is not correct. Check it, or send a new one.' in _html(response)
    assert not _logged_in(client)
    assert _pending(client)['contact'] == 'email'
    assert 'login code rejected: reasons=invalid_code' in [
        r.getMessage() for r in caplog.records if r.name == 'accounts.views']
    # The right one still works.
    assert _post_code(client, _right_code())['Location'] == '/en/welcome/'


def test_empty_code_is_required(client, doctor):
    _to_code(client)

    response = _post_code(client, '')

    assert response.context['form'].has_error('code', 'required')
    assert not _logged_in(client)


def test_rejections_go_through_the_one_hook(client, doctor, monkeypatch):
    _to_code(client)
    seen = []
    monkeypatch.setattr(login_guard, 'login_code_rejected',
                        lambda request, user, form: seen.append((user.pk, form.errors)))

    _post_code(client, _wrong(_right_code()))
    assert len(seen) == 1 and seen[0][0] == doctor.pk

    # Not a code: an empty submit, a non-code action, a check the provider couldn't make.
    _post_code(client, '')
    client.post(CODE, {'action': 'unknown'})

    def unavailable(self, channel, contact, purpose, code):
        raise CheckFailed('twilio: check failed (503)')
    with monkeypatch.context() as patch:
        patch.setattr(FakeVerificationProvider, 'check', unavailable)
        _post_code(client, '123456')
    assert len(seen) == 1

    # No longer valid (the 5th wrong attempt) counts.
    for _ in range(MAX_CHECK_ATTEMPTS - 1):
        _post_code(client, '000000' if _right_code() != '000000' else '111111')
    assert len(seen) == MAX_CHECK_ATTEMPTS
    assert seen[-1][1].as_data()['code'][0].code == 'code_expired'


def test_five_wrong_codes_invalidate_it_and_a_new_one_works(client, doctor, login_clock):
    _to_code(client)
    wrong = _wrong(_right_code())
    for _ in range(MAX_CHECK_ATTEMPTS - 1):
        assert _post_code(client, wrong).context['form'].has_error('code', 'invalid_code')
    # Not the lockout's 5th wrong code in a row (entry 5, test_lockout.py): only the
    # provider's own limit on this code is tested here.
    login_failures.reset(doctor.pk)

    response = _post_code(client, wrong)

    assert response.context['form'].has_error('code', 'code_expired')
    html = _html(response)
    assert 'This code is no longer valid. Send a new code.' in html
    assert 'data-support' in html and 'value="resend"' in html
    assert not _logged_in(client)

    login_clock.advance(limits.COOLDOWN_SECONDS + 1)
    response = client.post(CODE, {'action': 'resend'})
    assert response['Location'] == CODE
    assert 'We have sent you the code again.' in _html(client.get(CODE))
    assert _post_code(client, _right_code())['Location'] == '/en/welcome/'


# --- Resend / SMS ----------------------------------------------------------------------------

def test_resend_within_the_cooldown_shows_the_wait(client, doctor, login_clock):
    _to_code(client)
    login_clock.advance(20)

    assert client.post(CODE, {'action': 'resend'})['Location'] == CODE

    html = _html(client.get(CODE))
    assert 'data-cooldown' in html and 'You can ask for a new code in 40 seconds.' in html
    assert _pending(client)  # still pending


def test_resend_after_the_cooldown_and_the_hourly_limit(client, doctor, login_clock):
    _to_code(client)
    for _ in range(limits.HOURLY_SENDS - 1):
        login_clock.advance(limits.COOLDOWN_SECONDS + 1)
        client.post(CODE, {'action': 'resend'})
        assert 'We have sent you the code again.' in _html(client.get(CODE))

    login_clock.advance(limits.COOLDOWN_SECONDS + 1)
    client.post(CODE, {'action': 'resend'})

    html = _html(client.get(CODE))
    assert 'data-hourly-limit' in html and 'data-support' in html
    assert 'You have asked for too many codes.' in html
    assert _post_code(client, _right_code())['Location'] == '/en/welcome/'


def test_resend_messages_in_spanish(client, doctor, login_clock):
    _to_code(client, 'mobile', lang='es')
    login_clock.advance(limits.COOLDOWN_SECONDS + 1)
    client.post('/es/login/code/', {'action': 'resend'})
    assert 'Le hemos enviado el código de nuevo.' in _html(client.get('/es/login/code/'))
    login_clock.advance(limits.COOLDOWN_SECONDS + 1)
    client.post('/es/login/code/', {'action': 'sms'})
    assert 'Le hemos enviado el código de nuevo por SMS.' in _html(
        client.get('/es/login/code/'))


def test_send_by_sms_instead(client, doctor, login_clock, capture_codes):
    _to_code(client, 'mobile')
    whatsapp_code = _right_code('mobile')
    login_clock.advance(limits.COOLDOWN_SECONDS + 1)

    assert client.post(CODE, {'action': 'sms'})['Location'] == CODE

    assert 'Verification code for sms' in _code_lines(capture_codes)[-1]
    html = _html(client.get(CODE))
    assert 'We have sent you the code again by SMS.' in html
    assert 'data-sent="sms"' in html and 'data-sms-fallback' not in html
    assert 'data-sms>' not in html
    # A mobile has one code, whichever channel carried it.
    assert _right_code('mobile') == whatsapp_code

    # A resend now goes by SMS too.
    login_clock.advance(limits.COOLDOWN_SECONDS + 1)
    client.post(CODE, {'action': 'resend'})
    assert 'Verification code for sms' in _code_lines(capture_codes)[-1]


def test_sms_action_is_ignored_on_the_email_page(client, doctor, login_clock, capture_codes):
    _to_code(client, 'email')
    login_clock.advance(limits.COOLDOWN_SECONDS + 1)
    sent = len(_code_lines(capture_codes))

    for action in ('sms', 'unknown'):
        response = client.post(CODE, {'action': action})

        assert response['Location'] == CODE  # back to the page, no form errors
        assert len(_code_lines(capture_codes)) == sent
    assert 'errorlist' not in _html(client.get(CODE))
    assert _pending(client)['contact'] == 'email'


# --- Send fails / check unavailable ----------------------------------------------------------

def test_failed_send_offers_a_retry_and_support(client, doctor, caplog):
    _set_phone(doctor, FAKE_SEND_FAILS_NUMBER)

    with caplog.at_level(logging.ERROR):
        _to_code(client, 'mobile')

    html = _html(client.get(CODE))
    assert 'data-send-failed' in html and 'data-support' in html
    assert "We couldn't send the code. Please try again." in html
    assert 'value="resend"' in html
    assert 'We couldn\'t send a code to <strong class="text-ink">+34 •••' in html
    assert _pending(client)['contact'] == 'mobile' and not _logged_in(client)
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.getMessage().split(':')[0] for r in errors] == ['Signup code send failed']
    for record in caplog.records:
        assert FAKE_SEND_FAILS_NUMBER not in record.getMessage()
        assert NORMALIZED not in record.getMessage()


def test_check_unavailable_keeps_the_pending_login(client, doctor, monkeypatch, caplog):
    _to_code(client)

    def unavailable(self, channel, contact, purpose, code):
        raise CheckFailed('twilio: check failed (503)')
    monkeypatch.setattr(FakeVerificationProvider, 'check', unavailable)

    with caplog.at_level(logging.ERROR):
        response = _post_code(client, '123456')

    assert response.status_code == 200
    assert response.context['form'].has_error('code', 'check_failed')
    assert "We couldn&#x27;t check your code right now. Please try again." in _html(response)
    assert not _logged_in(client) and _pending(client)
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert messages == ['Signup code check failed: channel=email purpose=login '
                        'error=twilio: check failed (503)']


def test_support_contact_after_two_checks_in_a_row_that_could_not_be_made(
        client, doctor, monkeypatch, login_clock):
    """QA A11, as on signup's code pages: one check that couldn't be made shows no support
    contact, two in a row do; a successful send resets the count."""
    _to_code(client)

    def unavailable(self, channel, contact, purpose, code):
        raise CheckFailed('twilio: check failed (503)')
    monkeypatch.setattr(FakeVerificationProvider, 'check', unavailable)

    assert 'data-support' not in _html(_post_code(client, '123456'))
    assert 'data-support' in _html(_post_code(client, '123456'))

    login_clock.advance(limits.COOLDOWN_SECONDS + 1)
    client.post(CODE, {'action': 'resend'})
    assert 'data-support' not in _html(client.get(CODE))


def test_plain_expiry_shows_no_support_contact(client, doctor, monkeypatch):
    """QA A11: "no longer valid" without 5 wrong codes before (e.g. an expired code)."""
    _to_code(client)
    monkeypatch.setattr(FakeVerificationProvider, 'check', lambda *args: NO_CODE)

    response = _post_code(client, '123456')

    assert response.context['form'].has_error('code', 'code_expired')
    assert 'data-support' not in _html(response)


# --- Pending expired / account changed --------------------------------------------------------

@pytest.mark.parametrize('url,data', [(CODE, None), (CODE, {'action': 'resend'}),
                                      (CODE, {'code': '123456'}), (VERIFY, {'contact': 'mobile'})])
def test_expired_pending_login_goes_back_to_login(client, doctor, login_clock, capture_codes,
                                                  url, data):
    _to_code(client)
    sent = len(_code_lines(capture_codes))
    login_clock.advance(CODE_TTL_SECONDS)

    response = client.post(url, data) if data else client.get(url)

    assert response['Location'] == '/en/login/'
    assert PENDING_LOGIN_SESSION_KEY not in client.session
    assert len(_code_lines(capture_codes)) == sent  # nothing sent
    assert 'Your login has expired. Please log in again.' in _html(client.get('/en/login/'))
    assert client.get(CODE)['Location'] == '/en/login/'  # gone
    assert not _logged_in(client)


def test_pending_login_is_valid_just_before_its_end(client, doctor, login_clock):
    _to_code(client)
    login_clock.advance(CODE_TTL_SECONDS - 5)

    assert _post_code(client, _right_code())['Location'] == '/en/welcome/'


def _change_password(doctor):
    doctor.set_password('another-orbit-canyon-77')
    doctor.save()


def _remove_phone(doctor):
    _set_phone(doctor, '')


def _change_phone(doctor):
    _set_phone(doctor, '+34622918375')


def _change_email(doctor):
    doctor.email = 'ana.other@example.com'
    doctor.save()


@pytest.mark.parametrize('change', [_deactivate, _change_password, _remove_phone,
                                    _change_phone, _change_email])
def test_account_changed_while_pending_refuses_the_code(client, doctor, change):
    _to_code(client)
    code = _right_code()
    change(User.objects.get(pk=doctor.pk))

    response = _post_code(client, code)

    assert response['Location'] == '/en/login/'
    assert not _logged_in(client)
    assert PENDING_LOGIN_SESSION_KEY not in client.session
    assert 'Your login has expired. Please log in again.' in _html(client.get('/en/login/'))
    # Refused before it was checked.
    assert _right_code() == code


def test_account_changed_between_the_check_and_the_login(client, doctor, monkeypatch):
    """`finish_login` checks the account again before `auth_login`."""
    _to_code(client)
    real_clean = views.LoginCodeForm.clean_code

    def clean_then_deactivate(form):
        code = real_clean(form)
        _deactivate(User.objects.get(pk=doctor.pk))
        return code
    monkeypatch.setattr(views.LoginCodeForm, 'clean_code', clean_then_deactivate)

    response = _post_code(client, _right_code())

    assert response['Location'] == '/en/login/'
    assert not _logged_in(client)


def test_a_new_password_login_replaces_the_pending_one(client, doctor, login_clock):
    _to_code(client)
    login_clock.advance(CODE_TTL_SECONDS - 10)
    _login(client)
    login_clock.advance(20)

    assert _pending(client)['contact'] is None
    assert client.get(VERIFY).status_code == 200


# --- No mobile --------------------------------------------------------------------------------

def test_account_without_a_mobile_gets_the_generic_error(client, doctor, capture_codes):
    _remove_phone(doctor)

    response = _login(client)

    assert response.status_code == 200
    assert response.context['form'].non_field_errors() == [INVALID_LOGIN_MESSAGE]
    assert not _logged_in(client)
    assert PENDING_LOGIN_SESSION_KEY not in client.session
    assert _code_lines(capture_codes) == [] or all(
        '(login,' not in line for line in _code_lines(capture_codes))
    assert limits.seconds_since_last_send(NORMALIZED, PURPOSE_LOGIN) is None


def test_no_mobile_page_is_identical_to_a_wrong_password(client, doctor):
    def page(password):
        html = _html(_login(client, password=password))
        return re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', '', html)

    wrong = page('wrong-password-123')
    _remove_phone(doctor)
    assert page(PASSWORD) == wrong


# --- Spanish ------------------------------------------------------------------------------------

def test_spanish_pages_and_code(client, doctor, capture_codes):
    assert _login(client, lang='es')['Location'] == '/es/login/verify/'
    html = _html(client.get('/es/login/verify/'))
    assert '<html lang="es"' in html
    assert 'Confirme que es usted' in html and 'Enviar el código a mi correo electrónico' in html
    assert 'Enviar el código a mi móvil' in html and 'por WhatsApp o por SMS' in html
    assert 'Iniciar sesión con otra cuenta' in html

    assert _choose(client, 'mobile', 'es')['Location'] == '/es/login/code/'
    assert '(login, es)' in _code_lines(capture_codes)[-1]
    html = _html(client.get('/es/login/code/'))
    assert 'Introduzca su código' in html
    assert 'Hemos enviado un código de 6 dígitos por WhatsApp a' in html
    assert 'para iniciar sesión.' in html and 'Recibir el código de otra forma' in html

    response = _post_code(client, _wrong(_right_code('mobile')), lang='es')
    assert 'Este código no es correcto. Revíselo o solicite uno nuevo.' in _html(response)
    assert _post_code(client, _right_code('mobile'), lang='es')['Location'] == '/es/welcome/'


def test_spanish_expired_message(client, doctor, login_clock):
    _to_code(client, lang='es')
    login_clock.advance(CODE_TTL_SECONDS)

    assert client.get('/es/login/code/')['Location'] == '/es/login/'
    assert 'Su inicio de sesión ha caducado. Vuelva a iniciar sesión.' in _html(
        client.get('/es/login/'))


# --- Masking --------------------------------------------------------------------------------------

@pytest.mark.parametrize('email,masked', [
    ('ana.garcia@example.com', MASKED_EMAIL),
    ('b@clinic.es', 'b•••@clinic.es'),
])
def test_mask_email(email, masked):
    assert mask_email(email) == masked


@pytest.mark.parametrize('phone,masked', [
    (MOBILE_E164, MASKED_PHONE),
    ('+12025550143', '+1 ••• ••• 143'),
    ('+447700900123', '+44 ••• ••• 123'),
])
def test_mask_phone(phone, masked):
    assert mask_phone(phone) == masked


def test_code_page_never_shows_the_contacts_in_full(client, doctor):
    for contact in ('email', 'mobile'):
        _to_code(client, contact)
        html = _html(client.get(CODE))
        assert NORMALIZED not in html and MOBILE_E164 not in html
        assert '622 918 374' not in html and '622918374' not in html


# --- Twilio: WhatsApp on or off -----------------------------------------------------------------

@pytest.fixture
def twilio_verify(settings, monkeypatch):
    """The Twilio provider with made-up credentials and its HTTP function faked."""
    from signup.tests.test_twilio_verify import CREDENTIALS, FakeVerify
    from signup.twilio_verify import TWILIO_PROVIDER

    settings.VERIFICATION_PROVIDER = TWILIO_PROVIDER
    for name, value in CREDENTIALS.items():
        setattr(settings, name, value)
    fake = FakeVerify()
    monkeypatch.setattr('signup.twilio_verify.post_verify', fake)
    return fake


@pytest.mark.parametrize('enabled,channel', [(True, 'whatsapp'), (False, 'sms')])
def test_mobile_code_by_whatsapp_or_by_sms_when_it_is_off(client, doctor, settings,
                                                          twilio_verify, enabled, channel):
    settings.TWILIO_WHATSAPP_ENABLED = enabled

    _to_code(client, 'mobile')

    assert [data['Channel'] for _path, data in twilio_verify.calls] == [channel]
    assert twilio_verify.calls[0][1]['To'] == MOBILE_E164
    html = _html(client.get(CODE))
    assert f'data-sent="{channel}"' in html
    assert ('data-sms-fallback' in html) is (not enabled)

    twilio_verify.answer = (200, {'status': 'approved'})
    assert _post_code(client, '123456')['Location'] == '/en/welcome/'
    assert twilio_verify.calls[-1][0] == 'VerificationCheck'


# --- An earlier pending login ends at the login page -----------------------------------------

@pytest.mark.parametrize('outcome', ['get', 'wrong_password', 'captcha_failed', 'no_mobile'])
def test_login_page_ends_an_earlier_pending_login(client, doctor, settings, outcome):
    from conftest import TURNSTILE_FAIL_SECRET

    _to_code(client)
    if outcome == 'get':
        assert client.get('/en/login/').status_code == 200
    elif outcome == 'wrong_password':
        assert _login(client, password='wrong-password-123').status_code == 200
    elif outcome == 'captcha_failed':
        settings.TURNSTILE_SECRET_KEY = TURNSTILE_FAIL_SECRET
        assert _login(client).status_code == 200
    else:
        _remove_phone(doctor)
        assert _login(client).status_code == 200

    assert PENDING_LOGIN_SESSION_KEY not in client.session
    assert client.get(VERIFY)['Location'] == '/en/login/'
    assert client.post(CODE, {'code': _right_code()})['Location'] == '/en/login/'
    assert not _logged_in(client)


# --- "Sent" only for a send of this pending login -------------------------------------------

def test_relogin_within_the_cooldown_shows_the_wait_not_sent(client, doctor, login_clock):
    assert _login_with_code(client)['Location'] == '/en/welcome/'
    client.logout()
    login_clock.advance(20)

    _to_code(client, 'email')

    assert _pending(client)['sent'] is False
    html = _html(client.get(CODE))
    assert 'data-cooldown' in html and 'You can ask for a new code in 40 seconds.' in html
    assert 'data-no-code' in html and 'data-sent=' not in html

    login_clock.advance(41)
    client.post(CODE, {'action': 'resend'})
    assert _pending(client)['sent'] is True
    html = _html(client.get(CODE))
    assert 'data-sent="email"' in html and 'data-no-code' not in html
    assert _post_code(client, _right_code())['Location'] == '/en/welcome/'


# --- The per-IP code-send limit (signup retrospective R4) ---------------------------------

@pytest.fixture
def no_code_sends(settings):
    settings.SIGNUP_IP_LIMITS = {**settings.SIGNUP_IP_LIMITS, limits.CODE_SENDS: 0}


def _ip_blocks():
    return list(Event.objects.filter(event='rejected', field='ip',
                                     attempt__kind=Attempt.LOGIN))


def test_an_ip_blocked_login_code_send_is_recorded_once(client, doctor, no_code_sends):
    _to_code(client)
    client.post(CODE, {'action': 'resend'})

    assert limits.seconds_since_last_send(NORMALIZED, PURPOSE_LOGIN) is None
    [event] = _ip_blocks()  # the resend's block, in the same hour, is not recorded again
    assert event.attempt.account_id == doctor.pk
    assert (event.step, event.reason) == (login_guard.VERIFY_STEP, 'blocked: rate limit')
    assert event.data['counter'] == limits.CODE_SENDS


def test_a_blocked_login_resend_is_recorded_at_the_code_page(client, doctor, settings,
                                                            login_clock):
    _to_code(client)  # sent: the IP's limit is not reached yet
    settings.SIGNUP_IP_LIMITS = {**settings.SIGNUP_IP_LIMITS, limits.CODE_SENDS: 0}
    login_clock.advance(limits.COOLDOWN_SECONDS + 1)

    client.post(CODE, {'action': 'resend'})

    [event] = _ip_blocks()
    assert (event.step, event.attempt.account_id) == (login_guard.CODE_STEP, doctor.pk)


def test_a_failing_block_marker_still_refuses_the_login_send(client, doctor, no_code_sends,
                                                            monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise RuntimeError('cache down')

    monkeypatch.setattr(limits, 'first_block', fail)
    _to_code(client)

    assert limits.seconds_since_last_send(NORMALIZED, PURPOSE_LOGIN) is None
    assert not _ip_blocks()
    assert 'Login code send block record failed: error=RuntimeError' in caplog.text


def test_after_a_login_block_a_signup_block_from_the_ip_is_still_logged(client, doctor,
                                                                        no_code_sends, rf):
    _to_code(client)  # the login send is blocked and recorded
    request = rf.post('/en/signup/verify-email/')  # the same IP as the test client
    request.session = SessionStore()
    attempt = eventlog.start_attempt(Attempt.SIGNUP)

    outcome = send_code(request, attempt, 'verify-email', 'someone@example.com',
                        PURPOSE_SIGNUP, EMAIL)

    assert outcome.status == BLOCKED
    assert len(_ip_blocks()) == 1
    [event] = Event.objects.filter(attempt=attempt, event='rejected', field='ip')
    assert (event.step, event.data['counter']) == ('verify-email', limits.CODE_SENDS)


def test_a_login_block_leaves_signups_first_block_to_signup(rf):
    request = rf.get('/')
    assert limits.first_block(request, limits.CODE_SENDS, flow=PURPOSE_LOGIN)
    assert limits.first_block(request, limits.CODE_SENDS)
    assert not limits.first_block(request, limits.CODE_SENDS, flow=PURPOSE_LOGIN)
    assert not limits.first_block(request, limits.CODE_SENDS)
