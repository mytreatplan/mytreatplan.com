"""The verification-provider interface and the console fake."""
import logging
import re

import pytest

from signup import verification
from signup.verification import (
    CHANNELS,
    EMAIL,
    MOBILE,
    FakeVerificationProvider,
    VerificationProvider,
    get_provider,
)

CONTACT = 'ana@example.com'


@pytest.fixture
def clock(monkeypatch):
    """Control time.time() as seen by the verification module."""
    now = {'t': 1_800_000_000.0}
    monkeypatch.setattr(verification.time, 'time', lambda: now['t'])
    return now


def _sent_code(caplog, provider=None, channel=EMAIL, contact=CONTACT, purpose='signup'):
    caplog.clear()
    with caplog.at_level(logging.INFO, logger='signup.verification'):
        (provider or FakeVerificationProvider()).send(channel, contact, purpose, 'en')
    (record,) = caplog.records
    return re.search(r'(\d{6})$', record.getMessage()).group(1)


def test_channels():
    assert CHANNELS == ('email', 'mobile')


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


@pytest.mark.parametrize('channel', [EMAIL, MOBILE])
def test_fake_sends_a_six_digit_code_to_the_log_and_checks_it(caplog, clock, channel):
    contact = CONTACT if channel == EMAIL else '+34600000000'
    code = _sent_code(caplog, channel=channel, contact=contact)

    message = caplog.records[0].getMessage()
    assert channel in message and contact in message
    assert re.fullmatch(r'\d{6}', code)
    assert FakeVerificationProvider().check(channel, contact, 'signup', code) is True


def test_fake_accepts_spaces_around_the_code(caplog, clock):
    code = _sent_code(caplog)
    assert FakeVerificationProvider().check(EMAIL, CONTACT, 'signup', f' {code} ')


@pytest.mark.parametrize('change', [
    {'channel': MOBILE}, {'contact': 'bea@example.com'}, {'purpose': 'resume'},
])
def test_code_is_bound_to_channel_contact_and_purpose(caplog, clock, change):
    code = _sent_code(caplog)
    args = {'channel': EMAIL, 'contact': CONTACT, 'purpose': 'signup', **change}

    assert FakeVerificationProvider().check(code=code, **args) is False


@pytest.mark.parametrize('code', ['', '12345', '1234567', 'abcdef', None])
def test_malformed_codes_fail(clock, code):
    assert FakeVerificationProvider().check(EMAIL, CONTACT, 'signup', code) is False


def test_wrong_code_fails(caplog, clock):
    code = _sent_code(caplog)
    wrong = f'{(int(code) + 1) % 1_000_000:06d}'
    assert FakeVerificationProvider().check(EMAIL, CONTACT, 'signup', wrong) is False


def test_code_valid_in_the_current_and_previous_window_only(caplog, clock):
    code = _sent_code(caplog)
    provider = FakeVerificationProvider()

    clock['t'] += FakeVerificationProvider.WINDOW_SECONDS
    assert provider.check(EMAIL, CONTACT, 'signup', code)
    clock['t'] += FakeVerificationProvider.WINDOW_SECONDS
    assert not provider.check(EMAIL, CONTACT, 'signup', code)


def test_same_window_gives_the_same_code_across_instances(caplog, clock):
    """Stateless: any Gunicorn worker can check a code another one sent."""
    assert _sent_code(caplog) == _sent_code(caplog, provider=FakeVerificationProvider())


def test_code_depends_on_the_secret_key(settings):
    # Fixed inputs, so this is deterministic.
    settings.SECRET_KEY = 'first-secret-key'
    first = FakeVerificationProvider._code(EMAIL, CONTACT, 'signup', 1)
    settings.SECRET_KEY = 'second-secret-key'
    assert FakeVerificationProvider._code(EMAIL, CONTACT, 'signup', 1) != first


def test_unknown_channel_is_refused(clock):
    provider = FakeVerificationProvider()
    with pytest.raises(ValueError):
        provider.send('fax', CONTACT, 'signup', 'en')
    with pytest.raises(ValueError):
        provider.check('fax', CONTACT, 'signup', '123456')
