"""The Redsys adapter (epic-payments entry 3, user decisions 2026-11-19): card payments of
the Iberian company (5.9's rule: `redsys` then `bank_transfer`) on Redsys's hosted payment
page (redirection, HMAC_SHA256_V1).

- `start`: gives the attempt a fresh `Ds_Order` (4 digits and 8 of `[0-9A-Z]`, redrawn on a
  collision; the attempt's `gateway_payment_id`) and returns our own page
  (`payments.views.redsys_gateway`), which renders the signed form that posts itself to
  Redsys's `realizarPago`. Every URL sent to Redsys (the notification, UrlOK, UrlKO) is
  built from `settings.SITE_URL`, never from the request.
- `verify_webhook`: Redsys's signed online notification (a form-encoded POST of
  `Ds_SignatureVersion`, `Ds_MerchantParameters`, `Ds_Signature`) through the shared
  handler (`payments.webhooks.handle`). The signature is checked with our key (bytes,
  `hmac.compare_digest`, URL-safe and standard base64 alike), then `Ds_Date`/`Ds_Hour`
  (Europe/Madrid) within 30 minutes. The event id is `Ds_Order:Ds_Response`; a
  `Ds_Response` of 0000-0099 is paid, any other failed.
- `fetch_payment`: the verified signed notification is the record of the payment (no
  SOAP query): a verified paid notification of the attempt wins, else the latest failed
  one (status, amount and currency, from `payments.models.WebhookLog`), else PENDING.
- `refund`: a refund (transaction type 3) over Redsys REST (`trataPeticionREST`); the signed
  response is verified and must say `Ds_Response` 0900. No order state change.

There is no `cancel_attempt` (Redsys cannot close a session): a retry is refused while the
attempt may still be paid (`session_lifetime`), or until a refusal notification failed it.

Saved cards and repeat charges (entry 11; Santander's "Servicios Venta +", COF/MIT):
- `form` of an order that asks to save the card (`Order.save_card`) adds the COF "initial"
  fields: `DS_MERCHANT_IDENTIFIER=REQUIRED`, `DS_MERCHANT_COF_INI=S`, `DS_MERCHANT_COF_TYPE=R`.
- `verify_webhook` reads the card Redsys saved (`Ds_Merchant_Identifier`, the token;
  `Ds_Merchant_Cof_Txnid`; `Ds_ExpiryDate` YYMM; `Ds_Card_Number` masked; `Ds_Card_Brand`)
  into `WebhookEvent.card`; `payments.webhooks` stores it (`payments.repeat.store_card`).
- `charge_saved_card`: a merchant-initiated charge (REST, transaction type 0) of the token
  with a new `Ds_Order` (stored on the attempt before the call): `DS_MERCHANT_IDENTIFIER`,
  `COF_INI=N`, `COF_TYPE=R`, `COF_TXNID`, `DIRECTPAYMENT=true`, `EXCEP_SCA=MIT`. The signed
  answer must match the request (order, amount, currency, type): `Ds_Response` 0000-0099 is
  paid, any other a decline with that code; no answer, an unsigned or mismatched one or an
  `errorCode` is `GatewayUnknown`.
The COF field names come from ActiveMerchant's `redsys_rest.rb` and creagia/redsys-php (Redsys's
own documentation was not reachable when this was built): confirm them on the test terminal.

Settings (settings.env): REDSYS_MERCHANT_CODE (the FUC), REDSYS_TERMINAL, REDSYS_SECRET_KEY
(the SHA-256 key, base64) and REDSYS_ENVIRONMENT (`test` or `production`, default `test`).
The adapter is available when the code and the key are set. Nothing of the key, the
signatures, `Ds_MerchantParameters` or card data is ever logged (AD-23).
"""
import base64
import binascii
import hashlib
import hmac
import json
import logging
import secrets
import string
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, unquote
from zoneinfo import ZoneInfo

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from . import gateways

logger = logging.getLogger(__name__)

NAME = 'redsys'
SIGNATURE_VERSION = 'HMAC_SHA256_V1'
TEST, PRODUCTION = 'test', 'production'
HOSTS = {TEST: 'https://sis-t.redsys.es:25443', PRODUCTION: 'https://sis.redsys.es'}
PAYMENT_PATH = '/sis/realizarPago'
REST_PATH = '/sis/rest/trataPeticionREST'
# Redsys's public test terminal (published by Redsys; not a secret): refused on a public
# site (payments.E004).
PUBLIC_TEST_MERCHANT_CODE = '999008881'
PUBLIC_TEST_KEY = 'sq7HjrUOBfKmC576ILgskD5srU870gJ7'

CURRENCIES = {'978': 'EUR'}
CURRENCY_CODES = {code: number for number, code in CURRENCIES.items()}
AUTHORISATION, REFUND = '0', '3'
REFUNDED = '0900'
MADRID = ZoneInfo('Europe/Madrid')
SESSION_LIFETIME = timedelta(minutes=30)
TIMESTAMP_TOLERANCE = timedelta(minutes=30)
REFUND_TIMEOUT = 30
CHARGE_TIMEOUT = 30
# Ds_Card_Brand codes (Redsys): the names shown with a saved card's last 4 digits.
CARD_BRANDS = {'1': 'VISA', '2': 'MASTERCARD', '6': 'DINERS', '7': 'PRIVATE', '8': 'AMEX',
               '9': 'JCB', '22': 'UPI'}
ORDER_ALPHABET = string.digits + string.ascii_uppercase
_ORDER_TRIES = 5


# --- Settings ----------------------------------------------------------------------------------

def merchant_code():
    return str(getattr(settings, 'REDSYS_MERCHANT_CODE', '') or '').strip()


def terminal():
    return str(getattr(settings, 'REDSYS_TERMINAL', '') or '').strip()


def secret_key():
    return str(getattr(settings, 'REDSYS_SECRET_KEY', '') or '').strip()


def environment():
    value = str(getattr(settings, 'REDSYS_ENVIRONMENT', TEST) or TEST).strip().lower()
    return PRODUCTION if value == PRODUCTION else TEST


def enabled():
    return bool(merchant_code() and secret_key())


def payment_url():
    return HOSTS[environment()] + PAYMENT_PATH


def rest_url():
    return HOSTS[environment()] + REST_PATH


# --- Signatures --------------------------------------------------------------------------------

def _b64decode(text):
    """Standard or URL-safe base64 (padding optional) to bytes; ValueError when it is not."""
    if isinstance(text, bytes):
        text = text.decode('ascii')
    text = text.strip().replace('-', '+').replace('_', '/').replace(' ', '+')
    text += '=' * (-len(text) % 4)
    try:
        return base64.b64decode(text.encode('ascii'), validate=True)
    except (binascii.Error, UnicodeEncodeError) as error:
        raise ValueError('not base64') from error


def _normalise(signature):
    """A signature in standard base64, as bytes (URL-safe `-_` become `+/`; a `+` turned into
    a space by form decoding is put back)."""
    return signature.strip().replace(' ', '+').replace('-', '+').replace('_', '/') \
        .rstrip('=').encode('ascii')


def _order_key(ds_order, key=None):
    """The per-order key: `Ds_Order` (zero-padded to 8 bytes) encrypted with 3DES-CBC, a zero
    IV and the merchant key (base64-decoded)."""
    merchant_key = _b64decode(secret_key() if key is None else key)
    data = ds_order.encode('ascii')
    data += b'\0' * (-len(data) % 8)
    encryptor = Cipher(TripleDES(merchant_key), modes.CBC(b'\0' * 8)).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def sign(ds_order, merchant_parameters, key=None):
    """The signature (standard base64) of `merchant_parameters` (the base64 text) for the
    order `ds_order`."""
    digest = hmac.new(_order_key(ds_order, key), merchant_parameters.encode('ascii'),
                      hashlib.sha256).digest()
    return base64.b64encode(digest).decode('ascii')


def _our_signature(ds_order, merchant_parameters):
    """`sign` with our key; a key that is not a 3DES key is a `GatewayError`."""
    try:
        return sign(ds_order, merchant_parameters)
    except ValueError:
        raise gateways.GatewayError('REDSYS_SECRET_KEY is not a valid key') from None


def encode_parameters(parameters):
    return base64.b64encode(
        json.dumps(parameters, separators=(',', ':')).encode('utf-8')).decode('ascii')


def decode_parameters(merchant_parameters):
    """The decoded parameters, keys lowercased (Redsys's case varies); ValueError when they
    are not base64 JSON of an object."""
    data = json.loads(_b64decode(merchant_parameters).decode('utf-8'))
    if not isinstance(data, dict):
        raise ValueError('not an object')
    return {str(name).lower(): value for name, value in data.items()}


def _verify(merchant_parameters, signature, key=None):
    """The decoded parameters when `signature` is ours for them, else `BadSignature` (any
    malformed value included)."""
    if not isinstance(merchant_parameters, str) or not isinstance(signature, str) \
            or not merchant_parameters or not signature:
        raise gateways.BadSignature('no signature')
    try:
        data = decode_parameters(merchant_parameters)
        # The key derives from `Ds_Order` only (a notification or a REST answer); our own
        # outgoing parameters (`DS_MERCHANT_ORDER`) never pass.
        ds_order = str(data.get('ds_order') or '')
        if not ds_order or not ds_order.isascii() or len(ds_order) > 12:
            raise ValueError('no order')
        expected = _normalise(sign(ds_order, merchant_parameters, key))
        given = _normalise(signature)
    except (ValueError, UnicodeError, TypeError) as error:
        raise gateways.BadSignature('malformed signature') from error
    if not hmac.compare_digest(expected, given):
        raise gateways.BadSignature('wrong signature')
    return data


def _currency_code(currency):
    try:
        return CURRENCY_CODES[currency]
    except KeyError:
        raise gateways.GatewayError(f'Redsys is not set up for {currency[:3]}') from None


# --- Amounts -----------------------------------------------------------------------------------

def to_cents(amount):
    """`amount` (a Decimal of at most 2 places) in integer cents."""
    cents = Decimal(amount) * 100
    if cents != cents.to_integral_value():
        raise ValueError('more than two decimal places')
    return int(cents)


def from_cents(cents):
    text = str(cents).strip()
    if not text.isdigit():
        raise ValueError('not an amount in cents')
    return (Decimal(int(text)) / 100).quantize(Decimal('0.01'))


# --- The adapter's operations ------------------------------------------------------------------

def new_order_id():
    """A fresh `Ds_Order`: 4 digits and 8 of [0-9A-Z]."""
    return (''.join(secrets.choice(string.digits) for _ in range(4))
            + ''.join(secrets.choice(ORDER_ALPHABET) for _ in range(8)))


def start(payment, request):
    """Give the attempt its `Ds_Order` (once: a second call keeps it) and return our page
    that posts the signed form to Redsys."""
    from . import service
    if not payment.gateway_payment_id:
        for _ in range(_ORDER_TRIES):
            try:
                with transaction.atomic():
                    service.set_gateway_payment_id(payment, new_order_id())
                break
            except IntegrityError:
                payment.gateway_payment_id = ''
        else:
            raise gateways.GatewayError('no free Redsys order number')
    return reverse('checkout_redsys', args=[payment.order_id])


def _site_url(path):
    return settings.SITE_URL.rstrip('/') + path


def form(payment):
    """The hosted page's form for the open attempt `payment`: the action URL and the three
    fields."""
    parameters = {
        'DS_MERCHANT_AMOUNT': str(to_cents(payment.amount)),
        'DS_MERCHANT_ORDER': payment.gateway_payment_id,
        'DS_MERCHANT_MERCHANTCODE': merchant_code(),
        'DS_MERCHANT_CURRENCY': _currency_code(payment.currency),
        'DS_MERCHANT_TRANSACTIONTYPE': AUTHORISATION,
        'DS_MERCHANT_TERMINAL': terminal(),
        'DS_MERCHANT_MERCHANTURL': _site_url(reverse('payments_webhook', args=[NAME])),
        'DS_MERCHANT_URLOK': _site_url(reverse('checkout_redsys_back',
                                               args=[payment.order_id, 'ok'])),
        'DS_MERCHANT_URLKO': _site_url(reverse('checkout_redsys_back',
                                               args=[payment.order_id, 'ko'])),
    }
    if payment.order.save_card:
        # COF initial (entry 11): Redsys saves the card and returns its token.
        parameters.update({'DS_MERCHANT_IDENTIFIER': 'REQUIRED', 'DS_MERCHANT_COF_INI': 'S',
                           'DS_MERCHANT_COF_TYPE': 'R'})
    merchant_parameters = encode_parameters(parameters)
    return {
        'action': payment_url(),
        'Ds_SignatureVersion': SIGNATURE_VERSION,
        'Ds_MerchantParameters': merchant_parameters,
        'Ds_Signature': _our_signature(payment.gateway_payment_id, merchant_parameters),
    }


def _field(fields, name):
    values = fields.get(name) or []
    if len(values) != 1:
        return ''
    return values[0].replace(' ', '+')  # form decoding turns an unescaped `+` into a space


def _sent_at(data, now):
    """The notification's `Ds_Date` (dd/mm/yyyy) and `Ds_Hour` (HH:MM), in Madrid. In the
    hour repeated in autumn both readings (fold 0 and 1) are possible: the one nearer to
    `now` is taken."""
    day = unquote(str(data.get('ds_date', '')))
    hour = unquote(str(data.get('ds_hour', '')))
    naive = datetime.strptime(f'{day} {hour}', '%d/%m/%Y %H:%M')
    readings = (naive.replace(tzinfo=MADRID, fold=fold).astimezone(dt_timezone.utc)
                for fold in (0, 1))
    return min(readings, key=lambda sent_at: abs(now - sent_at))


def _same_terminal(given, ours):
    """Terminals compared as integers (Redsys may send `1` or `001`)."""
    try:
        return int(str(given).strip()) == int(str(ours).strip())
    except ValueError:
        return False


def _status(response_code):
    code = int(response_code)
    return gateways.PAID if 0 <= code <= 99 else gateways.FAILED


def _card(data):
    """The card Redsys saved with a payment (COF initial), as `payments.repeat.store_card`
    takes it, or None when the notification carries no token."""
    token = str(data.get('ds_merchant_identifier') or '').strip()
    if not token:
        return None
    brand = str(data.get('ds_card_brand') or '').strip()
    return {'token': token, 'cof_txnid': str(data.get('ds_merchant_cof_txnid') or '').strip(),
            'number': str(data.get('ds_card_number') or '').strip(),
            'brand': CARD_BRANDS.get(brand.lstrip('0'), brand[:20]),
            'expiry': str(data.get('ds_expirydate') or '').strip()}


def verify_webhook(request):
    try:
        fields = parse_qs(request.body.decode('ascii'), keep_blank_values=True,
                          strict_parsing=False, max_num_fields=50)
    except (UnicodeDecodeError, ValueError) as error:
        raise gateways.BadSignature('not a Redsys notification') from error
    if _field(fields, 'Ds_SignatureVersion') != SIGNATURE_VERSION:
        raise gateways.BadSignature('unknown signature version')
    data = _verify(_field(fields, 'Ds_MerchantParameters'), _field(fields, 'Ds_Signature'))
    try:
        if (str(data['ds_merchantcode']).strip() != merchant_code()
                or not _same_terminal(data['ds_terminal'], terminal())):
            raise ValueError('another merchant code or terminal')
        ds_order = str(data['ds_order'])
        response_code = str(data['ds_response']).strip()
        transaction_type = str(data.get('ds_transactiontype', AUTHORISATION)).strip()
        currency = str(data['ds_currency']).strip()
        # Only the authorisation's notification says what happened to the payment; any
        # other (a refund's, when the terminal sends one) is logged without a status.
        status = _status(response_code) if transaction_type == AUTHORISATION else None
        event = gateways.WebhookEvent(
            event_id=f'{ds_order}:{response_code}', payment_id=ds_order,
            amount=from_cents(data['ds_amount']),
            currency=CURRENCIES.get(currency, currency[:3]), status=status,
            card=_card(data) if status == gateways.PAID else None)
        now = timezone.now()
        sent_at = _sent_at(data, now)
    except (KeyError, ValueError, TypeError, InvalidOperation) as error:
        raise gateways.Malformed('not a Redsys notification') from error
    gateways.check_timestamp(gateways.adapter(NAME), sent_at, now, event)
    return event


def fetch_payment(payment):
    """The attempt as its verified notifications (the record of the payment) say: PAID
    when any says paid (a later refusal on the same Ds_Order never hides a charge), else
    the latest failed one, else PENDING."""
    from .models import WebhookLog
    rows = (WebhookLog.objects
            .filter(gateway=NAME, payment=payment, result__in=WebhookLog.DEDUPED)
            .order_by('-received_at', '-id'))
    row = (rows.filter(status=WebhookLog.PAID).first()
           or rows.filter(status=WebhookLog.FAILED).first())
    if row is None:
        return gateways.GatewayPayment(payment.gateway_payment_id, gateways.PENDING,
                                       payment.amount, payment.currency)
    return gateways.GatewayPayment(payment.gateway_payment_id, row.status, row.amount,
                                   row.currency)


def refund(payment, amount):
    """Refund `amount` (a Decimal) of the confirmed `payment` at Redsys (REST, type 3); the
    refund's id (`Ds_Order:Ds_AuthorisationCode`). Raises `GatewayUnknown` on an HTTP fault or an unsigned or mismatched
    answer (the refund may have happened) and `GatewayError` only on a refusal (nothing of
    the response is logged)."""
    from accounts import http
    if not payment.gateway_payment_id:
        raise gateways.GatewayError('the payment has no Redsys order number')
    parameters = {
        'DS_MERCHANT_AMOUNT': str(to_cents(amount)),
        'DS_MERCHANT_ORDER': payment.gateway_payment_id,
        'DS_MERCHANT_MERCHANTCODE': merchant_code(),
        'DS_MERCHANT_CURRENCY': _currency_code(payment.currency),
        'DS_MERCHANT_TRANSACTIONTYPE': REFUND,
        'DS_MERCHANT_TERMINAL': terminal(),
    }
    merchant_parameters = encode_parameters(parameters)
    signature = _our_signature(payment.gateway_payment_id, merchant_parameters)
    try:
        answer = json.loads(http.post_json(rest_url(), {
            'Ds_SignatureVersion': SIGNATURE_VERSION,
            'Ds_MerchantParameters': merchant_parameters,
            'Ds_Signature': signature,
        }, timeout=REFUND_TIMEOUT))
    except http.FETCH_ERRORS as failure:
        raise gateways.GatewayUnknown(
            f'Redsys could not be reached ({type(failure).__name__})') from None
    if not isinstance(answer, dict):
        raise gateways.GatewayUnknown('Redsys answered something unexpected')
    if 'errorCode' in answer:
        code = str(answer['errorCode'])[:10]
        raise gateways.GatewayError(f'Redsys refused the refund ({code})')
    try:
        data = _verify(answer.get('Ds_MerchantParameters'), answer.get('Ds_Signature'))
    except gateways.BadSignature:
        raise gateways.GatewayUnknown('the Redsys answer is not signed with our key') from None
    response_code = str(data.get('ds_response', '')).strip()
    if str(data.get('ds_order', '')) != payment.gateway_payment_id:
        raise gateways.GatewayUnknown('the Redsys answer is for another order')
    if (str(data.get('ds_amount', '')).strip() != parameters['DS_MERCHANT_AMOUNT']
            or str(data.get('ds_currency', '')).strip() != parameters['DS_MERCHANT_CURRENCY']
            or str(data.get('ds_transactiontype', REFUND)).strip() != REFUND):
        raise gateways.GatewayUnknown(
            'the Redsys answer is for another amount or operation')
    if response_code != REFUNDED:
        raise gateways.GatewayError(f'Redsys refused the refund (Ds_Response '
                                    f'{response_code[:4] or "-"})')
    return f'{payment.gateway_payment_id}:{str(data.get("ds_authorisationcode", "")).strip()}'


def _new_charge_order(payment):
    """Give the repeat charge `payment` a fresh `Ds_Order` before Redsys is called (redrawn
    on a collision), in its own short transaction."""
    from . import service
    for _ in range(_ORDER_TRIES):
        try:
            with transaction.atomic():
                service.set_gateway_payment_id(payment, new_order_id())
            return payment.gateway_payment_id
        except IntegrityError:
            payment.gateway_payment_id = ''
    raise gateways.GatewayError('no free Redsys order number')


def charge_saved_card(card, payment):
    """Charge the saved `card` (a `SavedCard` of this gateway) for the open repeat charge
    `payment` without the doctor (REST, type 0, COF/MIT). Returns the `GatewayPayment`
    (PAID, or FAILED with the `Ds_Response` code); raises `GatewayUnknown` on an HTTP fault,
    an `errorCode`, or an unsigned or mismatched answer (the card may have been charged).
    Nothing of the card or the response is logged."""
    from accounts import http
    if card.gateway != NAME or not card.token:
        raise gateways.GatewayError('not a Redsys card')
    ds_order = _new_charge_order(payment)
    parameters = {
        'DS_MERCHANT_AMOUNT': str(to_cents(payment.amount)),
        'DS_MERCHANT_ORDER': ds_order,
        'DS_MERCHANT_MERCHANTCODE': merchant_code(),
        'DS_MERCHANT_CURRENCY': _currency_code(payment.currency),
        'DS_MERCHANT_TRANSACTIONTYPE': AUTHORISATION,
        'DS_MERCHANT_TERMINAL': terminal(),
        'DS_MERCHANT_IDENTIFIER': card.token,
        'DS_MERCHANT_COF_INI': 'N',
        'DS_MERCHANT_COF_TYPE': 'R',
        'DS_MERCHANT_DIRECTPAYMENT': 'true',
        'DS_MERCHANT_EXCEP_SCA': 'MIT',
        'DS_MERCHANT_MERCHANTURL': _site_url(reverse('payments_webhook', args=[NAME])),
    }
    if card.cof_txnid:
        parameters['DS_MERCHANT_COF_TXNID'] = card.cof_txnid
    merchant_parameters = encode_parameters(parameters)
    signature = _our_signature(ds_order, merchant_parameters)
    try:
        answer = json.loads(http.post_json(rest_url(), {
            'Ds_SignatureVersion': SIGNATURE_VERSION,
            'Ds_MerchantParameters': merchant_parameters,
            'Ds_Signature': signature,
        }, timeout=CHARGE_TIMEOUT))
    except http.FETCH_ERRORS as failure:  # a body that is not JSON too (ValueError)
        raise gateways.GatewayUnknown(
            f'Redsys could not be reached ({type(failure).__name__})') from None
    if not isinstance(answer, dict):
        raise gateways.GatewayUnknown('Redsys answered something unexpected')
    if 'errorCode' in answer:
        code = str(answer['errorCode'])[:10]
        raise gateways.GatewayUnknown(f'Redsys answered an error ({code})')
    try:
        data = _verify(answer.get('Ds_MerchantParameters'), answer.get('Ds_Signature'))
    except gateways.BadSignature:
        raise gateways.GatewayUnknown('the Redsys answer is not signed with our key') from None
    if str(data.get('ds_order', '')) != ds_order:
        raise gateways.GatewayUnknown('the Redsys answer is for another order')
    if (str(data.get('ds_amount', '')).strip() != parameters['DS_MERCHANT_AMOUNT']
            or str(data.get('ds_currency', '')).strip() != parameters['DS_MERCHANT_CURRENCY']
            or str(data.get('ds_transactiontype', AUTHORISATION)).strip() != AUTHORISATION):
        raise gateways.GatewayUnknown(
            'the Redsys answer is for another amount or operation')
    response_code = str(data.get('ds_response', '')).strip()
    try:
        status = _status(response_code)
    except ValueError:
        raise gateways.GatewayUnknown('the Redsys answer has no response code') from None
    code = '' if status == gateways.PAID else response_code[:20]
    return gateways.GatewayPayment(ds_order, status, payment.amount, payment.currency, code)


def register():
    gateways.register(
        NAME, start, enabled=enabled, verify_webhook=verify_webhook,
        fetch_payment=fetch_payment, refund=refund, charge_saved_card=charge_saved_card,
        saves_cards=True, session_lifetime=SESSION_LIFETIME,
        timestamp_tolerance=TIMESTAMP_TOLERANCE,
        required_settings=('REDSYS_MERCHANT_CODE', 'REDSYS_TERMINAL', 'REDSYS_SECRET_KEY'))
