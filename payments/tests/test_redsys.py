"""The Redsys adapter (epic-payments entry 3): one test (or more) per row of the plan's
matrix. Redsys is never called: notifications are built and signed here with Redsys's
public test key, and the REST refund goes to a fake `post_json`."""
import base64
import json
import logging
import re
import urllib.error
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from urllib.parse import urlencode

import pytest
from django.core.checks import Error
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, RequestFactory
from django.utils import timezone

from payments import checks, gateways, redsys, service, webhooks
from payments.models import Order, Payment, WebhookLog
from payments.tests.test_checkout import browser, html
from payments.tests.test_service import actor
from payments.tests.test_webhooks import attempt, last_log, paid_hooks
from subscriptions.tests.helpers import doctor as make_doctor

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('clear_cache', 'portal_urls')]

KEY = redsys.PUBLIC_TEST_KEY
SITE = 'https://dev.example.test'
ORDER_RE = re.compile(r'^[0-9]{4}[0-9A-Z]{8}$')


@pytest.fixture
def redsys_on(settings):
    settings.REDSYS_MERCHANT_CODE = redsys.PUBLIC_TEST_MERCHANT_CODE
    settings.REDSYS_TERMINAL = '1'
    settings.REDSYS_SECRET_KEY = KEY
    settings.REDSYS_ENVIRONMENT = 'test'
    settings.ROBOTS_NOINDEX = True  # a staging site
    settings.SITE_URL = SITE
    return settings


@pytest.fixture
def sentry(monkeypatch):
    seen = []
    monkeypatch.setattr('sentry_sdk.capture_message',
                        lambda message, **kw: seen.append(message))
    return seen


def madrid(moment):
    local = moment.astimezone(redsys.MADRID)
    return local.strftime('%d/%m/%Y'), local.strftime('%H:%M')


def notification(payment, *, response='0000', cents=None, currency='978', at=None,
                 order=None, transaction_type='0', drop=()):
    """Redsys's notification parameters for `payment` (as Redsys sends them: the date and
    hour URL-encoded inside the JSON)."""
    day, hour = madrid(at or timezone.now())
    data = {
        'Ds_Date': day.replace('/', '%2F'), 'Ds_Hour': hour.replace(':', '%3A'),
        'Ds_SecurePayment': '1', 'Ds_Card_Country': '724',
        'Ds_Amount': str(cents if cents is not None else redsys.to_cents(payment.amount)),
        'Ds_Currency': currency, 'Ds_Order': order or payment.gateway_payment_id,
        'Ds_MerchantCode': redsys.PUBLIC_TEST_MERCHANT_CODE, 'Ds_Terminal': '1',
        'Ds_Response': response, 'Ds_MerchantData': '', 'Ds_TransactionType': transaction_type,
        'Ds_ConsumerLanguage': '1', 'Ds_AuthorisationCode': '123456',
    }
    for name in drop:
        data.pop(name)
    return data


def body_of(data, *, key=KEY, urlsafe=False, signature=None, version=redsys.SIGNATURE_VERSION):
    parameters = redsys.encode_parameters(data)
    signed = signature if signature is not None else redsys.sign(
        str(data.get('Ds_Order', '')), parameters, key)
    if urlsafe:
        signed = signed.replace('+', '-').replace('/', '_')
    return urlencode({'Ds_SignatureVersion': version, 'Ds_MerchantParameters': parameters,
                      'Ds_Signature': signed}).encode('ascii')


def post(body):
    request = RequestFactory().post(
        '/payments/webhook/redsys/', data=body,
        content_type='application/x-www-form-urlencoded', REMOTE_ADDR='192.0.2.10')
    return webhooks.webhook(request, 'redsys')


def notify(payment, **kwargs):
    signing = {name: kwargs.pop(name) for name in ('key', 'urlsafe', 'signature', 'version')
               if name in kwargs}
    body = body_of(notification(payment, **kwargs), **signing)
    return post(body), body


def our_request(merchant_parameters, signature):
    """Our own outgoing parameters, checked as Redsys would (the key from
    DS_MERCHANT_ORDER): notification verification never accepts them."""
    data = redsys.decode_parameters(merchant_parameters)
    assert redsys.sign(data['ds_merchant_order'], merchant_parameters, KEY) == signature
    with pytest.raises(gateways.BadSignature):
        redsys._verify(merchant_parameters, signature, KEY)
    return data


def form_of(page):
    action = re.search(r'<form method="post" action="([^"]+)"[^>]*data-redsys-form', page).group(1)
    fields = dict(re.findall(r'name="(Ds_[A-Za-z]+)" value="([^"]*)"', page))
    return action, fields


# --- Signatures ----------------------------------------------------------------------------------

def test_the_signature_matches_a_known_vector_of_the_public_test_key():
    # Computed independently with OpenSSL (3DES-CBC of the zero-padded order under the
    # base64-decoded key, zero IV; HMAC-SHA256 of the base64 parameters; base64).
    parameters = redsys.encode_parameters(
        {'DS_MERCHANT_AMOUNT': '10000', 'DS_MERCHANT_ORDER': '1234ABCDEFGH'})
    assert parameters == ('eyJEU19NRVJDSEFOVF9BTU9VTlQiOiIxMDAwMCIsIkRTX01FUkNIQU5UX09SREVSIjoi'
                          'MTIzNEFCQ0RFRkdIIn0=')
    assert redsys._order_key('1234ABCDEFGH', KEY).hex() == 'd1d2fc2dbd1183d84e5856360021090a'
    assert redsys.sign('1234ABCDEFGH', parameters, KEY) == \
        'jBwTREdFN8mn/RhnD/qi9UESS8Qp/fGI8QJvu5Z/2rc='


def test_amounts_are_integer_cents():
    assert redsys.to_cents(Decimal('100.00')) == 10000
    assert redsys.to_cents(Decimal('0.10')) == 10
    assert redsys.from_cents('12345') == Decimal('123.45')
    with pytest.raises(ValueError):
        redsys.to_cents(Decimal('1.005'))
    with pytest.raises(ValueError):
        redsys.from_cents('12.5')


# --- Start ---------------------------------------------------------------------------------------

def test_start_sends_the_doctor_to_our_page_which_posts_the_signed_form(
        doctor, entity, redsys_on):
    client = browser(doctor)
    order = service.create_order(doctor, [_line()], actor=actor(doctor))
    assert gateways.offered(order) == ['redsys']
    response = client.post(f'/en/checkout/{order.pk}/', {
        'action': 'pay', 'entity': str(entity.pk), 'gateway': 'redsys',
        'version': order.version, 'total': str(order.total)})
    assert response.status_code == 302
    assert response['Location'] == f'/en/checkout/{order.pk}/redsys/'
    payment = Payment.objects.get(order=order)
    assert payment.gateway == 'redsys' and ORDER_RE.match(payment.gateway_payment_id)
    assert payment.session_expires_at - payment.created_at == timedelta(minutes=30)
    page = html(client.get(response['Location']))
    assert 'data-redsys-submit' in page and 'js/redsys-form.js' in page
    action, fields = form_of(page)
    assert action == 'https://sis-t.redsys.es:25443/sis/realizarPago'
    assert fields['Ds_SignatureVersion'] == 'HMAC_SHA256_V1'
    data = our_request(fields['Ds_MerchantParameters'], fields['Ds_Signature'])
    assert data == {
        'ds_merchant_amount': '10000', 'ds_merchant_order': payment.gateway_payment_id,
        'ds_merchant_merchantcode': '999008881', 'ds_merchant_currency': '978',
        'ds_merchant_transactiontype': '0', 'ds_merchant_terminal': '1',
        'ds_merchant_merchanturl': f'{SITE}/payments/webhook/redsys/',
        'ds_merchant_urlok': f'{SITE}/en/checkout/{order.pk}/redsys/ok/',
        'ds_merchant_urlko': f'{SITE}/en/checkout/{order.pk}/redsys/ko/',
    }
    # No personal data, key or signature in the URLs.
    assert KEY not in page and doctor.email not in fields['Ds_MerchantParameters']


def _line():
    from payments.tests.test_service import line
    return line()


def test_the_form_page_is_the_doctors_own(doctor, entity, redsys_on):
    order, _payment = attempt(doctor, 'redsys')
    other = make_doctor('other@example.com')
    assert browser(other).get(f'/en/checkout/{order.pk}/redsys/').status_code == 404
    anonymous = Client().get(f'/en/checkout/{order.pk}/redsys/')
    assert anonymous.status_code == 302 and 'login' in anonymous['Location']


def test_the_form_page_of_a_timed_out_attempt_goes_to_the_failure_page(
        doctor, entity, redsys_on):
    order, payment = attempt(doctor, 'redsys')
    Payment.objects.filter(pk=payment.pk).update(
        session_expires_at=timezone.now() - timedelta(seconds=1))
    response = browser(doctor).get(f'/en/checkout/{order.pk}/redsys/')
    assert response.status_code == 302 and response['Location'].endswith('/failed/')


def test_start_keeps_the_attempts_order_number_and_redraws_a_collision(
        doctor, entity, redsys_on, monkeypatch):
    _order, payment = attempt(doctor, 'redsys')
    first = payment.gateway_payment_id
    redsys.start(payment, None)
    payment.refresh_from_db()
    assert payment.gateway_payment_id == first  # a second start keeps it
    other_doctor = make_doctor('other@example.com')
    from accounts import fiscal
    from payments.tests.conftest import COMPANY
    fiscal.create(other_doctor, dict(COMPANY))
    from payments.tests.test_service import new
    order = new(other_doctor)
    order = service.start_payment(order, 'redsys', expected_version=order.version,
                                  actor=actor(other_doctor))
    second = service.open_payment(order)
    draws = iter([first, '9999ZZZZZZZZ'])
    monkeypatch.setattr(redsys, 'new_order_id', lambda: next(draws))
    redsys.start(second, None)
    second.refresh_from_db()
    assert second.gateway_payment_id == '9999ZZZZZZZZ'


def test_new_order_numbers_have_the_redsys_shape():
    numbers = {redsys.new_order_id() for _ in range(200)}
    assert len(numbers) == 200 and all(ORDER_RE.match(n) for n in numbers)


# --- Notifications -------------------------------------------------------------------------------

def test_a_paid_notification_is_accepted_and_its_task_pays_the_order_once(
        doctor, entity, redsys_on, events):
    order, payment = attempt(doctor, 'redsys')
    response, body = notify(payment)
    assert response.status_code == 200
    row = last_log()
    assert (row.result, row.status, row.payment_id) == ('accepted', 'paid', payment.pk)
    assert row.event_id == f'{payment.gateway_payment_id}:0000'
    assert (row.amount, row.currency) == (Decimal('100.00'), 'EUR')
    assert row.body_size == len(body)
    assert webhooks.process(row.pk) == 'confirmed'
    assert webhooks.process(row.pk) is None
    assert Order.objects.get(pk=order.pk).state == 'paid'
    assert Payment.objects.get(pk=payment.pk).state == 'confirmed'
    assert paid_hooks(events) == 1


def test_a_response_up_to_0099_is_paid_and_keys_may_differ_in_case(doctor, entity, redsys_on):
    order, payment = attempt(doctor, 'redsys')
    data = {name.upper(): value for name, value in notification(payment, response='0099').items()}
    response = post(body_of({**data, 'Ds_Order': payment.gateway_payment_id}))
    assert response.status_code == 200 and last_log().status == 'paid'


def test_a_refused_notification_fails_the_attempt_and_the_doctor_can_retry(
        doctor, entity, redsys_on, events):
    order, payment = attempt(doctor, 'redsys')
    response, _ = notify(payment, response='0190')
    assert response.status_code == 200
    row = last_log()
    assert (row.result, row.status) == ('accepted', 'failed')
    assert webhooks.process(row.pk) == 'failed'
    assert Payment.objects.get(pk=payment.pk).state == 'failed'
    order = Order.objects.get(pk=order.pk)
    assert order.state == 'pending_payment' and paid_hooks(events) == 0
    client = browser(doctor)
    retry = client.post(f'/en/checkout/{order.pk}/failed/', {
        'gateway': 'redsys', 'version': order.version})
    assert retry['Location'] == f'/en/checkout/{order.pk}/redsys/'
    second = service.open_payment(order)
    assert second.pk != payment.pk and second.gateway_payment_id != payment.gateway_payment_id


def test_a_duplicate_is_answered_200_and_changes_nothing(doctor, entity, redsys_on, events):
    order, payment = attempt(doctor, 'redsys')
    body = body_of(notification(payment))
    assert post(body).status_code == 200
    webhooks.process(last_log().pk)
    version = Order.objects.get(pk=order.pk).version
    assert post(body).status_code == 200
    assert [r.result for r in WebhookLog.objects.order_by('id')] == ['accepted', 'duplicate']
    assert Order.objects.get(pk=order.pk).version == version and paid_hooks(events) == 1


def test_a_wrong_key_or_tampered_parameters_are_a_bad_signature(
        doctor, entity, redsys_on, caplog):
    order, payment = attempt(doctor, 'redsys')
    with caplog.at_level(logging.DEBUG):
        response, wrong_key = notify(payment, key=base64.b64encode(b'x' * 24).decode())
        assert response.status_code == 401
        good = body_of(notification(payment))
        fields = dict(pair.split('=', 1) for pair in good.decode().split('&'))
        tampered_data = notification(payment, cents=1)
        fields['Ds_MerchantParameters'] = redsys.encode_parameters(tampered_data)
        assert post(urlencode(fields).encode()).status_code == 401
    assert [r.result for r in WebhookLog.objects.all()] == ['bad_signature'] * 2
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    assert 'bad_signature' in caplog.text
    for secret in (KEY, fields['Ds_MerchantParameters'], wrong_key.decode()):
        assert secret not in caplog.text


@pytest.mark.parametrize('signature', ['', 'not base64 !!', 'é中', 'AAAA', '=' * 3])
def test_a_malformed_signature_is_a_bad_signature(doctor, entity, redsys_on, signature):
    _order, payment = attempt(doctor, 'redsys')
    response, _ = notify(payment, signature=signature)
    assert response.status_code == 401 and last_log().result == 'bad_signature'


@pytest.mark.parametrize('body', [
    b'', b'nonsense', 'é=中'.encode(), b'\xff\xfe',
    b'Ds_SignatureVersion=HMAC_SHA256_V1&Ds_MerchantParameters=%%%&Ds_Signature=abc',
    b'Ds_SignatureVersion=HMAC_SHA256_V1&Ds_MerchantParameters=bm90IGpzb24%3D&Ds_Signature=a',
    b'Ds_SignatureVersion=HMAC_SHA256_V1&Ds_MerchantParameters=WzFd&Ds_Signature=abc',
])
def test_a_malformed_body_is_a_bad_signature_never_a_500(doctor, entity, redsys_on, body):
    assert post(body).status_code == 401 and last_log().result == 'bad_signature'


def test_another_signature_version_is_refused(doctor, entity, redsys_on):
    _order, payment = attempt(doctor, 'redsys')
    response, _ = notify(payment, version='HMAC_SHA512_V2')
    assert response.status_code == 401


def test_the_url_safe_base64_of_a_valid_signature_is_accepted(doctor, entity, redsys_on):
    _order, payment = attempt(doctor, 'redsys')
    # Find a notification whose signature has a `+` or `/` so the variants differ.
    for cents in range(10000, 10100):
        data = notification(payment, cents=cents)
        signed = redsys.sign(payment.gateway_payment_id, redsys.encode_parameters(data), KEY)
        if '+' in signed or '/' in signed:
            break
    assert post(body_of(data, urlsafe=True)).status_code == 200
    assert last_log().result == 'accepted'
    # A `+` sent unescaped (form decoding turns it into a space) is still the same signature.
    raw = (f'Ds_SignatureVersion=HMAC_SHA256_V1&Ds_MerchantParameters='
           f'{redsys.encode_parameters(data)}&Ds_Signature={signed}').encode()
    assert post(raw).status_code == 200 and last_log().result == 'duplicate'


def test_a_stale_notification_is_refused_within_30_minutes_madrid_time(
        doctor, entity, redsys_on):
    order, payment = attempt(doctor, 'redsys')
    old = timezone.now() - timedelta(minutes=32)
    response, _ = notify(payment, at=old)
    assert response.status_code == 400
    row = last_log()
    assert row.result == 'stale' and row.gateway_payment_id == payment.gateway_payment_id
    ahead = timezone.now() + timedelta(minutes=32)
    assert notify(payment, at=ahead, response='0001')[0].status_code == 400
    recent = timezone.now() - timedelta(minutes=28)
    assert notify(payment, at=recent)[0].status_code == 200
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'


def test_a_signed_notification_without_its_fields_is_refused(doctor, entity, redsys_on):
    _order, payment = attempt(doctor, 'redsys')
    for missing in ('Ds_Response', 'Ds_Amount', 'Ds_Date', 'Ds_Currency'):
        response, _ = notify(payment, drop=(missing,))
        assert response.status_code == 404 and last_log().result == 'unknown'


def test_an_amount_or_currency_mismatch_needs_attention(
        doctor, entity, redsys_on, events, sentry):
    order, payment = attempt(doctor, 'redsys')
    response, _ = notify(payment, cents=100)
    assert response.status_code == 200
    row = last_log()
    assert row.amount == Decimal('1.00')
    assert webhooks.process(row.pk) == 'amount_mismatch'
    assert WebhookLog.objects.get(pk=row.pk).result == 'amount_mismatch'
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    assert Payment.objects.get(pk=payment.pk).state == 'needs_attention'
    assert sentry == [f'payment amount or currency differs: order {order.pk}']
    assert paid_hooks(events) == 0


def test_another_currency_needs_attention(doctor, entity, redsys_on, sentry):
    order, payment = attempt(doctor, 'redsys')
    notify(payment, currency='840')
    assert last_log().currency == '840'
    assert webhooks.process(last_log().pk) == 'amount_mismatch'
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'


def test_an_unknown_order_is_refused(doctor, entity, redsys_on):
    _order, payment = attempt(doctor, 'redsys')
    response, _ = notify(payment, order='0000NOTOURS0')
    assert response.status_code == 404
    assert last_log().result == 'unknown' and last_log().payment is None


def test_a_refund_notification_is_logged_without_a_status(doctor, entity, redsys_on):
    _order, payment = attempt(doctor, 'redsys')
    notify(payment, response='0900', transaction_type='3')
    assert last_log().result == 'accepted' and last_log().status == ''
    assert redsys.fetch_payment(payment).status == gateways.PENDING


# --- Re-read, return and retry -------------------------------------------------------------------

def test_fetch_payment_reads_the_latest_verified_notification(doctor, entity, redsys_on):
    _order, payment = attempt(doctor, 'redsys')
    found = redsys.fetch_payment(payment)
    assert (found.status, found.amount, found.currency) == (
        gateways.PENDING, Decimal('100.00'), 'EUR')
    notify(payment)
    found = redsys.fetch_payment(payment)
    assert (found.gateway_payment_id, found.status, found.amount, found.currency) == (
        payment.gateway_payment_id, gateways.PAID, Decimal('100.00'), 'EUR')


def test_the_return_page_waits_while_no_notification_arrived(doctor, entity, redsys_on):
    order, _payment = attempt(doctor, 'redsys')
    page = html(browser(doctor).get(f'/en/checkout/{order.pk}/return/'))
    assert 'data-waiting' in page


def test_the_return_page_pays_once_the_notification_arrived(
        doctor, entity, redsys_on, events):
    order, payment = attempt(doctor, 'redsys')
    notify(payment)
    page = html(browser(doctor).get(f'/en/checkout/{order.pk}/return/'))
    assert 'data-order-state="paid"' in page and paid_hooks(events) == 1


def test_a_retry_during_the_session_is_refused_until_it_times_out(doctor, entity, redsys_on):
    order, payment = attempt(doctor, 'redsys')
    client = browser(doctor)
    response = client.post(f'/en/checkout/{order.pk}/failed/', {
        'gateway': 'redsys', 'version': order.version}, follow=True)
    assert 'in progress' in response.content.decode()
    assert Payment.objects.filter(order=order).count() == 1
    Payment.objects.filter(pk=payment.pk).update(
        session_expires_at=timezone.now() - timedelta(seconds=1))
    retry = client.post(f'/en/checkout/{order.pk}/failed/', {
        'gateway': 'redsys', 'version': order.version})
    assert retry['Location'] == f'/en/checkout/{order.pk}/redsys/'
    assert Payment.objects.get(pk=payment.pk).state == 'closed'
    second = service.open_payment(order)
    assert second.gateway_payment_id and second.gateway_payment_id != payment.gateway_payment_id


@pytest.mark.parametrize('outcome, target', [('ok', 'return'), ('ko', 'failed')])
def test_the_way_back_answers_303_to_our_get_page(doctor, entity, redsys_on, outcome, target):
    order, _payment = attempt(doctor, 'redsys')
    url = f'/en/checkout/{order.pk}/redsys/{outcome}/'
    redsys_post = Client(enforce_csrf_checks=True)  # no session, no CSRF token
    response = redsys_post.post(url, {'Ds_SignatureVersion': 'x', 'Ds_Signature': 'y'})
    assert response.status_code == 303
    assert response['Location'] == f'/en/checkout/{order.pk}/{target}/'
    assert redsys_post.get(url).status_code == 303
    client = browser(doctor)  # the doctor's GET after the 303 has their session
    assert client.get(response['Location']).status_code == 200
    assert Client().get(f'/en/checkout/{order.pk}/redsys/elsewhere/').status_code == 404


# --- Refunds ------------------------------------------------------------------------------------

class FakeRedsys:
    """`accounts.http.post_json` as Redsys REST answers it."""

    def __init__(self, response='0900', key=KEY, error=None, fault=None, order=None,
                 **overrides):
        self.response, self.key, self.error, self.fault, self.order = (
            response, key, error, fault, order)
        self.overrides = overrides
        self.calls = []

    def __call__(self, url, payload, timeout=None):
        self.calls.append((url, payload, timeout))
        if self.fault:
            raise self.fault
        if self.error:
            return json.dumps({'errorCode': self.error})
        data = our_request(payload['Ds_MerchantParameters'], payload['Ds_Signature'])
        answer = {'Ds_Amount': data['ds_merchant_amount'], 'Ds_Currency': '978',
                  'Ds_Order': self.order or data['ds_merchant_order'],
                  'Ds_MerchantCode': '999008881', 'Ds_Terminal': '1',
                  'Ds_Response': self.response, 'Ds_AuthorisationCode': '654321',
                  'Ds_TransactionType': '3', 'Ds_SecurePayment': '0', 'Ds_Language': '1',
                  'Ds_MerchantData': '', 'Ds_Card_Country': '724', **self.overrides}
        parameters = redsys.encode_parameters(answer)
        return json.dumps({'Ds_SignatureVersion': 'HMAC_SHA256_V1',
                           'Ds_MerchantParameters': parameters,
                           'Ds_Signature': redsys.sign(answer['Ds_Order'], parameters, self.key)})


def paid(doctor):
    order, payment = attempt(doctor, 'redsys')
    notify(payment)
    webhooks.process(last_log().pk)
    payment.refresh_from_db()
    assert payment.state == 'confirmed'
    return Order.objects.get(pk=order.pk), payment


def refund(reference, *args):
    out = StringIO()
    call_command('redsys_refund', reference, *args, stdout=out)
    return out.getvalue()


def test_redsys_refund_refunds_the_full_amount_and_changes_nothing_on_the_order(
        doctor, entity, redsys_on, monkeypatch):
    order, payment = paid(doctor)
    fake = FakeRedsys()
    monkeypatch.setattr('accounts.http.post_json', fake)
    printed = refund(order.reference)
    assert printed.strip().endswith(f'{payment.gateway_payment_id}:654321')
    url, payload, timeout = fake.calls[0]
    assert url == 'https://sis-t.redsys.es:25443/sis/rest/trataPeticionREST'
    assert timeout == redsys.REFUND_TIMEOUT
    data = our_request(payload['Ds_MerchantParameters'], payload['Ds_Signature'])
    assert data == {'ds_merchant_amount': '10000',
                    'ds_merchant_order': payment.gateway_payment_id,
                    'ds_merchant_merchantcode': '999008881', 'ds_merchant_currency': '978',
                    'ds_merchant_transactiontype': '3', 'ds_merchant_terminal': '1'}
    fresh = Order.objects.get(pk=order.pk)
    assert (fresh.state, fresh.version) == ('paid', order.version)
    assert Payment.objects.get(pk=payment.pk).state == 'confirmed'


def test_redsys_refund_takes_a_partial_amount(doctor, entity, redsys_on, monkeypatch):
    order, _payment = paid(doctor)
    fake = FakeRedsys()
    monkeypatch.setattr('accounts.http.post_json', fake)
    assert 'refunded 25.50 EUR' in refund(order.reference, '--amount', '25.50')
    data = redsys.decode_parameters(fake.calls[0][1]['Ds_MerchantParameters'])
    assert data['ds_merchant_amount'] == '2550'
    for wrong in ('0', '100.01', '-1', 'abc', '1.005', 'NaN'):
        with pytest.raises(CommandError):
            refund(order.reference, '--amount', wrong)
    assert len(fake.calls) == 1


@pytest.mark.parametrize('fake', [
    FakeRedsys(response='0950'), FakeRedsys(error='SIS0057'),
    FakeRedsys(key=base64.b64encode(b'y' * 24).decode()), FakeRedsys(order='0000OTHER000'),
    FakeRedsys(fault=urllib.error.URLError('down')), FakeRedsys(fault=ValueError('HTTP 500')),
    FakeRedsys(Ds_Amount='100'), FakeRedsys(Ds_Currency='840'),
    FakeRedsys(Ds_TransactionType='0'),
])
def test_redsys_refund_fails_on_any_other_answer(doctor, entity, redsys_on, monkeypatch,
                                                 caplog, fake):
    order, _payment = paid(doctor)
    monkeypatch.setattr('accounts.http.post_json', fake)
    with caplog.at_level(logging.DEBUG), pytest.raises(CommandError) as raised:
        refund(order.reference)
    assert 'Ds_MerchantParameters' not in str(raised.value) and KEY not in caplog.text
    assert Order.objects.get(pk=order.pk).state == 'paid'


def test_redsys_refund_needs_a_confirmed_redsys_payment(doctor, entity, redsys_on):
    order, _payment = attempt(doctor, 'redsys')
    with pytest.raises(CommandError, match='no confirmed Redsys payment'):
        refund(order.reference)
    with pytest.raises(CommandError, match='no such order'):
        refund('KM-000000')


# --- Settings and checks ------------------------------------------------------------------------

def test_the_adapter_is_available_only_with_a_code_and_a_key(redsys_on):
    found = gateways.adapter('redsys')
    assert found is not None and not found.supports('cancel_attempt')
    assert not found.supports('save_card') and not found.supports('charge_saved_card')
    assert found.required_settings == ('REDSYS_MERCHANT_CODE', 'REDSYS_TERMINAL',
                                       'REDSYS_SECRET_KEY')
    assert found.timestamp_tolerance == timedelta(minutes=30)
    redsys_on.REDSYS_SECRET_KEY = ''
    assert gateways.adapter('redsys') is None


def test_production_uses_redsys_production_host(redsys_on):
    redsys_on.REDSYS_ENVIRONMENT = 'production'
    assert redsys.payment_url() == 'https://sis.redsys.es/sis/realizarPago'
    assert redsys.rest_url() == 'https://sis.redsys.es/sis/rest/trataPeticionREST'


def public(settings):
    settings.DEBUG = False
    settings.ROBOTS_NOINDEX = False


def test_the_test_environment_or_the_public_test_terminal_is_refused_on_a_public_site(
        redsys_on):
    assert checks.check_redsys_environment() == []  # staging: fine
    public(redsys_on)
    errors = checks.check_redsys_environment()
    assert [e.id for e in errors] == ['payments.E004'] and isinstance(errors[0], Error)
    redsys_on.REDSYS_ENVIRONMENT = 'production'
    assert [e.id for e in checks.check_redsys_environment()] == ['payments.E004']
    redsys_on.REDSYS_SECRET_KEY = base64.b64encode(b'k' * 24).decode()
    assert [e.id for e in checks.check_redsys_environment()] == ['payments.E004']
    redsys_on.REDSYS_MERCHANT_CODE = '123456789'
    assert checks.check_redsys_environment() == []
    redsys_on.REDSYS_SECRET_KEY = ''  # not enabled: nothing to check
    redsys_on.REDSYS_ENVIRONMENT = 'test'
    assert checks.check_redsys_environment() == []


def test_a_missing_terminal_on_a_public_site_is_e003(redsys_on):
    public(redsys_on)
    redsys_on.REDSYS_TERMINAL = ''
    errors = [e for e in checks.check_gateway_secrets() if 'REDSYS_TERMINAL' in e.msg]
    assert [e.id for e in errors] == ['payments.E003']


def test_the_redsys_settings_are_sensitive_for_sentry(settings):
    from mytp_core.sentry import _squash
    fields = {_squash(f) for f in settings.MYTP_SENTRY_SENSITIVE_FIELDS}
    for name in ('REDSYS_SECRET_KEY', 'REDSYS_MERCHANT_CODE', 'Ds_MerchantParameters',
                 'Ds_Signature'):
        assert any(field in _squash(name) for field in fields)


# --- Review fixes -------------------------------------------------------------------------------

@pytest.mark.parametrize('first, second', [('0000', '0190'), ('0190', '0000')])
def test_a_paid_notification_wins_over_a_refusal_of_the_same_order(
        doctor, entity, redsys_on, first, second):
    _order, payment = attempt(doctor, 'redsys')
    notify(payment, response=first)
    notify(payment, response=second)
    found = redsys.fetch_payment(payment)
    assert (found.status, found.amount, found.currency) == (
        gateways.PAID, Decimal('100.00'), 'EUR')


def test_the_latest_refusal_is_failed(doctor, entity, redsys_on):
    _order, payment = attempt(doctor, 'redsys')
    notify(payment, response='0190')
    assert redsys.fetch_payment(payment).status == gateways.FAILED


def _with(payment, **changes):
    data = notification(payment)
    data.update(changes)
    return post(body_of(data))


@pytest.mark.parametrize('changes', [{'Ds_MerchantCode': '123456789'}, {'Ds_Terminal': '2'},
                                     {'Ds_Terminal': 'x'}])
def test_another_merchant_code_or_terminal_is_refused(doctor, entity, redsys_on, changes):
    _order, payment = attempt(doctor, 'redsys')
    assert _with(payment, **changes).status_code == 404
    assert last_log().result == 'unknown'


def test_a_zero_padded_terminal_is_ours(doctor, entity, redsys_on):
    _order, payment = attempt(doctor, 'redsys')
    assert _with(payment, Ds_Terminal='001').status_code == 200
    assert last_log().result == 'accepted'


def test_a_redsys_test_terminal_on_a_public_site_refuses_to_start(redsys_on):
    from django.apps import apps
    from django.core.exceptions import ImproperlyConfigured
    public(redsys_on)
    assert checks.redsys_test_on_public_site()
    with pytest.raises(ImproperlyConfigured, match='payments.E004'):
        apps.get_app_config('payments').ready()
    redsys_on.ROBOTS_NOINDEX = True
    assert not checks.redsys_test_on_public_site()


def test_showing_the_form_moves_the_session_forward(doctor, entity, redsys_on):
    order, payment = attempt(doctor, 'redsys')
    soon = timezone.now() + timedelta(minutes=1)
    Payment.objects.filter(pk=payment.pk).update(session_expires_at=soon)
    before = timezone.now()
    html(browser(doctor).get(f'/en/checkout/{order.pk}/redsys/'))
    expires = Payment.objects.get(pk=payment.pk).session_expires_at
    assert expires >= before + timedelta(minutes=30)
    later = timezone.now() + timedelta(hours=2)  # never moved back
    Payment.objects.filter(pk=payment.pk).update(session_expires_at=later)
    service.extend_session(payment, timedelta(minutes=30))
    assert Payment.objects.get(pk=payment.pk).session_expires_at == later


def test_the_repeated_autumn_hour_reads_either_way(monkeypatch):
    from datetime import datetime, timezone as dt_timezone
    data = {'ds_date': '25%2F10%2F2026', 'ds_hour': '02%3A30'}
    first = datetime(2026, 10, 25, 0, 30, tzinfo=dt_timezone.utc)   # 02:30 CEST (fold 0)
    second = datetime(2026, 10, 25, 1, 30, tzinfo=dt_timezone.utc)  # 02:30 CET (fold 1)
    assert redsys._sent_at(data, first + timedelta(minutes=5)) == first
    assert redsys._sent_at(data, second + timedelta(minutes=5)) == second
