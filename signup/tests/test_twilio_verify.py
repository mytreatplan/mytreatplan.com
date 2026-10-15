"""Signup entry 11: the Twilio Verify provider (signup.twilio_verify), its settings check
(signup.E001), and the wizard running on it. Twilio is never called: the tests replace
the provider's single HTTP function (`post_verify`), or urllib under it."""
import base64
import http.client
import io
import json
import logging
import socket
import urllib.error
import urllib.parse

import pytest
from django.core.checks import run_checks

from eventlog.models import Attempt
from signup import twilio_verify
from signup.checks import TWILIO_CREDENTIALS_MISSING, check_twilio_credentials
from signup.twilio_verify import TwilioVerifyProvider, post_verify
from signup.verification import (APPROVED, EMAIL, NO_CODE, SMS, WHATSAPP, WRONG,
                                 ChannelUnavailable, CheckFailed, SendFailed, get_provider)

from .test_wizard import MOBILE_E164, NORMALIZED, _legal, _step1

TWILIO = 'signup.twilio_verify.TwilioVerifyProvider'
# Made-up values in Twilio's formats; none is a real credential.
ACCOUNT_SID = 'AC' + '0' * 31 + '1'
API_KEY_SID = 'SK' + '0' * 31 + '2'
API_KEY_SECRET = 'not-a-real-secret-0123456789abcdef'
SERVICE_SID = 'VA' + '0' * 31 + '3'
CREDENTIALS = {
    'TWILIO_ACCOUNT_SID': ACCOUNT_SID,
    'TWILIO_API_KEY_SID': API_KEY_SID,
    'TWILIO_API_KEY_SECRET': API_KEY_SECRET,
    'TWILIO_VERIFY_SERVICE_SID': SERVICE_SID,
}
EMAIL_TO = 'ana.garcia@example.com'
CODE = '482913'
# Nothing of these may appear in an error, its repr, or a log line.
SECRETS = (EMAIL_TO, MOBILE_E164, MOBILE_E164[1:], CODE, API_KEY_SECRET, API_KEY_SID,
           ACCOUNT_SID, SERVICE_SID)


@pytest.fixture
def twilio(settings):
    """The Twilio provider selected, with made-up credentials."""
    settings.VERIFICATION_PROVIDER = TWILIO
    for name, value in CREDENTIALS.items():
        setattr(settings, name, value)
    return settings


class FakeVerify:
    """Stands in for `post_verify`: records each call; answers with `answer` (status,
    body), or raises `error`."""

    def __init__(self):
        self.calls = []
        self.answer = (201, {'status': 'pending'})
        self.error = None

    def __call__(self, path, data):
        self.calls.append((path, dict(data)))
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture
def verify(monkeypatch, twilio):
    fake = FakeVerify()
    monkeypatch.setattr('signup.twilio_verify.post_verify', fake)
    return fake


def _assert_no_secrets(*texts):
    for text in texts:
        for secret in SECRETS:
            assert secret not in text, f'{secret!r} leaked into {text!r}'


def _log_text(caplog):
    return '\n'.join(r.getMessage() for r in caplog.records)


# --- Send ---------------------------------------------------------------------------------

def test_get_provider_returns_twilio_when_selected(twilio):
    assert isinstance(get_provider(), TwilioVerifyProvider)


def test_email_send_posts_email_channel_and_locale(verify):
    TwilioVerifyProvider().send(EMAIL, EMAIL_TO, 'signup', 'es')

    assert verify.calls == [('Verifications',
                             {'To': EMAIL_TO, 'Channel': 'email', 'Locale': 'es'})]


@pytest.mark.parametrize('language, locale', [
    ('en', 'en'), ('es', 'es'), ('es-es', 'es'), ('EN-us', 'en'), ('fr', 'en'), (None, 'en')])
def test_locale_follows_the_language(verify, language, locale):
    TwilioVerifyProvider().send(SMS, MOBILE_E164, 'signup', language)

    assert verify.calls[-1][1]['Locale'] == locale


def test_whatsapp_send_when_enabled(verify, settings):
    settings.TWILIO_WHATSAPP_ENABLED = True

    TwilioVerifyProvider().send(WHATSAPP, MOBILE_E164, 'signup', 'en')

    assert verify.calls == [('Verifications',
                             {'To': MOBILE_E164, 'Channel': 'whatsapp', 'Locale': 'en'})]


def test_whatsapp_off_is_unavailable_without_calling_twilio(verify, settings):
    settings.TWILIO_WHATSAPP_ENABLED = False

    with pytest.raises(ChannelUnavailable):
        TwilioVerifyProvider().send(WHATSAPP, MOBILE_E164, 'signup', 'en')
    assert verify.calls == []


def test_sms_send_while_whatsapp_is_off(verify, settings):
    settings.TWILIO_WHATSAPP_ENABLED = False

    TwilioVerifyProvider().send(SMS, MOBILE_E164, 'signup', 'en')

    assert verify.calls[-1][1]['Channel'] == 'sms'


@pytest.mark.parametrize('channel, status, code', [
    (WHATSAPP, 400, 63003),   # no WhatsApp on the number
    (WHATSAPP, 400, 60223),   # channel disabled on the service
    (SMS, 400, 60205),        # a landline: SMS can't reach it
    (WHATSAPP, 400, '63003'),
])
def test_channel_unavailable_errors(verify, settings, channel, status, code):
    settings.TWILIO_WHATSAPP_ENABLED = True
    verify.answer = (status, {'code': code, 'message': f'To {MOBILE_E164} unreachable',
                              'status': status})

    with pytest.raises(ChannelUnavailable) as caught:
        TwilioVerifyProvider().send(channel, MOBILE_E164, 'signup', 'en')
    assert f'code {code}, HTTP {status}' in str(caught.value)
    _assert_no_secrets(str(caught.value), repr(caught.value))


@pytest.mark.parametrize('status, body', [
    (403, {'code': 60410, 'message': 'Fraud Guard blocked +34622918374'}),
    (429, {'code': 60203, 'message': 'Max send attempts reached'}),
    (401, {'code': 20003, 'message': 'Authenticate'}),
    (404, {'code': 20404, 'message': f'Service {SERVICE_SID} not found'}),
    (400, {'code': 60200, 'message': 'Invalid parameter'}),
    (500, {}),
    (503, {'unexpected': True}),
    (200 + 100, {}),  # a redirect status is not a send either
])
def test_send_refused_is_send_failed_with_code_and_status_only(verify, caplog, status, body):
    caplog.set_level(logging.INFO)
    verify.answer = (status, body)

    with pytest.raises(SendFailed) as caught:
        TwilioVerifyProvider().send(EMAIL, EMAIL_TO, 'signup', 'en')
    message = str(caught.value)
    assert f'HTTP {status}' in message
    assert f"code {body.get('code', 'none')}" in message
    assert 'Fraud' not in message and 'Authenticate' not in message
    _assert_no_secrets(message, repr(caught.value), _log_text(caplog))


@pytest.mark.parametrize('error', [
    TimeoutError('timed out'),
    socket.timeout('timed out'),
    urllib.error.URLError(f'cannot reach https://x/{EMAIL_TO}'),
    ConnectionResetError(f'reset by {EMAIL_TO}'),
    http.client.RemoteDisconnected('closed'),
    ValueError('bad body'),
])
def test_network_failure_is_send_failed(verify, caplog, error):
    caplog.set_level(logging.INFO)
    verify.error = error

    with pytest.raises(SendFailed) as caught:
        TwilioVerifyProvider().send(EMAIL, EMAIL_TO, 'signup', 'en')
    assert type(error).__name__ in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    _assert_no_secrets(str(caught.value), repr(caught.value), _log_text(caplog))


def test_unknown_channel_is_refused(verify):
    with pytest.raises(ValueError):
        TwilioVerifyProvider().send('fax', MOBILE_E164, 'signup', 'en')
    assert verify.calls == []


def test_provider_logs_on_the_code_logger_without_contact(verify, caplog):
    caplog.set_level(logging.INFO)

    TwilioVerifyProvider().send(EMAIL, EMAIL_TO, 'signup', 'en')
    verify.answer = (200, {'status': 'approved'})
    TwilioVerifyProvider().check(EMAIL, EMAIL_TO, 'signup', CODE)

    records = [r for r in caplog.records if r.name.startswith('signup')]
    assert records and {r.name for r in records} == {'signup.verification'}
    _assert_no_secrets(_log_text(caplog))


# --- Check --------------------------------------------------------------------------------

@pytest.mark.parametrize('status, body, expected', [
    (200, {'status': 'approved', 'valid': True}, APPROVED),
    (200, {'status': 'pending', 'valid': False}, WRONG),
    (200, {'status': 'canceled'}, NO_CODE),
    (200, {'status': 'expired'}, NO_CODE),
    (200, {'status': 'max_attempts_reached'}, NO_CODE),
    (404, {'code': 20404, 'message': 'The requested resource was not found'}, NO_CODE),
    (404, {}, NO_CODE),
    (429, {'code': 60202, 'message': 'Max check attempts reached'}, NO_CODE),
])
def test_check_results(verify, status, body, expected):
    verify.answer = (status, body)

    assert TwilioVerifyProvider().check(SMS, MOBILE_E164, 'signup', CODE) == expected
    assert verify.calls == [('VerificationCheck', {'To': MOBILE_E164, 'Code': CODE})]


def test_check_strips_spaces_around_the_code(verify):
    verify.answer = (200, {'status': 'approved'})

    assert TwilioVerifyProvider().check(EMAIL, EMAIL_TO, 'resume', f' {CODE} ') == APPROVED
    assert verify.calls[-1][1]['Code'] == CODE


@pytest.mark.parametrize('code', ['12ab', '12345', '1234567', '', None, '12 345', '12345a',
                                  '\u0661\u0662\u0663\u0664\u0665\u0666'])
def test_malformed_code_is_wrong_without_calling_twilio(verify, code):
    assert TwilioVerifyProvider().check(EMAIL, EMAIL_TO, 'signup', code) == WRONG
    assert verify.calls == []


@pytest.mark.parametrize('status, body', [
    (401, {'code': 20003, 'message': 'Authenticate'}),
    (403, {'code': 20403}),
    (500, {}),
    (503, {'code': 20503}),
    (429, {'code': 60212, 'message': f'Too many requests for {MOBILE_E164}'}),
    (200, {'status': 'something new'}),
    (200, {}),
])
def test_check_failure_answers(verify, caplog, status, body):
    caplog.set_level(logging.INFO)
    verify.answer = (status, body)

    with pytest.raises(CheckFailed) as caught:
        TwilioVerifyProvider().check(SMS, MOBILE_E164, 'signup', CODE)
    assert f'HTTP {status}' in str(caught.value)
    _assert_no_secrets(str(caught.value), repr(caught.value), _log_text(caplog))


@pytest.mark.parametrize('error', [
    TimeoutError('timed out'), urllib.error.URLError('no route'),
    http.client.IncompleteRead(b''), OSError(f'{MOBILE_E164} {CODE}')])
def test_check_network_failure(verify, caplog, error):
    caplog.set_level(logging.INFO)
    verify.error = error

    with pytest.raises(CheckFailed) as caught:
        TwilioVerifyProvider().check(SMS, MOBILE_E164, 'signup', CODE)
    _assert_no_secrets(str(caught.value), repr(caught.value), _log_text(caplog))


# --- The HTTP call ------------------------------------------------------------------------

class _Response(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(body)
        self.status = status


@pytest.fixture
def urlopen(monkeypatch, twilio):
    """Replaces urllib's urlopen; `answer` is a response, or an exception to raise."""
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen['request'], seen['timeout'] = request, timeout
        answer = seen['answer']
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr('urllib.request.urlopen', fake_urlopen)
    return seen


def test_post_verify_request(urlopen, settings):
    settings.EXTERNAL_CHECK_TIMEOUT = 2.5
    urlopen['answer'] = _Response(201, b'{"status": "pending", "sid": "VE1"}')

    status, body = post_verify('Verifications', {'To': EMAIL_TO, 'Channel': 'email',
                                                 'Locale': 'en'})

    assert (status, body) == (201, {'status': 'pending', 'sid': 'VE1'})
    request = urlopen['request']
    assert request.full_url == f'https://verify.test.invalid/v2/Services/{SERVICE_SID}/Verifications'
    assert request.get_method() == 'POST'
    assert urlopen['timeout'] == 2.5
    assert urllib.parse.parse_qs(request.data.decode()) == {
        'To': [EMAIL_TO], 'Channel': ['email'], 'Locale': ['en']}
    expected = base64.b64encode(f'{API_KEY_SID}:{API_KEY_SECRET}'.encode()).decode()
    assert request.get_header('Authorization') == f'Basic {expected}'
    assert request.get_header('Content-type') == 'application/x-www-form-urlencoded'
    assert request.get_header('User-agent') == settings.EXTERNAL_CHECK_USER_AGENT


def test_post_verify_quotes_the_service_sid(urlopen, settings):
    settings.TWILIO_VERIFY_SERVICE_SID = '../Other?x=1'
    urlopen['answer'] = _Response(200, b'{}')

    post_verify('VerificationCheck', {'To': EMAIL_TO, 'Code': CODE})

    assert urlopen['request'].full_url == (
        'https://verify.test.invalid/v2/Services/..%2FOther%3Fx%3D1/VerificationCheck')


def test_post_verify_returns_http_errors_with_their_body(urlopen):
    body = json.dumps({'code': 60203, 'message': 'Max send attempts reached',
                       'status': 429}).encode()
    urlopen['answer'] = urllib.error.HTTPError('https://verify.test.invalid', 429, 'Too Many',
                                               {}, io.BytesIO(body))

    assert post_verify('Verifications', {}) == (429, {'code': 60203,
                                                      'message': 'Max send attempts reached',
                                                      'status': 429})


@pytest.mark.parametrize('raw', [b'not json', b'[1, 2]', b''])
def test_post_verify_unreadable_body_is_empty(urlopen, raw):
    urlopen['answer'] = urllib.error.HTTPError('https://verify.test.invalid', 500, 'Error',
                                               {}, io.BytesIO(raw))

    assert post_verify('Verifications', {}) == (500, {})


def test_post_verify_network_error_propagates(urlopen):
    urlopen['answer'] = urllib.error.URLError('timed out')

    with pytest.raises(urllib.error.URLError):
        post_verify('Verifications', {})


def test_provider_through_the_real_http_function(urlopen):
    """post_verify and the provider together: a 404 send (wrong service SID) fails."""
    urlopen['answer'] = urllib.error.HTTPError(
        'https://verify.test.invalid', 404, 'Not Found', {},
        io.BytesIO(b'{"code": 20404, "message": "not found", "status": 404}'))

    with pytest.raises(SendFailed, match=r'code 20404, HTTP 404'):
        TwilioVerifyProvider().send(EMAIL, EMAIL_TO, 'signup', 'en')


def test_tests_never_reach_twilio(twilio):
    """The `no_network` guard stays: without the fakes, a call cannot leave the machine."""
    with pytest.raises(RuntimeError, match='tests must not reach the network'):
        twilio_verify.post_verify('Verifications', {'To': EMAIL_TO})


# --- signup.E001 --------------------------------------------------------------------------

def test_fake_provider_needs_no_credentials(settings):
    settings.VERIFICATION_PROVIDER = 'signup.verification.FakeVerificationProvider'

    assert check_twilio_credentials(None) == []


def test_twilio_with_every_credential_passes(twilio):
    assert check_twilio_credentials(None) == []


@pytest.mark.parametrize('missing', list(CREDENTIALS))
def test_twilio_with_an_empty_credential_fails(twilio, missing):
    setattr(twilio, missing, '  ' if missing.endswith('SECRET') else '')

    errors = check_twilio_credentials(None)

    assert [e.id for e in errors] == [TWILIO_CREDENTIALS_MISSING]
    assert missing in errors[0].msg
    assert all(name not in errors[0].msg for name in CREDENTIALS if name != missing)
    _assert_no_secrets(errors[0].msg, errors[0].hint)
    # A normal check (also run by `migrate`), not only `check --deploy`.
    assert TWILIO_CREDENTIALS_MISSING in {e.id for e in run_checks()}


def test_twilio_with_no_credentials_names_all_four(settings):
    settings.VERIFICATION_PROVIDER = TWILIO

    errors = check_twilio_credentials(None)

    assert all(name in errors[0].msg for name in CREDENTIALS)


def test_tests_run_on_the_fake_provider(settings):
    """Root conftest.py pins the fake, whatever a developer's settings.env says."""
    assert settings.VERIFICATION_PROVIDER == 'signup.verification.FakeVerificationProvider'
    assert settings.TWILIO_VERIFY_BASE_URL.startswith('https://verify.test.invalid/')
    assert not settings.TWILIO_API_KEY_SECRET


# --- The wizard on Twilio -----------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.usefixtures('portal_urls')
def test_signup_on_twilio_mobile_goes_by_sms_while_whatsapp_is_off(client, verify, settings):
    settings.TWILIO_WHATSAPP_ENABLED = False
    _step1(client, lang='es')
    _legal(client, lang='es')
    assert verify.calls[-1] == ('Verifications',
                                {'To': NORMALIZED, 'Channel': 'email', 'Locale': 'es'})

    verify.answer = (200, {'status': 'approved'})
    response = client.post('/es/signup/verify-email/', {'code': CODE})
    assert response['Location'] == '/es/signup/verify-mobile/'
    assert verify.calls[-2] == ('VerificationCheck', {'To': NORMALIZED, 'Code': CODE})
    # WhatsApp is off: no call for it, the code goes by SMS.
    assert verify.calls[-1] == ('Verifications',
                                {'To': MOBILE_E164, 'Channel': 'sms', 'Locale': 'es'})
    sent = [e for e in Attempt.objects.get().events.all() if e.event == 'code_sent'][-1]
    assert (sent.data['channel'], sent.data['fallback']) == ('sms', True)


@pytest.mark.django_db
@pytest.mark.usefixtures('portal_urls')
def test_signup_on_twilio_wrong_code_and_expired_code(client, verify):
    _step1(client)
    _legal(client)

    verify.answer = (200, {'status': 'pending'})
    html = client.post('/en/signup/verify-email/', {'code': CODE}).content.decode()
    assert 'This code is not correct.' in html

    verify.answer = (404, {'code': 20404})
    html = client.post('/en/signup/verify-email/', {'code': CODE}).content.decode()
    assert 'This code is no longer valid.' in html

    calls = len(verify.calls)
    html = client.post('/en/signup/verify-email/', {'code': '12ab'}).content.decode()
    assert 'This code is not correct.' in html and len(verify.calls) == calls


@pytest.mark.django_db
@pytest.mark.usefixtures('portal_urls')
def test_signup_on_twilio_failed_send_shows_the_retry(client, verify, caplog):
    verify.answer = (404, {'code': 20404})  # e.g. a wrong TWILIO_VERIFY_SERVICE_SID
    _step1(client)
    _legal(client)

    html = client.get('/en/signup/verify-email/').content.decode()
    assert "We couldn't send the code. Please try again." in html
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.name for r in errors] == ['signup.steps']
    assert errors[0].getMessage() == (
        'Signup code send failed: channel=email purpose=signup '
        'error=twilio: send refused (code 20404, HTTP 404)')


@pytest.mark.django_db
@pytest.mark.usefixtures('portal_urls')
def test_signup_on_twilio_no_whatsapp_answer_falls_back_to_sms(client, verify, settings,
                                                               monkeypatch):
    """WhatsApp on, and Twilio answers the WhatsApp send with 63003 (no WhatsApp on the
    number): the code goes by SMS."""
    settings.TWILIO_WHATSAPP_ENABLED = True
    _step1(client)
    _legal(client)

    def answer(path, data):
        verify.calls.append((path, dict(data)))
        if path == 'VerificationCheck':
            return 200, {'status': 'approved'}
        if data['Channel'] == 'whatsapp':
            return 400, {'code': 63003, 'message': 'Channel could not find To address',
                         'status': 400}
        return 201, {'status': 'pending'}

    monkeypatch.setattr('signup.twilio_verify.post_verify', answer)
    response = client.post('/en/signup/verify-email/', {'code': CODE})
    assert response['Location'] == '/en/signup/verify-mobile/'

    assert [data['Channel'] for path, data in verify.calls if path == 'Verifications'][-2:] == [
        'whatsapp', 'sms']
    html = client.get('/en/signup/verify-mobile/').content.decode()
    assert 'WhatsApp is not available for this number, so we sent the code by SMS.' in html
    assert 'by SMS to' in html
    sent = [e for e in Attempt.objects.get().events.all() if e.event == 'code_sent'][-1]
    assert (sent.data['channel'], sent.data['fallback']) == ('sms', True)
