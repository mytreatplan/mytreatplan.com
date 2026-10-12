"""Verification codes for signup (email now, mobile from signup entry 7).

A provider sends a code to a contact and later checks the code the person typed. Our
database never holds a code: the real provider (Twilio Verify, entry 11) keeps them, and
the fake below derives them. `get_provider()` returns the class named by the setting
VERIFICATION_PROVIDER.
"""
import hashlib
import hmac
import logging
import time

from django.conf import settings
from django.utils.crypto import constant_time_compare
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)

EMAIL = 'email'
MOBILE = 'mobile'
CHANNELS = (EMAIL, MOBILE)

# What a code is for; part of the code derivation, so a code for one purpose fails another.
PURPOSE_SIGNUP = 'signup'

CODE_LENGTH = 6


class VerificationProvider:
    """Interface for verification-code providers."""

    def send(self, channel, contact, purpose, language):
        """Send a new code to `contact` (an email address or a phone number) over `channel`."""
        raise NotImplementedError

    def check(self, channel, contact, purpose, code):
        """True if `code` is the valid code for this channel, contact and purpose."""
        raise NotImplementedError

    @staticmethod
    def _validate_channel(channel):
        if channel not in CHANNELS:
            raise ValueError(f'Unknown verification channel: {channel!r}')


class FakeVerificationProvider(VerificationProvider):
    """Development and staging stand-in: writes the code to the log and stores nothing.

    The code is HMAC(SECRET_KEY, channel|contact|purpose|window) reduced to 6 digits,
    where window is the current 10-minute period. `check` accepts the current and the
    previous window, so a code stays valid for 10 to 20 minutes and works on any Gunicorn
    worker. Expiry and attempt limits come with signup entry 7.
    """

    WINDOW_SECONDS = 600

    def send(self, channel, contact, purpose, language):
        self._validate_channel(channel)
        code = self._code(channel, contact, purpose, self._window())
        # The only place a code is ever written. This logger is kept out of Sentry.
        logger.info('Verification code for %s %s (%s, %s): %s',
                    channel, contact, purpose, language, code)

    def check(self, channel, contact, purpose, code):
        self._validate_channel(channel)
        code = (code or '').strip()
        if len(code) != CODE_LENGTH or not code.isdigit():
            return False
        window = self._window()
        return any(constant_time_compare(code, self._code(channel, contact, purpose, w))
                   for w in (window, window - 1))

    @classmethod
    def _window(cls):
        return int(time.time() // cls.WINDOW_SECONDS)

    @staticmethod
    def _code(channel, contact, purpose, window):
        message = f'{channel}|{contact}|{purpose}|{window}'.encode()
        digest = hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).digest()
        return f'{int.from_bytes(digest, "big") % 10 ** CODE_LENGTH:0{CODE_LENGTH}d}'


def get_provider():
    """An instance of the provider class named by settings.VERIFICATION_PROVIDER."""
    return import_string(settings.VERIFICATION_PROVIDER)()
