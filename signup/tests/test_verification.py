"""The verification-provider interface and the fake, which emulates Twilio Verify's code
rules (signup entry 7)."""
import logging
import re

import pytest
from django.core.cache import cache

from accounts.crypto import email_index, phone_index
from signup.verification import (
    APPROVED,
    CHANNELS,
    CODE_TTL_SECONDS,
    EMAIL,
    FAKE_NO_WHATSAPP_NUMBER,
    FAKE_SEND_FAILS_NUMBER,
    MAX_CHECK_ATTEMPTS,
    NO_CODE,
    SMS,
    WHATSAPP,
    WRONG,
    ChannelUnavailable,
    FakeVerificationProvider,
    SendFailed,
    VerificationProvider,
    get_provider,
)

CONTACT = 'ana@example.com'
PHONE = '+34622918374'


def _sent_code(caplog, provider=None, channel=EMAIL, contact=CONTACT, purpose='signup'):
    caplog.clear()
    with caplog.at_level(logging.INFO, logger='signup.verification'):
        (provider or FakeVerificationProvider()).send(channel, contact, purpose, 'en')
    (record,) = caplog.records
    return re.search(r'(\d{6})$', record.getMessage()).group(1)


def _wrong(code):
    return f'{(int(code) + 1) % 1_000_000:06d}'


def _check(code, channel=EMAIL, contact=CONTACT, purpose='signup'):
    return FakeVerificationProvider().check(channel, contact, purpose, code)


def test_channels():
    assert CHANNELS == ('email', 'whatsapp', 'sms')


def test_interface_methods_are_abstract():
    provider = VerificationProvider()
    with pytest.raises(NotImplementedError):
        provider.send(EMAIL, CONTACT, 'signup', 'en')
    with pytest.raises(NotImplementedError):
        provider.check(EMAIL, CONTACT, 'signup', '123456')


def test_get_provider_defaults_to_the_fake():
    assert isinstance(get_provider(), FakeVerificationProvider)


class _OtherProvider(VerificationProvider):
    pass


def test_get_provider_follows_the_setting(settings):
    settings.VERIFICATION_PROVIDER = f'{__name__}._OtherProvider'
    assert isinstance(get_provider(), _OtherProvider)


@pytest.mark.django_db
@pytest.mark.parametrize('channel, contact', [(EMAIL, CONTACT), (WHATSAPP, PHONE), (SMS, PHONE)])
def test_fake_sends_a_six_digit_code_to_the_log_and_checks_it(caplog, clock, channel, contact):
    code = _sent_code(caplog, channel=channel, contact=contact)

    message = caplog.records[0].getMessage()
    assert channel in message and contact in message and 'en' in message
    assert re.fullmatch(r'\d{6}', code)
    assert _check(code, channel=channel, contact=contact) == APPROVED


@pytest.mark.django_db
def test_an_approved_code_is_used_up(caplog, clock):
    code = _sent_code(caplog)
    assert _check(code) == APPROVED
    assert _check(code) == NO_CODE


@pytest.mark.django_db
def test_a_mobile_has_one_code_over_whatsapp_and_sms(caplog, clock):
    code = _sent_code(caplog, channel=WHATSAPP, contact=PHONE)
    assert _sent_code(caplog, channel=SMS, contact=PHONE) == code
    assert _check(code, channel=SMS, contact=PHONE) == APPROVED


@pytest.mark.django_db
def test_fake_accepts_spaces_around_the_code(caplog, clock):
    code = _sent_code(caplog)
    assert _check(f' {code} ') == APPROVED


@pytest.mark.django_db
@pytest.mark.parametrize('change', [
    {'contact': 'bea@example.com'}, {'purpose': 'resume'}, {'channel': SMS, 'contact': PHONE},
])
def test_code_is_bound_to_contact_and_purpose(caplog, clock, change):
    code = _sent_code(caplog)
    assert _check(code, **change) == NO_CODE  # no code was sent for those
    _sent_code(caplog, **{'channel': EMAIL, 'contact': CONTACT, 'purpose': 'signup', **change})
    assert _check(code, **change) == WRONG


@pytest.mark.django_db
def test_no_code_sent_is_no_code(clock):
    assert _check('123456') == NO_CODE


@pytest.mark.django_db
@pytest.mark.parametrize('code', ['', '12345', '1234567', 'abcdef', None])
def test_malformed_codes_are_wrong(caplog, clock, code):
    _sent_code(caplog)
    assert _check(code) == WRONG


@pytest.mark.django_db
def test_a_send_within_ten_minutes_repeats_the_code(caplog, clock):
    first = _sent_code(caplog)
    clock.advance(CODE_TTL_SECONDS - 1)
    assert _sent_code(caplog, provider=FakeVerificationProvider()) == first


@pytest.mark.django_db
def test_code_expires_after_ten_minutes_and_a_new_send_gives_a_new_one(caplog, clock):
    first = _sent_code(caplog)
    clock.advance(CODE_TTL_SECONDS - 1)
    assert FakeVerificationProvider.current_code(EMAIL, CONTACT, 'signup') == first
    clock.advance(1)
    assert _check(first) == NO_CODE

    second = _sent_code(caplog)  # a new nonce (the code may collide, 1 in a million)
    assert _check(second) == APPROVED


@pytest.mark.django_db
def test_five_wrong_attempts_invalidate_the_code(caplog, clock):
    code = _sent_code(caplog)
    for _attempt in range(MAX_CHECK_ATTEMPTS - 1):
        assert _check(_wrong(code)) == WRONG
    assert _check(_wrong(code)) == NO_CODE  # the fifth
    assert _check(code) == NO_CODE  # even the right one now


@pytest.mark.django_db
def test_wrong_attempts_count_across_resends(caplog, clock):
    code = _sent_code(caplog)
    for _attempt in range(MAX_CHECK_ATTEMPTS - 1):
        _check(_wrong(code))
    assert _sent_code(caplog) == code  # Twilio: the same verification, same attempts
    assert _check(_wrong(code)) == NO_CODE
    new = _sent_code(caplog)  # a new verification
    assert FakeVerificationProvider.current_code(EMAIL, CONTACT, 'signup') == new
    assert _check(new) == APPROVED


@pytest.mark.django_db
def test_code_depends_on_the_secret_key(settings):
    settings.SECRET_KEY = 'first-secret-key'
    first = FakeVerificationProvider._code(EMAIL, CONTACT, 'signup', 'nonce')
    settings.SECRET_KEY = 'second-secret-key'
    assert FakeVerificationProvider._code(EMAIL, CONTACT, 'signup', 'nonce') != first


@pytest.mark.django_db
def test_cache_holds_no_code_and_no_plain_contact(caplog, clock):
    from django.db import connection

    email_code = _sent_code(caplog)
    phone_code = _sent_code(caplog, channel=WHATSAPP, contact=PHONE)
    with connection.cursor() as cursor:
        cursor.execute('SELECT cache_key, value FROM django_cache')
        rows = cursor.fetchall()
    assert rows
    for key, value in rows:
        for secret in (CONTACT, PHONE, email_code, phone_code):
            assert secret not in key and secret not in str(value)
    keys = ' '.join(key for key, _value in rows)
    assert email_index(CONTACT) in keys and phone_index(PHONE) in keys
    assert cache.get(f'signup:fake-otp:email:{email_index(CONTACT)}:signup')['wrong'] == 0


@pytest.mark.django_db
def test_no_whatsapp_number(caplog, clock):
    with pytest.raises(ChannelUnavailable):
        FakeVerificationProvider().send(WHATSAPP, FAKE_NO_WHATSAPP_NUMBER, 'signup', 'en')
    code = _sent_code(caplog, channel=SMS, contact=FAKE_NO_WHATSAPP_NUMBER)
    assert _check(code, channel=SMS, contact=FAKE_NO_WHATSAPP_NUMBER) == APPROVED


@pytest.mark.django_db
@pytest.mark.parametrize('channel', [WHATSAPP, SMS])
def test_send_fails_number(caplog, clock, channel):
    caplog.clear()
    with pytest.raises(SendFailed):
        FakeVerificationProvider().send(channel, FAKE_SEND_FAILS_NUMBER, 'signup', 'en')
    assert not [r for r in caplog.records if r.name == 'signup.verification']


def test_unknown_channel_is_refused():
    provider = FakeVerificationProvider()
    with pytest.raises(ValueError):
        provider.send('fax', CONTACT, 'signup', 'en')
    with pytest.raises(ValueError):
        provider.check('fax', CONTACT, 'signup', '123456')
