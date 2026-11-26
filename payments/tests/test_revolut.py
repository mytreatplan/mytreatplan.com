"""The Revolut adapter (epic-payments entry 4): one test (or more) per row of the plan's
matrix. Revolut is never called: `accounts.http.request_json` is a fake Revolut, and
webhooks are signed here from the documented format."""
import json
import logging
import urllib.error
import uuid
from decimal import Decimal
from io import StringIO

import pytest
from django.core.checks import Error
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory
from django.utils import timezone

from payments import checks, gateways, revolut, service, webhooks
from payments.models import Order, Payment, WebhookLog
from payments.tests.test_checkout import browser, html
from payments.tests.test_service import actor, new
from payments.tests.test_webhooks import last_log, paid_hooks

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('clear_cache', 'portal_urls')]

KEY = 'sk_sandbox_not_a_real_key'
SECRET = 'wsk_test_secret'
SITE = 'https://dev.example.test'


@pytest.fixture
def revolut_on(settings):
    settings.REVOLUT_SECRET_KEY = KEY
    settings.REVOLUT_WEBHOOK_SECRET = SECRET
    settings.REVOLUT_ENVIRONMENT = 'sandbox'
    settings.ROBOTS_NOINDEX = True  # a staging site
    settings.SITE_URL = SITE
    # The Spanish company's order is created only when a method of its own rule is
    # available; the tests then swap its gateways for `revolut` (see `revolut_order`).
    settings.REDSYS_MERCHANT_CODE = '999008881'
    settings.REDSYS_SECRET_KEY = 'sq7HjrUOBfKmC576ILgskD5srU870gJ7'
    return settings


@pytest.fixture
def sentry(monkeypatch):
    seen = []
    monkeypatch.setattr('sentry_sdk.capture_message',
                        lambda message, **kw: seen.append(message))
    return seen


class FakeRevolut:
    """`accounts.http.request_json` as the Merchant API answers it."""

    def __init__(self):
        self.orders, self.calls, self.webhooks = {}, [], {}
        self.fail = {}  # (method, path suffix) -> an exception raised instead
        self.refund_answer = None

    def __call__(self, method, url, payload=None, headers=None, timeout=None, ok=(200, 201)):
        self.calls.append((method, url, payload, headers, timeout))
        path = url.split('revolut.com', 1)[1]
        for (fail_method, suffix), error in self.fail.items():
            if method == fail_method and path.endswith(suffix):
                raise error
        if (method, path) == ('POST', '/api/orders'):
            order_id = str(uuid.uuid4())
            self.orders[order_id] = {
                'id': order_id, 'type': 'payment', 'state': 'pending',
                'amount': payload['amount'], 'currency': payload['currency'],
                'checkout_url': f'https://sandbox-checkout.revolut.com/payment-link/{order_id}'}
            return json.dumps(self.orders[order_id])
        if (method, path) == ('POST', '/api/webhooks'):
            self.webhooks['wh1'] = {'id': 'wh1', **payload}
            return json.dumps({'id': 'wh1', **payload, 'signing_secret': 'wsk_printed_once'})
        if (method, path) == ('GET', '/api/webhooks'):
            return json.dumps(list(self.webhooks.values()))
        if method == 'DELETE' and path.startswith('/api/webhooks/'):
            self.webhooks.pop(path.rsplit('/', 1)[1], None)
            return ''
        parts = path.split('/')
        order = self.orders.get(parts[3]) if len(parts) > 3 else None
        if order is None:
            raise urllib.error.HTTPError(url, 404, 'not found', {}, None)
        if method == 'GET' and len(parts) == 4:
            return json.dumps(order)
        if method == 'POST' and parts[4:] == ['cancel']:
            if order['state'] != 'pending':
                raise urllib.error.HTTPError(url, 422, 'not pending', {}, None)
            order['state'] = 'cancelled'
            return json.dumps(order)
        if method == 'POST' and parts[4:] == ['refund']:
            if self.refund_answer is not None:
                return json.dumps(self.refund_answer)
            return json.dumps({'id': 'refund-1', 'type': 'refund', 'state': 'completed'})
        raise AssertionError(f'unexpected call {method} {path}')

    def calls_of(self, method, suffix):
        return [call for call in self.calls if call[0] == method and call[1].endswith(suffix)]


@pytest.fixture
def fake(monkeypatch, revolut_on):
    fake_revolut = FakeRevolut()
    monkeypatch.setattr('accounts.http.request_json', fake_revolut)
    return fake_revolut


def revolut_order(doctor):
    """A new order that offers Revolut (the Irish company's rule, as the billing rule of the
    region would say)."""
    order = new(doctor)
    Order.objects.filter(pk=order.pk).update(gateways=['revolut'])
    return Order.objects.get(pk=order.pk)


def attempt(doctor):
    order = revolut_order(doctor)
    order = service.start_payment(order, 'revolut', expected_version=order.version,
                                  actor=actor(doctor))
    payment = service.open_payment(order)
    revolut.start(payment, None)
    payment.refresh_from_db()
    return order, payment


def signed(body, *, secret=SECRET, timestamp=None, extra='', version='v1'):
    timestamp = str(timestamp if timestamp is not None else int(timezone.now().timestamp() * 1000))
    signature = f'{version}={revolut.sign(secret, timestamp, body)}'
    return {'HTTP_REVOLUT_SIGNATURE': signature + extra,
            'HTTP_REVOLUT_REQUEST_TIMESTAMP': timestamp}


def body_of(payment, event='ORDER_COMPLETED'):
    return json.dumps({'event': event, 'order_id': payment.gateway_payment_id,
                       'merchant_order_ext_ref': 'KM-1'}).encode()


def post(body, headers):
    request = RequestFactory().post('/payments/webhook/revolut/', data=body,
                                    content_type='application/json', REMOTE_ADDR='192.0.2.10',
                                    **headers)
    return webhooks.webhook(request, 'revolut')


def notify(payment, event='ORDER_COMPLETED', **kwargs):
    body = body_of(payment, event)
    return post(body, signed(body, **kwargs))


# --- Start ----------------------------------------------------------------------------------

def test_start_creates_a_revolut_order_and_sends_the_doctor_to_its_page(
        doctor, entity, fake):
    order = revolut_order(doctor)
    assert gateways.offered(order) == ['revolut']
    client = browser(doctor)
    response = client.post(f'/en/checkout/{order.pk}/', {
        'action': 'pay', 'entity': str(entity.pk), 'gateway': 'revolut',
        'version': order.version, 'total': str(order.total)})
    payment = Payment.objects.get(order=order)
    assert response.status_code == 302
    assert response['Location'] == (
        f'https://sandbox-checkout.revolut.com/payment-link/{payment.gateway_payment_id}')
    method, url, payload, headers, _timeout = fake.calls[0]
    assert (method, url) == ('POST', 'https://sandbox-merchant.revolut.com/api/orders')
    assert payload == {
        'amount': 10000, 'currency': 'EUR',
        'description': f'MyTreatPlan order {order.reference}',
        'merchant_order_data': {'reference': order.reference},
        'redirect_url': f'{SITE}/en/checkout/{order.pk}/return/',
        'expire_pending_after': 'PT30M'}
    assert headers['Authorization'] == f'Bearer {KEY}'
    assert headers['Revolut-Api-Version'] == '2026-09-22'
    assert doctor.email not in json.dumps(payload)
    assert payment.session_expires_at - payment.created_at >= revolut.SESSION_LIFETIME
    assert revolut.SESSION_LIFETIME.total_seconds() == 2100


@pytest.mark.parametrize('error', [
    urllib.error.URLError('down'), TimeoutError('slow'), ValueError('HTTP 500'),
    urllib.error.HTTPError('https://x.revolut.com/api/orders', 400, 'bad', {}, None)])
def test_a_revolut_fault_at_start_says_try_again_and_leaves_no_live_attempt(
        doctor, entity, fake, error):
    order = revolut_order(doctor)
    fake.fail[('POST', '/api/orders')] = error
    client = browser(doctor)
    response = client.post(f'/en/checkout/{order.pk}/', {
        'action': 'pay', 'entity': str(entity.pk), 'gateway': 'revolut',
        'version': order.version, 'total': str(order.total)}, follow=True)
    assert 'try again' in html(response)
    payment = Payment.objects.get(order=order)
    assert payment.state == 'failed' and payment.gateway_payment_id == ''
    assert service.open_payment(order) is None
    del fake.fail[('POST', '/api/orders')]  # the doctor retries at once
    retry = client.post(f'/en/checkout/{order.pk}/failed/', {
        'gateway': 'revolut', 'version': Order.objects.get(pk=order.pk).version})
    assert retry.status_code == 302 and 'sandbox-checkout.revolut.com' in retry['Location']


def test_an_unusable_answer_from_revolut_is_a_gateway_error(doctor, entity, fake, monkeypatch):
    order = revolut_order(doctor)
    order = service.start_payment(order, 'revolut', expected_version=order.version,
                                  actor=actor(doctor))
    payment = service.open_payment(order)
    for answer in ('{}', '[]', json.dumps({'id': 'x', 'checkout_url': 'http://evil.test/'}),
                   json.dumps({'id': 'x', 'checkout_url': 'https://evil.test/'}), 'not json'):
        monkeypatch.setattr('accounts.http.request_json', lambda *a, answer=answer, **k: answer)
        with pytest.raises(gateways.GatewayError):
            revolut.start(payment, None)
    payment.refresh_from_db()
    assert payment.gateway_payment_id == ''


def test_start_keeps_the_revolut_order_of_the_attempt(doctor, entity, fake):
    _order, payment = attempt(doctor)
    again = revolut.start(payment, None)
    assert again.endswith(payment.gateway_payment_id)
    assert len(fake.calls_of('POST', '/api/orders')) == 1


def test_only_euros_are_accepted(doctor, entity, fake):
    order = revolut_order(doctor)
    assert revolut.accepts(order)
    Order.objects.filter(pk=order.pk).update(currency='AED')
    assert gateways.offered(Order.objects.get(pk=order.pk)) == []
    payment = Payment(order_id=order.pk, gateway='revolut', amount=Decimal('1.00'),
                      currency='AED')
    with pytest.raises(gateways.GatewayError):
        revolut.start(payment, None)


# --- Signature vectors --------------------------------------------------------------------

def test_the_signature_matches_a_known_vector():
    body = b'{"event":"ORDER_COMPLETED","order_id":"abc"}'
    assert revolut.sign('wsk_test_secret', '1721000000000', body) == (
        'ffd2916a8c0b75c75866d855678b9e14f9aa2e8f29be5b206fe75e0b69abb2b0')


# --- Paid ---------------------------------------------------------------------------------

def test_a_completed_webhook_is_accepted_and_its_task_pays_the_order_once(
        doctor, entity, fake, events):
    order, payment = attempt(doctor)
    fake.orders[payment.gateway_payment_id]['state'] = 'completed'
    response = notify(payment)
    assert response.status_code == 200
    row = last_log()
    assert (row.result, row.payment_id) == ('accepted', payment.pk)
    assert row.event_id == f'{payment.gateway_payment_id}:ORDER_COMPLETED'
    assert row.status == ''
    assert webhooks.process(row.pk) == 'confirmed'
    assert webhooks.process(row.pk) is None
    assert Order.objects.get(pk=order.pk).state == 'paid'
    assert Payment.objects.get(pk=payment.pk).state == 'confirmed'
    assert paid_hooks(events) == 1


def test_a_webhook_is_not_trusted_when_revolut_still_says_pending(doctor, entity, fake):
    order, payment = attempt(doctor)
    assert notify(payment).status_code == 200
    assert webhooks.process(last_log().pk) == 'retry'
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'


@pytest.mark.parametrize('changes', [{'amount': 9999}, {'currency': 'USD'}])
def test_an_amount_or_currency_mismatch_is_not_paid(
        doctor, entity, fake, sentry, changes):
    order, payment = attempt(doctor)
    fake.orders[payment.gateway_payment_id].update(state='completed', **changes)
    notify(payment)
    assert webhooks.process(last_log().pk) == 'amount_mismatch'
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    assert Payment.objects.get(pk=payment.pk).state == 'needs_attention'


# --- Failed / cancelled -------------------------------------------------------------------

@pytest.mark.parametrize('event, state', [('ORDER_FAILED', 'failed'),
                                          ('ORDER_CANCELLED', 'cancelled')])
def test_a_failed_or_cancelled_order_fails_the_attempt_and_the_doctor_can_retry(
        doctor, entity, fake, event, state):
    order, payment = attempt(doctor)
    fake.orders[payment.gateway_payment_id]['state'] = state
    assert notify(payment, event).status_code == 200
    assert webhooks.process(last_log().pk) == 'failed'
    assert Payment.objects.get(pk=payment.pk).state == 'failed'
    order = Order.objects.get(pk=order.pk)
    assert order.state == 'pending_payment'
    again = service.start_payment(order, 'revolut', expected_version=order.version,
                                  actor=actor(doctor))
    assert again.gateway == 'revolut'


def test_the_states_map_to_the_gateway_statuses(doctor, entity, fake):
    _order, payment = attempt(doctor)
    seen = {}
    for state in ('pending', 'processing', 'authorised', 'completed', 'failed', 'cancelled',
                  'something_new'):
        fake.orders[payment.gateway_payment_id]['state'] = state
        seen[state] = revolut.fetch_payment(payment).status
    assert seen == {'pending': 'pending', 'processing': 'pending', 'authorised': 'pending',
                    'completed': 'paid', 'failed': 'failed', 'cancelled': 'failed',
                    'something_new': 'pending'}
    got = revolut.fetch_payment(payment)
    assert (got.amount, got.currency) == (Decimal('100.00'), 'EUR')


def test_a_refund_order_is_not_a_payment(doctor, entity, fake):
    _order, payment = attempt(doctor)
    fake.orders[payment.gateway_payment_id]['type'] = 'refund'
    with pytest.raises(gateways.GatewayError):
        revolut.fetch_payment(payment)


def test_an_unreadable_revolut_is_a_gateway_error(doctor, entity, fake):
    _order, payment = attempt(doctor)
    fake.fail[('GET', payment.gateway_payment_id)] = urllib.error.URLError('down')
    with pytest.raises(gateways.GatewayError):
        revolut.fetch_payment(payment)


# --- Bad webhooks -------------------------------------------------------------------------

def count(result):
    return WebhookLog.objects.filter(gateway='revolut', result=result).count()


def test_a_wrong_secret_or_tampered_body_is_refused_401(doctor, entity, fake):
    _order, payment = attempt(doctor)
    body = body_of(payment)
    assert post(body, signed(body, secret='another')).status_code == 401
    assert post(body + b' ', signed(body)).status_code == 401
    assert count('bad_signature') == 2


@pytest.mark.parametrize('headers', [
    {}, {'HTTP_REVOLUT_SIGNATURE': ''}, {'HTTP_REVOLUT_SIGNATURE': 'v1='},
    {'HTTP_REVOLUT_SIGNATURE': 'v2=abcd', 'HTTP_REVOLUT_REQUEST_TIMESTAMP': '1'},
    {'HTTP_REVOLUT_SIGNATURE': 'v1=abcd', 'HTTP_REVOLUT_REQUEST_TIMESTAMP': 'later'},
    {'HTTP_REVOLUT_SIGNATURE': 'v1=abcd'},
    {'HTTP_REVOLUT_SIGNATURE': 'v1=éé', 'HTTP_REVOLUT_REQUEST_TIMESTAMP': '1'},
    {'HTTP_REVOLUT_SIGNATURE': ',,v1,=,', 'HTTP_REVOLUT_REQUEST_TIMESTAMP': '1'},
    {'HTTP_REVOLUT_SIGNATURE': 'v1=abcd', 'HTTP_REVOLUT_REQUEST_TIMESTAMP': '9' * 40},
])
def test_a_malformed_header_is_a_bad_signature_never_a_500(doctor, entity, fake, headers):
    _order, payment = attempt(doctor)
    assert post(body_of(payment), headers).status_code == 401


def test_a_missing_signing_secret_refuses_every_webhook(doctor, entity, fake, revolut_on):
    _order, payment = attempt(doctor)
    body = body_of(payment)
    headers = signed(body)
    revolut_on.REVOLUT_WEBHOOK_SECRET = ''
    assert post(body, headers).status_code == 401


def test_one_valid_entry_among_several_is_enough_and_a_rotated_secret_works(
        doctor, entity, fake, revolut_on):
    _order, payment = attempt(doctor)
    body = body_of(payment)
    timestamp = str(int(timezone.now().timestamp() * 1000))
    other = revolut.sign('old-secret', timestamp, body)
    headers = {'HTTP_REVOLUT_SIGNATURE': f'v1={other}, v1={revolut.sign(SECRET, timestamp, body)}',
               'HTTP_REVOLUT_REQUEST_TIMESTAMP': timestamp}
    assert post(body, headers).status_code == 200
    revolut_on.REVOLUT_WEBHOOK_SECRET = f'brand-new, {SECRET}'
    second = body_of(payment, 'ORDER_FAILED')
    assert post(second, signed(second)).status_code == 200
    assert count('accepted') == 2


def test_a_stale_or_future_timestamp_is_refused_400_within_five_minutes(
        doctor, entity, fake):
    _order, payment = attempt(doctor)
    now = int(timezone.now().timestamp() * 1000)
    assert notify(payment, timestamp=now - 6 * 60 * 1000).status_code == 400
    assert notify(payment, timestamp=now + 6 * 60 * 1000).status_code == 400
    assert count('stale') == 2
    assert notify(payment, timestamp=now - 4 * 60 * 1000).status_code == 200


@pytest.mark.parametrize('body', [b'not json', b'[]', b'{}', b'{"event":"ORDER_FAILED"}',
                                  b'{"order_id":"x"}', b'{"event":1,"order_id":2}',
                                  b'\xff\xfe'])
def test_a_signed_body_that_is_not_a_revolut_event_is_refused(doctor, entity, fake, body):
    assert post(body, signed(body)).status_code == 404  # shared handler: logged unknown
    assert count('unknown') == 1 and count('accepted') == 0


def test_an_unknown_order_id_is_logged_and_ignored(doctor, entity, fake):
    body = json.dumps({'event': 'ORDER_COMPLETED', 'order_id': 'no-such-order'}).encode()
    response = post(body, signed(body))
    assert response.status_code == 404 and count('unknown') == 1


def test_the_signature_rate_limit_is_the_shared_one(doctor, entity, fake):
    _order, payment = attempt(doctor)
    body = body_of(payment)
    for _ in range(webhooks.FAILED_SIGNATURE_LIMIT):
        assert post(body, signed(body, secret='x')).status_code == 401
    assert post(body, signed(body)).status_code == 429
    assert count('rate_limited') == 1


# --- Duplicate ----------------------------------------------------------------------------

def test_a_resent_event_is_a_duplicate_answered_200(doctor, entity, fake, events):
    _order, payment = attempt(doctor)
    fake.orders[payment.gateway_payment_id]['state'] = 'completed'
    assert notify(payment).status_code == 200
    assert notify(payment).status_code == 200
    assert (count('accepted'), count('duplicate')) == (1, 1)
    webhooks.process(WebhookLog.objects.get(result='accepted').pk)
    assert paid_hooks(events) == 1


# --- Retry while live ---------------------------------------------------------------------

def test_a_retry_while_live_cancels_the_pending_revolut_order_first(doctor, entity, fake):
    order, payment = attempt(doctor)
    again = service.start_payment(order, 'revolut', expected_version=order.version,
                                  actor=actor(doctor))
    assert again.gateway == 'revolut'
    assert fake.orders[payment.gateway_payment_id]['state'] == 'cancelled'
    assert Payment.objects.get(pk=payment.pk).state == 'closed'
    assert fake.calls_of('POST', '/cancel')[0][1].startswith(
        'https://sandbox-merchant.revolut.com/api/orders/')
    new_payment = service.open_payment(again)
    revolut.start(new_payment, None)
    new_payment.refresh_from_db()
    assert new_payment.gateway_payment_id != payment.gateway_payment_id


def test_a_cancel_refused_for_a_completed_order_leaves_the_attempt_open(doctor, entity, fake):
    order, payment = attempt(doctor)
    fake.orders[payment.gateway_payment_id]['state'] = 'completed'
    with pytest.raises(service.AttemptOpen):
        service.start_payment(order, 'revolut', expected_version=order.version,
                              actor=actor(doctor))
    assert Payment.objects.get(pk=payment.pk).state == 'open'
    # The existing re-check decides: it pays the order.
    assert webhooks.reread_and_confirm(payment) == service.CONFIRMED
    assert Order.objects.get(pk=order.pk).state == 'paid'


def test_a_cancel_refused_for_an_order_already_failed_counts_as_closed(
        doctor, entity, fake):
    order, payment = attempt(doctor)
    fake.orders[payment.gateway_payment_id]['state'] = 'failed'
    again = service.start_payment(order, 'revolut', expected_version=order.version,
                                  actor=actor(doctor))
    assert again.gateway == 'revolut'
    assert Payment.objects.get(pk=payment.pk).state == 'closed'


def test_a_cancel_that_cannot_reach_revolut_leaves_the_attempt_open(doctor, entity, fake):
    order, payment = attempt(doctor)
    fake.fail[('POST', '/cancel')] = urllib.error.URLError('down')
    with pytest.raises(service.AttemptOpen):
        service.start_payment(order, 'revolut', expected_version=order.version,
                              actor=actor(doctor))
    assert Payment.objects.get(pk=payment.pk).state == 'open'


# --- Refunds ------------------------------------------------------------------------------

def paid(doctor, fake):
    order, payment = attempt(doctor)
    fake.orders[payment.gateway_payment_id]['state'] = 'completed'
    notify(payment)
    webhooks.process(last_log().pk)
    payment.refresh_from_db()
    assert payment.state == 'confirmed'
    return Order.objects.get(pk=order.pk), payment


def refund_command(reference):
    out = StringIO()
    call_command('refund_order', reference, '--reason', 'cancellation', stdout=out)
    return out.getvalue()


@pytest.mark.django_db(transaction=True)
def test_a_refund_posts_the_amount_with_an_idempotency_key_and_returns_the_refund_id(
        doctor, entity, fake):
    order, payment = paid(doctor, fake)
    printed = refund_command(order.reference)
    assert printed.strip().startswith(f'refund done for order {order.reference}: credit note ')
    method, url, payload, headers, timeout = fake.calls_of('POST', '/refund')[0]
    assert url == (f'https://sandbox-merchant.revolut.com/api/orders/'
                   f'{payment.gateway_payment_id}/refund')
    assert payload['amount'] == 10000 and payload['currency'] == 'EUR'
    assert payload['merchant_order_data']['reference']
    assert uuid.UUID(headers['Idempotency-Key'])
    assert headers['Authorization'] == f'Bearer {KEY}' and timeout == revolut.REFUND_TIMEOUT
    assert Order.objects.get(pk=order.pk).state == 'refunded'
    assert revolut.refund(payment, Decimal('100.00')) == 'refund-1'
    from payments.models import Refund
    assert headers['Idempotency-Key'] == str(Refund.objects.get().key)
    # Without a pending refund row a fresh key is drawn: a retry is a new request.
    assert fake.calls_of('POST', '/refund')[1][3]['Idempotency-Key'] != headers['Idempotency-Key']


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('failure', [
    urllib.error.HTTPError('https://x.revolut.com/', 422, 'refused', {}, None),
    'decline', 'declined', 'failed'])
def test_a_refused_refund_is_a_gateway_error_and_leaves_the_order_paid(
        doctor, entity, fake, failure, caplog):
    order, payment = paid(doctor, fake)
    if failure == 'decline':
        fake.refund_answer = {'id': 'r', 'type': 'refund', 'decline_reason': 'insufficient'}
    elif failure in ('declined', 'failed'):
        # Revolut Pay: 200, the refund `processing`, the decline inside `payments[]`.
        fake.refund_answer = {'id': 'r', 'type': 'refund', 'state': 'processing', 'payments': [
            {'id': 'p', 'state': failure, 'decline_reason': 'insufficient_funds'}]}
    else:
        fake.fail[('POST', '/refund')] = failure
    with caplog.at_level(logging.DEBUG), pytest.raises(CommandError) as raised:
        refund_command(order.reference)
    assert KEY not in str(raised.value) and KEY not in caplog.text
    assert Order.objects.get(pk=order.pk).state == 'paid'
    with pytest.raises(gateways.GatewayError) as error:
        revolut.refund(payment, Decimal('100.00'))
    assert not isinstance(error.value, gateways.GatewayUnknown)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('failure', [
    urllib.error.URLError('timed out'), TimeoutError('slow'), ValueError('HTTP 500'),
    urllib.error.HTTPError('https://x.revolut.com/', 502, 'bad gateway', {}, None)])
def test_a_refund_timeout_is_an_unknown_outcome(doctor, entity, fake, sentry, failure):
    order, payment = paid(doctor, fake)
    fake.fail[('POST', '/refund')] = failure
    printed = refund_command(order.reference)
    assert 'refund needs_attention' in printed
    assert Order.objects.get(pk=order.pk).state == 'paid'
    with pytest.raises(gateways.GatewayUnknown):
        revolut.refund(payment, Decimal('100.00'))


@pytest.mark.django_db(transaction=True)
def test_an_answer_without_a_refund_id_is_unknown(doctor, entity, fake):
    _order, payment = paid(doctor, fake)
    fake.refund_answer = {'type': 'refund'}
    with pytest.raises(gateways.GatewayUnknown):
        revolut.refund(payment, Decimal('100.00'))


# --- The webhook command ------------------------------------------------------------------

def run(*args):
    out = StringIO()
    call_command('revolut_webhook', *args, stdout=out)
    return out.getvalue()


def test_register_prints_the_signing_secret_once_and_registers_the_site_url(
        fake, caplog, tmp_path):
    with caplog.at_level(logging.DEBUG):
        printed = run('register')
    assert 'wsk_printed_once' in printed
    assert fake.webhooks['wh1']['url'] == f'{SITE}/payments/webhook/revolut/'
    assert fake.webhooks['wh1']['events'] == ['ORDER_COMPLETED', 'ORDER_FAILED',
                                              'ORDER_CANCELLED']
    assert 'wsk_printed_once' not in caplog.text and KEY not in caplog.text
    listed = run('list')
    assert 'wh1' in listed and 'wsk_printed_once' not in listed
    assert f'{SITE}/payments/webhook/revolut/' in listed
    assert 'deleted wh1' in run('delete', 'wh1')
    assert 'no webhooks' in run('list')


def test_the_command_needs_the_key_and_reports_a_revolut_fault(fake, settings):
    fake.fail[('POST', '/api/webhooks')] = urllib.error.HTTPError(
        'https://x.revolut.com/', 401, 'no', {}, None)
    with pytest.raises(CommandError, match='refused'):
        run('register')
    settings.REVOLUT_SECRET_KEY = ''
    with pytest.raises(CommandError, match='REVOLUT_SECRET_KEY'):
        run('list')
    with pytest.raises(CommandError):
        run('delete', '../x')


# --- Settings and checks ------------------------------------------------------------------

def test_the_adapter_is_available_only_with_a_key(revolut_on):
    found = gateways.adapter('revolut')
    assert found is not None and found.supports('cancel_attempt') and found.supports('refund')
    assert not found.supports('charge_saved_card') and not found.saves_cards
    assert not gateways.can_save_cards('revolut')
    assert found.required_settings == ('REVOLUT_SECRET_KEY', 'REVOLUT_WEBHOOK_SECRET')
    assert found.session_lifetime.total_seconds() == 2100
    revolut_on.REVOLUT_SECRET_KEY = ''
    assert gateways.adapter('revolut') is None


def test_the_environment_picks_the_host(revolut_on):
    assert revolut.base_url() == 'https://sandbox-merchant.revolut.com'
    revolut_on.REVOLUT_ENVIRONMENT = 'production'
    assert revolut.base_url() == 'https://merchant.revolut.com'
    revolut_on.REVOLUT_ENVIRONMENT = 'nonsense'
    assert revolut.base_url() == 'https://sandbox-merchant.revolut.com'


def public(settings):
    settings.DEBUG = False
    settings.ROBOTS_NOINDEX = False


def test_the_sandbox_is_refused_on_a_public_site(revolut_on):
    assert checks.check_revolut_environment() == []  # staging: fine
    public(revolut_on)
    errors = checks.check_revolut_environment()
    assert [e.id for e in errors] == ['payments.E005'] and isinstance(errors[0], Error)
    assert checks.revolut_sandbox_on_public_site()
    revolut_on.REVOLUT_ENVIRONMENT = 'production'
    assert checks.check_revolut_environment() == []
    revolut_on.REVOLUT_ENVIRONMENT = 'sandbox'
    revolut_on.REVOLUT_SECRET_KEY = ''  # not enabled: nothing to check
    assert checks.check_revolut_environment() == []


def test_a_missing_webhook_secret_on_a_public_site_is_e003(revolut_on):
    public(revolut_on)
    revolut_on.REVOLUT_ENVIRONMENT = 'production'
    revolut_on.REVOLUT_WEBHOOK_SECRET = ''
    errors = [e for e in checks.check_gateway_secrets() if 'REVOLUT_WEBHOOK_SECRET' in e.msg]
    assert [e.id for e in errors] == ['payments.E003']


def test_the_revolut_settings_are_sensitive_for_sentry(settings):
    from mytp_core.sentry import _squash
    fields = {_squash(f) for f in settings.MYTP_SENTRY_SENSITIVE_FIELDS}
    for name in ('REVOLUT_SECRET_KEY', 'REVOLUT_WEBHOOK_SECRET', 'Revolut-Signature'):
        assert any(field in _squash(name) for field in fields)


def test_no_key_is_logged_or_put_in_a_url(doctor, entity, fake, caplog):
    with caplog.at_level(logging.DEBUG):
        order, payment = attempt(doctor)
        fake.fail[('GET', payment.gateway_payment_id)] = urllib.error.URLError('down')
        with pytest.raises(gateways.GatewayError):
            revolut.fetch_payment(payment)
    assert KEY not in caplog.text and SECRET not in caplog.text
    assert all(KEY not in call[1] for call in fake.calls)


# --- Review fixes -------------------------------------------------------------------------

def open_attempt(doctor):
    """An open attempt with no Revolut order yet (as `start_payment` leaves it)."""
    order = revolut_order(doctor)
    order = service.start_payment(order, 'revolut', expected_version=order.version,
                                  actor=actor(doctor))
    return order, service.open_payment(order)


def test_an_attempt_closed_while_it_starts_cancels_the_new_revolut_order(
        doctor, entity, fake):
    _order, payment = open_attempt(doctor)
    original = fake.__call__

    def closing(method, url, payload=None, **kwargs):
        answer = original(method, url, payload, **kwargs)
        if (method, url.split('revolut.com')[1]) == ('POST', '/api/orders'):
            Payment.objects.filter(pk=payment.pk).update(state='closed')  # a second Pay
        return answer

    fake.__class__.__call__  # the fake is called through the patched attribute below
    import accounts.http
    accounts.http.request_json = closing
    try:
        with pytest.raises(gateways.GatewayError):
            revolut.start(payment, None)
    finally:
        accounts.http.request_json = fake
    (new_id,) = fake.orders
    assert fake.orders[new_id]['state'] == 'cancelled'


def test_a_processing_order_sends_the_doctor_to_the_return_page(doctor, entity, fake):
    order, payment = attempt(doctor)
    for state in ('processing', 'authorised'):
        fake.orders[payment.gateway_payment_id]['state'] = state
        assert revolut.start(payment, None) == f'{SITE}/en/checkout/{order.pk}/return/'
    fake.orders[payment.gateway_payment_id]['state'] = 'completed'
    with pytest.raises(gateways.GatewayError):
        revolut.start(payment, None)


def test_start_extends_the_session_to_35_minutes(doctor, entity, fake):
    _order, payment = attempt(doctor)
    payment.refresh_from_db()
    assert payment.session_expires_at >= timezone.now() + revolut.SESSION_LIFETIME \
        - revolut.timedelta(seconds=30)


def test_the_view_returns_to_the_return_page_when_the_started_attempt_has_an_id(
        doctor, entity, fake):
    from payments import views
    order, payment = attempt(doctor)
    fake.fail[('GET', payment.gateway_payment_id)] = urllib.error.URLError('down')
    request = RequestFactory().post('/x')
    request.user = doctor
    from django.contrib.messages.storage.fallback import FallbackStorage
    request.session = {}
    request._messages = FallbackStorage(request)
    response = views._go_to_gateway(request, Order.objects.get(pk=order.pk))
    assert response.url == f'/en/checkout/{order.pk}/return/'
    assert Payment.objects.get(pk=payment.pk).state == 'open'


def test_a_redirect_is_refused_so_the_key_is_never_forwarded():
    import io
    import urllib.request
    from accounts import http
    assert not any(type(handler) is urllib.request.HTTPRedirectHandler
                   for handler in http._NO_REDIRECT.handlers)
    req = urllib.request.Request('https://merchant.revolut.com/api/orders', data=b'{}',
                                 method='POST', headers={'Authorization': 'Bearer k'})
    for code in (301, 302, 303, 307, 308):
        with pytest.raises(urllib.error.HTTPError) as raised:
            http._NO_REDIRECT.error('http', req, io.BytesIO(b''), code, 'moved',
                                    {'Location': 'https://evil.test/'})
        assert raised.value.code == code
