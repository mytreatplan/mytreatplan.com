"""The SMS senders of the fallback (epic-case-lifecycle 10.3), chosen by the `SMS_SENDER`
setting: `FakeSmsSender` (development and tests) and `TwilioSmsSender` (Twilio's Messaging
API through a Messaging Service, urllib as `mytp_core.twilio_verify.post_verify`). A sender
raises `SmsFailed` with the reason kept on the row; it never logs the mobile or the text
(AD-23): only the row's UUID, Twilio's error code and the HTTP status."""
import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.utils.module_loading import import_string
from mytp_core.verification import FETCH_ERRORS

logger = logging.getLogger(__name__)

FAKE_SENDER = 'notifications.sms.FakeSmsSender'
TWILIO_SENDER = 'notifications.sms.TwilioSmsSender'
DEFAULT_BASE_URL = 'https://api.twilio.com/2010-04-01'
# The settings the Twilio sender needs (notifications.E001 names the empty ones).
CREDENTIAL_SETTINGS = ('TWILIO_ACCOUNT_SID', 'TWILIO_API_KEY_SID', 'TWILIO_API_KEY_SECRET',
                       'TWILIO_MESSAGING_SERVICE_SID')
# Socket timeout of the Twilio send, in seconds: longer than EXTERNAL_CHECK_TIMEOUT (3 s, for
# the signup checks), so that a slow but successful answer is not stored as a failure.
SEND_TIMEOUT = 10
# Bodies are small JSON objects; never read more than this.
_MAX_BODY = 65536


class SmsFailed(Exception):
    """The SMS was not sent; `detail` is safe to keep (no mobile, no text)."""

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


class FakeSmsSender:
    """Development and tests: keeps nothing, logs the row's UUID only (the tests record the
    sends by patching `send`)."""

    def send(self, to, body, reference=''):
        logger.info('fake sms for message %s', reference)


def _json(raw):
    try:
        parsed = json.loads(raw or b'{}')
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _error_code(body):
    code = body.get('code')
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    if isinstance(code, str) and code.isdigit():
        return int(code)
    return None


class TwilioSmsSender:
    """POST <base>/Accounts/<sid>/Messages.json with the Messaging Service SID."""

    def send(self, to, body, reference=''):
        base = (settings.TWILIO_MESSAGING_BASE_URL or DEFAULT_BASE_URL).rstrip('/')
        account = urllib.parse.quote(settings.TWILIO_ACCOUNT_SID, safe='')
        credentials = f'{settings.TWILIO_API_KEY_SID}:{settings.TWILIO_API_KEY_SECRET}'
        request = urllib.request.Request(
            f'{base}/Accounts/{account}/Messages.json',
            data=urllib.parse.urlencode({
                'To': to, 'MessagingServiceSid': settings.TWILIO_MESSAGING_SERVICE_SID,
                'Body': body}).encode('utf-8'),
            method='POST', headers={
                'User-Agent': settings.EXTERNAL_CHECK_USER_AGENT,
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
                'Authorization': 'Basic ' + base64.b64encode(credentials.encode()).decode('ascii'),
            })
        try:
            with urllib.request.urlopen(request, timeout=SEND_TIMEOUT):
                return
        except urllib.error.HTTPError as exc:
            try:
                answer = _json(exc.read(_MAX_BODY))
            except FETCH_ERRORS:
                answer = {}
            finally:
                exc.close()
            code = _error_code(answer)
            logger.warning('sms for message %s refused: code %s, HTTP %s', reference,
                           code if code is not None else 'none', exc.code)
            raise SmsFailed(f'twilio {code if code is not None else "none"}') from None
        except FETCH_ERRORS as exc:
            raise SmsFailed(type(exc).__name__) from None


def get_sender():
    return import_string(settings.SMS_SENDER)()
