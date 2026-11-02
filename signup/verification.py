"""Verification codes for signup: the email, the mobile (WhatsApp, else SMS) and the
resume code; and the login code (accounts.views, the second factor).

The code moved to mytp-core 0.4.0 (`mytp_core.verification`, epic-admin-access), where
the admin site's second factor reuses it; this module re-exports it under the old names
and keeps the portal's fake: the provider interface, the channels and code rules (6
digits, valid for 10 minutes, a send within that time repeats the same code, 5 wrong
attempts invalidate it), the errors, `contact_kind`, `contact_index` and `get_provider()`,
which returns the class named by the setting VERIFICATION_PROVIDER: this module's fake
(FAKE_PROVIDER; development and tests) or Twilio Verify
(signup.twilio_verify.TWILIO_PROVIDER; staging and production). Our database never holds
a code.

The portal's fake keeps its logger (`signup.verification`: the code line is read from it
in development, and it is kept out of Sentry), its cache prefix (`signup:fake-otp:`) and
this module's clock `_now` (tests patch it).

Our own limits on how often a code may be sent are in signup.limits; the send flow with
them is signup.steps.send_code.
"""
import logging
import time

from mytp_core import verification as _core
from mytp_core.verification import (  # noqa: F401 - re-exported
    APPROVED,
    CHANNELS,
    CODE_LENGTH,
    CODE_TTL_SECONDS,
    CONTACT_KINDS,
    EMAIL,
    FAKE_NO_WHATSAPP_NUMBER,
    FAKE_SEND_FAILS_NUMBER,
    MAX_CHECK_ATTEMPTS,
    MOBILE,
    NO_CODE,
    PURPOSE_LOGIN,
    PURPOSE_RESUME,
    PURPOSE_SIGNUP,
    SMS,
    WHATSAPP,
    WRONG,
    ChannelUnavailable,
    CheckFailed,
    SendFailed,
    VerificationError,
    VerificationProvider,
    contact_index,
    contact_kind,
    get_provider,
)

logger = logging.getLogger(__name__)

# The dotted path of this module's fake, for VERIFICATION_PROVIDER.
FAKE_PROVIDER = 'signup.verification.FakeVerificationProvider'


def _now():
    return time.time()


class FakeVerificationProvider(_core.FakeVerificationProvider):
    """mytp-core's fake (it emulates Twilio Verify's code rules) on the portal's logger,
    cache keys and clock. The log line in `send` is the one place a code is ever written
    (that logger is kept out of Sentry)."""

    logger_name = __name__
    cache_prefix = 'signup:fake-otp:'

    @classmethod
    def _now(cls):
        return _now()  # this module's, looked up at call time (tests patch it)
