"""Gateway interface, webhook handling and payment attempts (epic-payments entry 2): one
test (or more) per row of the plan's matrix. The fake gateway signs its webhooks; its
records are in the cache (no network)."""
import dataclasses
import logging
from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.checks import Error
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from payments import checks, fake, gateways, service, tasks, webhooks
from payments.models import Order, Payment, WebhookLog
from payments.tests.test_checkout import browser, html
from payments.tests.test_service import actor, new

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('clear_cache', 'portal_urls')]


def attempt(user, gateway='fake', order=None):
    """A `pending_payment` order with its open attempt, as the doctor's Pay leaves it (the
    gateway's id recorded by the adapter's `start`)."""
    order = order or new(user)
    order = service.start_payment(order, gateway, expected_version=order.version,
                                  actor=actor(user))
    payment = service.open_payment(order)
    gateways.adapter(gateway).start(payment, None)
    payment.refresh_from_db()
    return order, payment


def send(payment, **kwargs):
    amount = kwargs.pop('amount', None)
    body = kwargs.pop('body', None) or fake.webhook_body(payment, amount=amount)
    return fake.send_webhook(body, **kwargs), body


def last_log():
    return WebhookLog.objects.order_by('-id').first()


def paid_hooks(events):
    return [e for e, *_ in events].count('paid')


# --- The interface ------------------------------------------------------------------------------

def test_an_adapter_declares_what_it_supports(fake_on):
    found = gateways.adapter('fake')
    assert all(found.supports(op) for op in gateways.OPERATIONS)
    bare = gateways.Adapter('x', lambda payment, request: '/')
    assert bare.supports('start') and not bare.supports('refund')
    with pytest.raises(gateways.UnsupportedOperation):
        bare.call('refund', None, 1)
    with pytest.raises(ValueError):
        bare.supports('nonsense')
    assert bare.timestamp_tolerance == timedelta(minutes=5)


def test_the_fake_adapter_implements_refunds_and_saved_cards(doctor, entity, fake_on):
    order, payment = attempt(doctor)
    found = gateways.adapter('fake')
    assert found.call('refund', payment, Decimal('10.00')).startswith('fake_refund_')
    token = found.call('save_card', doctor)
    charged = found.call('charge_saved_card', doctor, token, Decimal('5.00'), 'EUR')
    assert charged.status == gateways.PAID and charged.amount == Decimal('5.00')


# --- Webhook handling ---------------------------------------------------------------------------

def test_a_valid_webhook_is_logged_and_its_task_pays_the_order_once(
        doctor, entity, fake_on, events):
    order, payment = attempt(doctor)
    fake.set_state(payment.gateway_payment_id, 'paid')
    response, body = send(payment)
    assert response.status_code == 200
    row = last_log()
    assert (row.result, row.gateway, row.payment_id) == ('accepted', 'fake', payment.pk)
    assert row.body_size == len(body) and len(row.body_sha256) == 64
    assert row.amount == Decimal('100.00') and row.currency == 'EUR' and row.ip == '127.0.0.1'
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'  # answered at once
    assert webhooks.process(row.pk) == 'confirmed'
    assert webhooks.process(row.pk) is None  # a task run again does nothing
    fresh = Order.objects.get(pk=order.pk)
    assert fresh.state == 'paid' and fresh.actor_type == 'system'
    assert Payment.objects.get(pk=payment.pk).state == 'confirmed'
    assert WebhookLog.objects.get(pk=row.pk).processed_at is not None
    assert paid_hooks(events) == 1


def test_the_raw_body_is_never_stored(doctor, entity, fake_on):
    _order, payment = attempt(doctor)
    send(payment)
    fields = {f.name for f in WebhookLog._meta.get_fields()}
    assert not {'body', 'raw', 'signature', 'headers'} & fields


def test_a_duplicate_is_answered_200_and_changes_nothing(doctor, entity, fake_on, events):
    order, payment = attempt(doctor)
    fake.set_state(payment.gateway_payment_id, 'paid')
    body = fake.webhook_body(payment)
    first, _ = send(payment, body=body)
    second, _ = send(payment, body=body)
    assert (first.status_code, second.status_code) == (200, 200)
    assert [r.result for r in WebhookLog.objects.order_by('id')] == ['accepted', 'duplicate']
    webhooks.process(WebhookLog.objects.get(result='accepted').pk)
    assert webhooks.process(last_log().pk) is None  # the duplicate row is no work item
    assert Order.objects.get(pk=order.pk).version == 3 and paid_hooks(events) == 1


def test_the_database_refuses_the_same_event_twice(doctor, entity, fake_on):
    _order, payment = attempt(doctor)
    send(payment, body=fake.webhook_body(payment, event_id='e1'))
    row = WebhookLog.objects.get()
    with pytest.raises(IntegrityError), transaction.atomic():
        WebhookLog.objects.create(gateway='fake', result='accepted', event_id='e1',
                                  payment=row.payment)
    # a refusal with the same event id is not a taken-in event
    WebhookLog.objects.create(gateway='fake', result='bad_signature', event_id='e1')


def test_a_wrong_signature_is_refused_logged_and_changes_nothing(
        doctor, entity, fake_on, caplog):
    order, payment = attempt(doctor)
    fake.set_state(payment.gateway_payment_id, 'paid')
    with caplog.at_level(logging.WARNING, logger='payments.webhooks'):
        response, body = send(payment, key='the-wrong-secret')
    assert response.status_code == 401
    assert last_log().result == 'bad_signature'
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    assert 'bad_signature' in caplog.text
    assert 'the-wrong-secret' not in caplog.text and 'v1=' not in caplog.text
    assert body.decode() not in caplog.text


def test_a_stale_webhook_is_refused_and_logged(doctor, entity, fake_on):
    order, payment = attempt(doctor)
    stale = int(timezone.now().timestamp()) - 6 * 60
    response, _ = send(payment, timestamp=stale)
    row = last_log()
    assert (response.status_code, row.result) == (400, 'stale')
    assert row.gateway_payment_id == payment.gateway_payment_id
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    fresh = int(timezone.now().timestamp()) - 4 * 60  # inside the tolerance
    assert send(payment, timestamp=fresh)[0].status_code == 200


def test_a_non_ascii_signature_is_a_bad_signature(doctor, entity, fake_on):
    from django.test import RequestFactory
    _order, payment = attempt(doctor)
    request = RequestFactory().post(
        '/payments/webhook/fake/', data=fake.webhook_body(payment),
        content_type='application/json', **{fake.META_HEADER: 't=1,v1=\u00e9\u4e2d'})
    response = webhooks.webhook(request, 'fake')
    assert response.status_code == 401 and last_log().result == 'bad_signature'


def test_a_paid_fake_payment_cannot_be_cancelled(doctor, entity, fake_on):
    _order, payment = attempt(doctor)
    fake.set_state(payment.gateway_payment_id, 'paid')
    with pytest.raises(gateways.GatewayError):
        fake.cancel_attempt(payment)
    assert fake._get(payment.gateway_payment_id)['status'] == 'paid'


def test_the_timestamp_tolerance_is_per_adapter(fake_on):
    adapter = dataclasses.replace(gateways.adapter('fake'),
                                  timestamp_tolerance=timedelta(seconds=10))
    now = timezone.now()
    gateways.check_timestamp(adapter, now - timedelta(seconds=5), now)
    with pytest.raises(gateways.Stale):
        gateways.check_timestamp(adapter, now - timedelta(seconds=30), now)
    with pytest.raises(gateways.Stale):  # a timestamp in the future is out too
        gateways.check_timestamp(adapter, now + timedelta(seconds=30), now)


def test_an_unknown_payment_is_refused_and_logged(doctor, entity, fake_on, caplog):
    attempt(doctor)
    body = (b'{"event_id": "e9", "payment_id": "fake_nope", "amount": "1.00", '
            b'"currency": "EUR"}')
    response = fake.send_webhook(body)
    assert response.status_code == 404
    assert last_log().result == 'unknown' and last_log().payment is None
    assert fake.send_webhook(b'not json').status_code == 404
    assert [r.result for r in WebhookLog.objects.all()] == ['unknown', 'unknown']


def test_an_amount_or_currency_mismatch_is_refused_and_reported(
        doctor, entity, fake_on, events, monkeypatch):
    seen = []
    monkeypatch.setattr('sentry_sdk.capture_message', lambda message, **kw: seen.append(message))
    order, payment = attempt(doctor)
    fake.set_state(payment.gateway_payment_id, 'paid', amount='1.00')
    send(payment)
    assert webhooks.process(last_log().pk) == 'amount_mismatch'
    assert last_log().result == 'amount_mismatch'
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    assert Payment.objects.get(pk=payment.pk).state == 'needs_attention'
    assert seen == [f'payment amount or currency differs: order {order.pk}']
    assert paid_hooks(events) == 0
    # the same for the currency
    order2, payment2 = attempt(doctor)
    fake.set_state(payment2.gateway_payment_id, 'paid', currency='USD')
    send(payment2)
    assert webhooks.process(last_log().pk) == 'amount_mismatch'
    assert Order.objects.get(pk=order2.pk).state == 'pending_payment'


def test_a_replayed_mismatch_is_still_a_duplicate(doctor, entity, fake_on):
    _order, payment = attempt(doctor)
    fake.set_state(payment.gateway_payment_id, 'paid', amount='1.00')
    body = fake.webhook_body(payment, amount='1.00')
    send(payment, body=body)
    webhooks.process(last_log().pk)
    send(payment, body=body)
    assert [r.result for r in WebhookLog.objects.order_by('id')] == [
        'amount_mismatch', 'duplicate']


def test_a_failed_reread_is_retried_with_backoff_then_reported(
        doctor, entity, fake_on, monkeypatch):
    seen = []
    monkeypatch.setattr('sentry_sdk.capture_message', lambda message, **kw: seen.append(message))
    order, payment = attempt(doctor)
    fake.set_state(payment.gateway_payment_id, 'paid')
    response, _ = send(payment)
    assert response.status_code == 200  # answered before the re-read
    row = last_log()

    def down(payment):
        raise gateways.GatewayError('timeout')

    broken = dataclasses.replace(gateways.adapter('fake'), fetch_payment=down)
    monkeypatch.setitem(gateways._registry, 'fake', broken)
    before = timezone.now()
    assert webhooks.process(row.pk) == 'retry'
    row.refresh_from_db()
    assert row.attempts == 1 and row.processed_at is None
    assert row.next_try_at >= before + webhooks.RETRY_BACKOFF[0] - timedelta(seconds=1)
    for _ in range(webhooks.MAX_ATTEMPTS - 2):
        assert webhooks.process(row.pk) == 'retry'
    assert seen == []
    assert webhooks.process(row.pk) == 'failed'
    row.refresh_from_db()
    assert row.attempts == webhooks.MAX_ATTEMPTS and row.next_try_at is None
    assert seen == [f'payment could not be confirmed: order {order.pk}']
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    # the gateway is back: a later run still confirms it
    monkeypatch.setitem(gateways._registry, 'fake', dataclasses.replace(broken,
                                                                       fetch_payment=fake.fetch_payment))
    assert webhooks.process(row.pk) == 'confirmed'


def test_the_task_queues_itself_again_after_a_failed_reread(
        doctor, entity, fake_on, monkeypatch):
    from django_tasks_db.models import DBTaskResult
    _order, payment = attempt(doctor)
    send(payment)
    monkeypatch.setitem(gateways._registry, 'fake', dataclasses.replace(
        gateways.adapter('fake'), fetch_payment=lambda p: (_ for _ in ()).throw(OSError())))
    assert tasks.confirm_webhook.call(last_log().pk) == 'retry'
    queued = DBTaskResult.objects.filter(task_path='payments.tasks.confirm_webhook')
    assert queued.count() == 1 and queued.get().run_after is not None


def test_the_webhook_handler_queues_the_task_when_it_commits(
        doctor, entity, fake_on, django_capture_on_commit_callbacks):
    _order, payment = attempt(doctor)
    with django_capture_on_commit_callbacks() as callbacks:
        send(payment)
    assert len(callbacks) == 1


def test_a_failed_attempt_at_the_gateway_is_recorded(doctor, entity, fake_on):
    order, payment = attempt(doctor)
    fake.fail(payment)
    send(payment)
    assert webhooks.process(last_log().pk) == 'failed'
    assert Payment.objects.get(pk=payment.pk).state == 'failed'
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'


def test_a_gateway_still_pending_leaves_everything(doctor, entity, fake_on):
    order, payment = attempt(doctor)
    send(payment)  # a webhook, but the gateway still says pending
    row = last_log()
    assert webhooks.process(row.pk) == 'retry'  # retried like a failed re-read
    row.refresh_from_db()
    assert row.attempts == 1 and row.processed_at is None and row.next_try_at is not None
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    assert Payment.objects.get(pk=payment.pk).state == 'open'


def test_the_body_limit_and_the_failed_signature_limit(doctor, entity, fake_on):
    _order, payment = attempt(doctor)
    big = b'x' * (webhooks.MAX_BODY_BYTES + 1)
    assert fake.send_webhook(big).status_code == 413
    assert last_log().result == 'too_large' and last_log().body_size == 0
    for _ in range(webhooks.FAILED_SIGNATURE_LIMIT):
        assert send(payment, key='bad')[0].status_code == 401
    assert send(payment, key='bad')[0].status_code == 429
    assert send(payment)[0].status_code == 429  # even a good one, from that IP
    assert last_log().result == 'rate_limited'
    assert send(payment, ip='10.9.9.9')[0].status_code == 200  # another IP is not limited


def test_the_endpoint_is_public_post_only_and_not_under_api(doctor, entity, fake_on):
    url = reverse('payments_webhook', args=['fake'])
    assert url == '/payments/webhook/fake/' and not url.startswith('/api/')
    client = Client(enforce_csrf_checks=True)
    assert client.get(url).status_code == 405
    assert client.post(url, b'{}', content_type='application/json').status_code == 401
    assert client.post('/payments/webhook/nonsense/', b'{}',
                       content_type='application/json').status_code == 404
    fake_on.PAYMENTS_FAKE_GATEWAY = False
    assert client.post(url, b'{}', content_type='application/json').status_code == 404


# --- Payment attempts ---------------------------------------------------------------------------

def test_an_open_attempt_freezes_the_order(doctor, entity, fake_on):
    order, payment = attempt(doctor)
    assert payment.state == 'open' and payment.amount == order.total
    assert payment.session_expires_at > timezone.now()
    with pytest.raises(service.AttemptOpen):
        service.cancel(order, expected_version=order.version, actor=actor(doctor))
    with pytest.raises(service.AttemptOpen):
        service.expire(order, expected_version=order.version)
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'


def test_the_expiry_job_skips_an_order_until_the_session_timed_out(
        doctor, entity, fake_on, events):
    order, payment = attempt(doctor)
    now = timezone.now()
    Order.objects.filter(pk=order.pk).update(created_at=now - timedelta(hours=30),
                                             pending_at=now - timedelta(hours=25))
    assert service.expire_stale(now) == 0
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    Payment.objects.filter(pk=payment.pk).update(session_expires_at=now - timedelta(minutes=1))
    assert service.expire_stale(now) == 1
    assert Order.objects.get(pk=order.pk).state == 'expired'
    assert Payment.objects.get(pk=payment.pk).state == 'closed'


def test_a_timed_out_session_no_longer_blocks_a_cancel(doctor, entity, fake_on):
    order, payment = attempt(doctor)
    Payment.objects.filter(pk=payment.pk).update(
        session_expires_at=timezone.now() - timedelta(seconds=1))
    assert service.cancel(order, expected_version=order.version,
                          actor=actor(doctor)).state == 'cancelled'


def test_a_retry_closes_the_previous_attempt_at_the_gateway(doctor, entity, fake_on):
    order, first = attempt(doctor)
    order = Order.objects.get(pk=order.pk)
    second = service.start_payment(order, 'fake', expected_version=order.version,
                                   actor=actor(doctor))
    states = list(Payment.objects.order_by('created_at').values_list('state', flat=True))
    assert states == ['closed', 'open']
    assert fake.fetch_payment(first).status == gateways.PENDING  # cancelled: not paid
    assert fake._get(first.gateway_payment_id)['status'] == 'cancelled'
    assert second.state == 'pending_payment'


def test_a_gateway_that_cannot_cancel_waits_for_its_session(doctor, entity, fake_on):
    gateways.register('redsys', lambda payment, request: '/x/')
    try:
        order = new(doctor)
        order = service.start_payment(order, 'redsys', expected_version=1, actor=actor(doctor))
        open_one = service.open_payment(order)
        with pytest.raises(service.AttemptOpen):
            service.start_payment(order, 'fake', expected_version=order.version,
                                  actor=actor(doctor))
        assert Payment.objects.count() == 1 and service.open_payment(order).pk == open_one.pk
        Payment.objects.filter(pk=open_one.pk).update(
            session_expires_at=timezone.now() - timedelta(seconds=1))
        order = Order.objects.get(pk=order.pk)
        service.start_payment(order, 'fake', expected_version=order.version,
                              actor=actor(doctor))
        assert [p.state for p in Payment.objects.order_by('created_at')] == ['closed', 'open']
    finally:
        gateways.unregister('redsys')


def test_a_gateway_that_fails_to_cancel_keeps_the_attempt_open(
        doctor, entity, fake_on, monkeypatch):
    order, payment = attempt(doctor)

    def down(payment):
        raise OSError('x')

    monkeypatch.setitem(gateways._registry, 'fake',
                        dataclasses.replace(gateways.adapter('fake'), cancel_attempt=down))
    order = Order.objects.get(pk=order.pk)
    with pytest.raises(service.AttemptOpen):
        service.start_payment(order, 'fake', expected_version=order.version,
                              actor=actor(doctor))
    assert Payment.objects.get().state == 'open'


def test_only_one_attempt_can_be_open_per_order_in_the_database(doctor, entity, fake_on):
    order, _payment = attempt(doctor)
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.create(order=order, gateway='fake', amount=order.total,
                               currency=order.currency)


def test_a_late_success_of_an_earlier_attempt_needs_attention(
        doctor, entity, fake_on, events, monkeypatch):
    seen = []
    monkeypatch.setattr('sentry_sdk.capture_message', lambda message, **kw: seen.append(message))
    order, first = attempt(doctor)
    order = Order.objects.get(pk=order.pk)
    service.start_payment(order, 'fake', expected_version=order.version, actor=actor(doctor))
    fake.set_state(first.gateway_payment_id, 'paid')  # A paid after all
    send(first)
    assert webhooks.process(last_log().pk) == 'not_payable'
    assert last_log().result == 'not_payable'
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    assert Payment.objects.get(pk=first.pk).state == 'needs_attention'
    assert seen == [f'payment confirmed for an order that is not payable: order {order.pk}']
    assert paid_hooks(events) == 0


@pytest.mark.parametrize('how', ['paid', 'cancelled', 'expired'])
def test_a_payment_for_an_order_that_is_not_payable_needs_attention(
        doctor, entity, fake_on, events, how, monkeypatch):
    monkeypatch.setattr('sentry_sdk.capture_message', lambda message, **kw: None)
    order, payment = attempt(doctor)
    if how == 'paid':
        service.mark_paid(order, expected_version=order.version, actor=service.SYSTEM_ACTOR)
    else:
        Payment.objects.filter(pk=payment.pk).update(
            session_expires_at=timezone.now() - timedelta(seconds=1))
        order = Order.objects.get(pk=order.pk)
        if how == 'cancelled':
            service.cancel(order, expected_version=order.version, actor=actor(doctor))
        else:
            service.expire(order, expected_version=order.version)
    before = Order.objects.get(pk=order.pk)
    fake.set_state(payment.gateway_payment_id, 'paid')
    send(payment)
    assert webhooks.process(last_log().pk) == 'not_payable'
    after = Order.objects.get(pk=order.pk)
    assert (after.state, after.version) == (before.state, before.version)
    assert Payment.objects.get(pk=payment.pk).state == 'needs_attention'
    assert last_log().result == 'not_payable'
    assert paid_hooks(events) == (1 if how == 'paid' else 0)
    page = html(browser(doctor).get(reverse('checkout_return', args=[order.pk])))
    assert 'data-needs-attention' in page  # the doctor sees an error message


def test_a_second_success_of_the_same_payment_is_a_no_op(doctor, entity, fake_on, events):
    order, payment = attempt(doctor)
    fake.set_state(payment.gateway_payment_id, 'paid')
    send(payment)
    webhooks.process(last_log().pk)
    send(payment)  # another event of the same payment
    webhooks.process(last_log().pk)
    assert Payment.objects.get(pk=payment.pk).state == 'confirmed'
    assert Order.objects.get(pk=order.pk).version == 3 and paid_hooks(events) == 1


def test_the_return_page_rereads_once_and_pays(doctor, entity, fake_on, events):
    order, payment = attempt(doctor)
    client = browser(doctor)
    url = reverse('checkout_return', args=[order.pk])
    page = html(client.get(url))  # the gateway says pending
    assert 'data-waiting' in page and 'data-refresh' in page
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    fake.set_state(payment.gateway_payment_id, 'paid')
    assert 'data-order-state="paid"' in html(client.get(url))
    assert Order.objects.get(pk=order.pk).state == 'paid' and paid_hooks(events) == 1
    assert webhooks.process(WebhookLog.objects.create(
        gateway='fake', result='accepted', event_id='e', payment=payment).pk) == 'confirmed'
    assert paid_hooks(events) == 1  # the webhook's task after the return page: once


def test_the_return_page_waits_when_the_gateway_cannot_be_read(
        doctor, entity, fake_on, monkeypatch):
    order, _payment = attempt(doctor)
    monkeypatch.setitem(gateways._registry, 'fake', dataclasses.replace(
        gateways.adapter('fake'), fetch_payment=lambda p: (_ for _ in ()).throw(OSError())))
    page = html(browser(doctor).get(reverse('checkout_return', args=[order.pk])))
    assert 'data-waiting' in page


def test_cancel_is_refused_on_the_failure_page_while_an_attempt_is_open(
        doctor, entity, fake_on):
    order, _payment = attempt(doctor)
    client = browser(doctor)
    url = reverse('checkout_failure', args=[order.pk])
    response = client.post(url, {'action': 'cancel', 'version': order.version}, follow=True)
    assert 'A payment for this order is in progress' in html(response)
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'


def test_a_second_tab_starting_to_pay_leaves_one_open_attempt(doctor, entity, fake_on):
    order, _payment = attempt(doctor)
    client = browser(doctor)
    url = reverse('checkout_failure', args=[order.pk])
    order = Order.objects.get(pk=order.pk)
    response = client.post(url, {'action': 'retry', 'gateway': 'fake',
                                 'version': order.version})
    assert response['Location'].endswith('/fake/')
    assert Payment.objects.filter(state='open').count() == 1
    assert Payment.objects.count() == 2


def test_the_fake_page_sends_a_signed_webhook_through_the_handler(doctor, entity, fake_on):
    order, payment = attempt(doctor)
    client = browser(doctor)
    client.post(reverse('checkout_fake', args=[order.pk]), {'action': 'pay'})
    row = WebhookLog.objects.get()
    assert row.result == 'accepted' and row.payment_id == payment.pk


# --- The log's retention ------------------------------------------------------------------------

def test_the_purge_deletes_rows_older_than_180_days(doctor, entity, fake_on):
    old = WebhookLog.objects.create(gateway='fake', result='duplicate')
    kept = WebhookLog.objects.create(gateway='fake', result='duplicate')
    WebhookLog.objects.filter(pk=old.pk).update(
        received_at=timezone.now() - timedelta(days=181))
    out = StringIO()
    call_command('purge_webhook_logs', stdout=out)
    assert '1 row(s) deleted' in out.getvalue()
    assert list(WebhookLog.objects.values_list('pk', flat=True)) == [kept.pk]
    assert 'purgewebhooklogs | purge_webhook_logs |' in open('deploy/jobs.conf').read()


# --- The staging command ------------------------------------------------------------------------

def run(reference, *args):
    out = StringIO()
    call_command('fake_webhook', reference, *args, stdout=out)
    return out.getvalue()


def test_fake_webhook_pays_the_order_once_and_each_refusal_is_a_log_row(
        doctor, entity, fake_on, events):
    order, payment = attempt(doctor)
    ref = order.reference
    assert 'HTTP 401, bad_signature' in run(ref, '--bad-signature')
    assert 'HTTP 400, stale' in run(ref, '--stale')
    assert fake._get(payment.gateway_payment_id)['status'] == 'pending'  # still not paid
    assert [r.result for r in WebhookLog.objects.order_by('id')] == ['bad_signature', 'stale']
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    out = run(ref, '--replay')
    assert 'webhook 1: HTTP 200, accepted, processed: confirmed' in out
    assert 'webhook 2: HTTP 200, duplicate' in out and 'order is paid' in out
    assert paid_hooks(events) == 1
    assert Payment.objects.get(pk=payment.pk).state == 'confirmed'


def test_fake_webhook_amount_is_an_amount_mismatch(doctor, entity, fake_on, monkeypatch):
    monkeypatch.setattr('sentry_sdk.capture_message', lambda message, **kw: None)
    order, _payment = attempt(doctor)
    out = run(order.reference, '--amount', '1.00')
    assert 'accepted, processed: amount_mismatch' in out and 'order is pending_payment' in out
    assert last_log().result == 'amount_mismatch'


def test_fake_webhook_needs_the_flag_and_an_order(doctor, entity, fake_on):
    with pytest.raises(CommandError):
        run('XX-000000')
    order, _payment = attempt(doctor)
    for bad in ('abc', 'NaN', '-1'):
        with pytest.raises(CommandError):
            run(order.reference, '--amount', bad)
    order = new(doctor)
    with pytest.raises(CommandError):  # no attempt yet
        run(order.reference)
    fake_on.PAYMENTS_FAKE_GATEWAY = False
    with pytest.raises(CommandError):
        run(order.reference)


# --- The system check ---------------------------------------------------------------------------

def test_an_enabled_gateway_without_its_secret_fails_a_public_site(settings):
    settings.DEBUG, settings.ROBOTS_NOINDEX = False, False
    settings.REDSYS_SECRET = ''
    gateways.register('redsys', lambda payment, request: '/x/',
                      required_settings=('REDSYS_SECRET',))
    try:
        errors = checks.check_gateway_secrets()
        assert [e.id for e in errors] == ['payments.E003'] and isinstance(errors[0], Error)
        assert 'REDSYS_SECRET' in errors[0].msg
        settings.REDSYS_SECRET = 'set'
        assert checks.check_gateway_secrets() == []
        settings.REDSYS_SECRET = ''
        settings.ROBOTS_NOINDEX = True  # not a public site
        assert checks.check_gateway_secrets() == []
    finally:
        gateways.unregister('redsys')
