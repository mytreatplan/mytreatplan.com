"""Verification codes for signup: the email, the mobile (WhatsApp, else SMS) and the
resume code.

A provider sends a code to a contact and later checks the code the person typed. The
code rules belong to the provider, as with Twilio Verify (entry 11): 6 digits, valid for
10 minutes, a send within that time repeats the same code, and 5 wrong attempts
invalidate it. Our database never holds a code. `get_provider()` returns the class named
by the setting VERIFICATION_PROVIDER.

Our own limits on how often a code may be sent are in signup.limits.
"""
import hashlib
import hmac
import logging
import secrets
import time

from django.conf import settings
from django.core.cache import cache
from django.utils.crypto import constant_time_compare
from django.utils.module_loading import import_string

from accounts.crypto import email_index, phone_index

logger = logging.getLogger(__name__)

# Channels a code is sent over.
EMAIL = 'email'
WHATSAPP = 'whatsapp'
SMS = 'sms'
CHANNELS = (EMAIL, WHATSAPP, SMS)

# Kinds of contact: an email address, or a mobile number (E.164) reached over WhatsApp
# or SMS. A mobile has one code, whichever of its channels carried it.
MOBILE = 'mobile'
CONTACT_KINDS = {EMAIL: EMAIL, WHATSAPP: MOBILE, SMS: MOBILE}

# What a code is for; part of the code derivation, so a code for one purpose fails another.
PURPOSE_SIGNUP = 'signup'
# Resuming a signup in progress (signup entry 5): a signup code does not work here.
PURPOSE_RESUME = 'resume'

CODE_LENGTH = 6
# How long a code is valid after it was first sent.
CODE_TTL_SECONDS = 600
# Wrong attempts that invalidate a code.
MAX_CHECK_ATTEMPTS = 5

# `check` results.
APPROVED = 'approved'
WRONG = 'wrong'
NO_CODE = 'no_code'  # no valid code: none sent, expired, invalidated or already used

# Fixed numbers the fake provider treats specially, so staging can exercise both paths:
# WhatsApp cannot deliver to the first (the code goes by SMS); every send to the second
# fails. Both are valid Spanish mobiles.
FAKE_NO_WHATSAPP_NUMBER = '+34699000001'
FAKE_SEND_FAILS_NUMBER = '+34699000002'


class VerificationError(Exception):
    """A code could not be sent."""


class ChannelUnavailable(VerificationError):
    """The channel cannot deliver to this contact (e.g. the number has no WhatsApp)."""


class SendFailed(VerificationError):
    """The provider, or the network to it, failed."""


def contact_kind(channel):
    """EMAIL or MOBILE for a channel; ValueError for an unknown one."""
    try:
        return CONTACT_KINDS[channel]
    except KeyError:
        raise ValueError(f'Unknown verification channel: {channel!r}') from None


def contact_index(kind, contact):
    """The contact's blind index (accounts.crypto), so no plain contact is ever a key."""
    return email_index(contact) if kind == EMAIL else phone_index(contact)


def _now():
    return time.time()


class VerificationProvider:
    """Interface for verification-code providers."""

    def send(self, channel, contact, purpose, language):
        """Send a code to `contact` (an email address or a phone number) over `channel`,
        in `language`. Raises ChannelUnavailable when the channel cannot deliver to the
        contact, SendFailed when the provider or the network fails."""
        raise NotImplementedError

    def check(self, channel, contact, purpose, code):
        """APPROVED, WRONG or NO_CODE for `code` typed for this contact and purpose
        (`channel` gives the kind of contact: a mobile's code is the same over WhatsApp
        and SMS). An approved code is used up."""
        raise NotImplementedError

    @staticmethod
    def _validate_channel(channel):
        contact_kind(channel)


class FakeVerificationProvider(VerificationProvider):
    """Development and staging stand-in that emulates Twilio Verify's code rules.

    Per (kind of contact, contact's blind index, purpose) the cache (CACHES['default'])
    holds a random nonce, the time the code was first sent and the wrong attempts; the
    code is HMAC(SECRET_KEY, kind|index|purpose|nonce) reduced to 6 digits. Neither the
    code nor the plain contact is stored. The log line in `send` is the one place a code
    is ever written (that logger is kept out of Sentry).

    A send while the code is valid repeats it; a code is valid for CODE_TTL_SECONDS
    from its first send, until approved, or until MAX_CHECK_ATTEMPTS wrong attempts.
    FAKE_NO_WHATSAPP_NUMBER and FAKE_SEND_FAILS_NUMBER simulate the two send failures.
    """

    def send(self, channel, contact, purpose, language):
        kind = contact_kind(channel)
        if kind == MOBILE and contact == FAKE_SEND_FAILS_NUMBER:
            raise SendFailed('fake provider: send fails for this test number')
        if channel == WHATSAPP and contact == FAKE_NO_WHATSAPP_NUMBER:
            raise ChannelUnavailable('fake provider: no WhatsApp for this test number')
        key = self._key(kind, contact, purpose)
        now = _now()
        record = self._valid_record(key, now)
        if record is None:
            record = {'nonce': secrets.token_hex(16), 'sent_at': now, 'wrong': 0}
            self._store(key, record, now)
        code = self._code(kind, contact, purpose, record['nonce'])
        # The only place a code is ever written. This logger is kept out of Sentry.
        logger.info('Verification code for %s %s (%s, %s): %s',
                    channel, contact, purpose, language, code)

    def check(self, channel, contact, purpose, code):
        kind = contact_kind(channel)
        key = self._key(kind, contact, purpose)
        now = _now()
        record = self._valid_record(key, now)
        if record is None:
            return NO_CODE
        code = (code or '').strip()
        expected = self._code(kind, contact, purpose, record['nonce'])
        if len(code) == CODE_LENGTH and code.isdigit() and constant_time_compare(code, expected):
            cache.delete(key)  # used up, as Twilio's approved verification
            return APPROVED
        record['wrong'] += 1
        if record['wrong'] >= MAX_CHECK_ATTEMPTS:
            cache.delete(key)
            return NO_CODE
        self._store(key, record, now)
        return WRONG

    @classmethod
    def current_code(cls, channel, contact, purpose):
        """The valid code for this contact and purpose, or None. For tests only."""
        kind = contact_kind(channel)
        record = cls._valid_record(cls._key(kind, contact, purpose), _now())
        return None if record is None else cls._code(kind, contact, purpose, record['nonce'])

    @staticmethod
    def _key(kind, contact, purpose):
        return f'signup:fake-otp:{kind}:{contact_index(kind, contact)}:{purpose}'

    @staticmethod
    def _valid_record(key, now):
        record = cache.get(key)
        if not isinstance(record, dict) or now - record['sent_at'] >= CODE_TTL_SECONDS:
            return None
        return record

    @staticmethod
    def _store(key, record, now):
        # The cache's own expiry is only housekeeping; validity is checked on `sent_at`.
        cache.set(key, record, timeout=max(1, int(record['sent_at'] + CODE_TTL_SECONDS - now) + 60))

    @staticmethod
    def _code(kind, contact, purpose, nonce):
        message = f'{kind}|{contact_index(kind, contact)}|{purpose}|{nonce}'.encode()
        digest = hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).digest()
        return f'{int.from_bytes(digest, "big") % 10 ** CODE_LENGTH:0{CODE_LENGTH}d}'


def get_provider():
    """An instance of the provider class named by settings.VERIFICATION_PROVIDER."""
    return import_string(settings.VERIFICATION_PROVIDER)()
