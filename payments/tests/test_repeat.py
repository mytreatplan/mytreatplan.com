"""Saved cards and repeat charges (epic-payments entry 11): one test (or more) per row of
the plan's matrix, on the fake gateway and on Redsys (never called: its notifications and
REST answers are built and signed here with Redsys's public test key)."""
import dataclasses
import json
import socket
import urllib.error
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction

from catalogue import resolver
from payments import fake, gateways, redsys, repeat, service, webhooks
from payments.models import Order, Payment, SavedCard
from payments.tests.test_checkout import browser, html
from payments.tests.test_redsys import (KEY, FakeRedsys, body_of, form_of, notification,
                                        our_request, post, redsys_on, sentry)  # noqa: F401
from payments.tests.test_service import ADVANCED, actor
from payments.tests.test_webhooks import attempt, last_log, paid_hooks

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('clear_cache', 'portal_urls')]

CARD = {'Ds_Merchant_Identifier': 'b3f2c1a0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4',
        'Ds_Merchant_Cof_Txnid': '2311221234567890', 'Ds_ExpiryDate': '4912',
        'Ds_Card_Number': '454881******0004', 'Ds_Card_Brand': '1'}


def subscription_order(user, save_card=True):
    return service.create_subscription_order(
        user, resolver.subscription_by_id(ADVANCED), 'monthly', actor=actor(user),
        save_card=save_card)


def notify_paid(payment, **fields):
    data = notification(payment)
    data.update(fields)
    response = post(body_of(data))
    assert response.status_code == 200
    webhooks.process(last_log().pk)


def saved_on_redsys(doctor):
    """A save-card order paid on Redsys, its notification carrying the card."""
    order, payment = attempt(doctor, 'redsys', order=subscription_order(doctor))
    notify_paid(payment, **CARD)
    return Order.objects.get(pk=order.pk)


def saved_on_fake(doctor):
    order, payment = attempt(doctor, 'fake', order=subscription_order(doctor))
    fake.pay(payment)
    webhooks.process(last_log().pk)
    return Order.objects.get(pk=order.pk)


def fake_charge(monkeypatch, charge):
    """The registered fake adapter with another `charge_saved_card`."""
    found = gateways._registry['fake']
    monkeypatch.setitem(gateways._registry, 'fake',
                        dataclasses.replace(found, charge_saved_card=charge))


class ChargeAnswer(FakeRedsys):
    """Redsys REST answering a charge (type 0): `response` 0000 by default."""

    def __init__(self, response='0000', **kwargs):
        kwargs.setdefault('Ds_TransactionType', '0')
        super().__init__(response=response, **kwargs)

    def sent(self, index=-1):
        _url, payload, _timeout = self.calls[index]
        return our_request(payload['Ds_MerchantParameters'], payload['Ds_Signature'])


# --- Saving at checkout -----------------------------------------------------------------------

def test_the_redsys_form_of_a_save_card_order_asks_redsys_to_save_it(
        doctor, entity, redsys_on, fake_on):
    order = subscription_order(doctor)
    assert gateways.offered(order) == ['redsys', 'fake']
    client = browser(doctor)
    page = html(client.get(f'/en/checkout/{order.pk}/'))
    assert 'data-save-card-consent' in page
    order, payment = attempt(doctor, 'redsys', order=order)
    _action, fields = form_of(html(client.get(f'/en/checkout/{order.pk}/redsys/')))
    data = our_request(fields['Ds_MerchantParameters'], fields['Ds_Signature'])
    assert (data['ds_merchant_identifier'], data['ds_merchant_cof_ini'],
            data['ds_merchant_cof_type']) == ('REQUIRED', 'S', 'R')


def test_an_order_that_does_not_save_the_card_sends_no_cof_fields_and_no_consent(
        doctor, entity, redsys_on):
    order = subscription_order(doctor, save_card=False)
    client = browser(doctor)
    assert 'data-save-card-consent' not in html(client.get(f'/en/checkout/{order.pk}/'))
    order, payment = attempt(doctor, 'redsys', order=order)
    fields = redsys.form(payment)
    data = redsys.decode_parameters(fields['Ds_MerchantParameters'])
    assert not any(name.startswith('ds_merchant_cof') or name == 'ds_merchant_identifier'
                   for name in data)


def test_a_gateway_that_cannot_save_cards_is_not_offered_for_a_save_card_order(
        doctor, entity, redsys_on, fake_on, monkeypatch):
    monkeypatch.setattr(gateways, 'can_save_cards', lambda name: name == 'fake')
    order = subscription_order(doctor)
    assert gateways.offered(order) == ['fake']
    with pytest.raises(service.GatewayRefused):
        service.start_payment(order, 'redsys', expected_version=order.version,
                              actor=actor(doctor))


def test_no_gateway_that_saves_cards_means_no_save_card_order(doctor, entity, monkeypatch):
    monkeypatch.setattr(gateways, 'can_save_cards', lambda name: False)
    with pytest.raises(service.NoPaymentMethod):
        subscription_order(doctor)


# --- The paid notification -----------------------------------------------------------------

def test_a_paid_notification_with_a_token_stores_the_card_encrypted(
        doctor, entity, redsys_on, events):
    order = saved_on_redsys(doctor)
    assert order.state == 'paid' and paid_hooks(events) == 1
    card = SavedCard.objects.get(user=doctor, gateway='redsys')
    assert (card.token, card.cof_txnid, card.masked_number) == (
        CARD['Ds_Merchant_Identifier'], CARD['Ds_Merchant_Cof_Txnid'], '454881******0004')
    assert (card.brand, card.last4, card.expiry) == ('VISA', '0004', '4912')
    with connection.cursor() as cursor:
        cursor.execute('SELECT token, cof_txnid, masked_number FROM payments_savedcard')
        raw = cursor.fetchone()
    assert CARD['Ds_Merchant_Identifier'] not in raw[0]
    assert CARD['Ds_Merchant_Cof_Txnid'] not in raw[1] and '0004' not in raw[2]
    assert CARD['Ds_Merchant_Identifier'] not in repr(card) + str(card)


def test_a_newer_save_replaces_the_older_card(doctor, entity, redsys_on):
    saved_on_redsys(doctor)
    first = SavedCard.objects.get(user=doctor)
    _order, payment = attempt(doctor, 'redsys', order=subscription_order(doctor))
    notify_paid(payment, **{**CARD, 'Ds_Merchant_Identifier': 'newtoken',
                            'Ds_Card_Number': '545454******5454', 'Ds_Card_Brand': '2'})
    card = SavedCard.objects.get(user=doctor, gateway='redsys')
    assert SavedCard.objects.count() == 1 and card.pk == first.pk
    assert (card.token, card.brand, card.last4) == ('newtoken', 'MASTERCARD', '5454')


def test_a_token_without_the_ask_stores_nothing(doctor, entity, redsys_on):
    order, payment = attempt(doctor, 'redsys', order=subscription_order(doctor, False))
    notify_paid(payment, **CARD)
    assert Order.objects.get(pk=order.pk).state == 'paid'
    assert not SavedCard.objects.exists()


def test_paid_without_a_token_is_a_warning_by_order_uuid(doctor, entity, redsys_on, sentry):
    order, payment = attempt(doctor, 'redsys', order=subscription_order(doctor))
    notify_paid(payment)
    assert Order.objects.get(pk=order.pk).state == 'paid'
    assert not SavedCard.objects.exists()
    assert sentry == [f'saved card not returned by the gateway: order {order.pk}']


def test_a_refused_notification_stores_no_card(doctor, entity, redsys_on, sentry):
    _order, payment = attempt(doctor, 'redsys', order=subscription_order(doctor))
    data = notification(payment, response='0190')
    data.update(CARD)
    assert post(body_of(data)).status_code == 200
    assert not SavedCard.objects.exists() and sentry == []


def test_a_card_that_cannot_be_stored_never_refuses_the_notification(
        doctor, entity, redsys_on, monkeypatch, caplog):
    def broken(payment, card):
        raise RuntimeError(CARD['Ds_Merchant_Identifier'])
    monkeypatch.setattr(repeat, 'store_card', broken)
    order, payment = attempt(doctor, 'redsys', order=subscription_order(doctor))
    notify_paid(payment, **CARD)
    assert Order.objects.get(pk=order.pk).state == 'paid'
    assert f'not stored (RuntimeError): order {order.pk}' in caplog.text
    assert CARD['Ds_Merchant_Identifier'] not in caplog.text


def test_the_fake_gateway_saves_a_card_when_the_order_asks(doctor, entity, fake_on):
    saved_on_fake(doctor)
    card = SavedCard.objects.get(user=doctor, gateway='fake')
    assert card.token.startswith('fake_card_') and card.last4 == '0004'
    _order, payment = attempt(doctor, 'fake', order=subscription_order(doctor, False))
    fake.pay(payment)
    assert SavedCard.objects.count() == 1


# --- Repeat charges: fake ----------------------------------------------------------------------

def test_a_repeat_charge_pays_the_order_once_and_a_rerun_returns_the_first_outcome(
        doctor, entity, fake_on, events, monkeypatch):
    saved_on_fake(doctor)
    renewal = subscription_order(doctor, save_card=False)
    result = repeat.charge_saved_card(renewal, '2027-01')
    assert result.outcome == 'paid' and result.payment.state == 'confirmed'
    assert result.payment.period == '2027-01' and result.payment.gateway == 'fake'
    renewal.refresh_from_db()
    assert renewal.state == 'paid' and renewal.gateway == 'fake'
    assert renewal.changes.filter(event='payment_started').get().detail == {
        'gateway': 'fake', 'repeat': True}
    calls = []
    fake_charge(monkeypatch, lambda *a: calls.append(a))
    again = repeat.charge_saved_card(renewal, '2027-01')
    assert again.outcome == 'paid' and again.payment.pk == result.payment.pk
    assert calls == [] and paid_hooks(events) == 2  # the first order and the renewal


def test_a_declined_charge_fails_with_its_code_and_a_new_attempt_may_follow(
        doctor, entity, fake_on, events):
    saved_on_fake(doctor)
    renewal = subscription_order(doctor, save_card=False)
    fake.decline_next_charge('0116')
    result = repeat.charge_saved_card(renewal, 'P1')
    assert (result.outcome, result.code) == ('declined', '0116')
    assert (result.payment.state, result.payment.decline_code) == ('failed', '0116')
    renewal.refresh_from_db()
    assert renewal.state == 'pending_payment' and paid_hooks(events) == 1
    second = repeat.charge_saved_card(renewal, 'P1')
    assert second.outcome == 'paid' and second.payment.pk != result.payment.pk
    assert second.payment.gateway_payment_id != result.payment.gateway_payment_id


def test_an_expired_card_is_declined_without_a_charge(doctor, entity, fake_on, monkeypatch):
    saved_on_fake(doctor)
    SavedCard.objects.update(expiry='2510')
    monkeypatch.setattr('django.utils.timezone.localdate', lambda: date(2026, 11, 26))
    renewal = subscription_order(doctor, save_card=False)
    result = repeat.charge_saved_card(renewal, 'P1')
    assert (result.outcome, result.code, result.payment) == ('declined', 'card_expired', None)
    assert not renewal.payments.exists()
    SavedCard.objects.update(expiry='2611')  # this month: still valid
    assert repeat.charge_saved_card(renewal, 'P1').outcome == 'paid'


def test_no_saved_card_or_no_gateway_that_charges_one_creates_nothing(
        doctor, entity, fake_on, monkeypatch):
    renewal = subscription_order(doctor, save_card=False)
    with pytest.raises(repeat.NoSavedCard):
        repeat.charge_saved_card(renewal, 'P1')
    saved_on_fake(doctor)
    monkeypatch.setattr(gateways, 'can_save_cards', lambda name: False)
    with pytest.raises(gateways.UnsupportedOperation):
        repeat.charge_saved_card(renewal, 'P1')
    renewal.refresh_from_db()
    assert renewal.state == 'checkout' and not renewal.payments.exists()


@pytest.mark.parametrize('end', ['paid', 'cancelled', 'expired'])
def test_an_order_that_is_not_payable_is_not_charged(doctor, entity, fake_on, end,
                                                     monkeypatch):
    saved_on_fake(doctor)
    renewal = subscription_order(doctor, save_card=False)
    Order.objects.filter(pk=renewal.pk).update(state=end)
    calls = []
    fake_charge(monkeypatch, lambda *a: calls.append(a))
    result = repeat.charge_saved_card(renewal, 'P1')
    assert (result.outcome, result.payment) == ('not_payable', None) and calls == []


def test_an_uncertain_charge_needs_attention_and_a_rerun_never_charges(
        doctor, entity, fake_on, sentry, monkeypatch):
    saved_on_fake(doctor)
    renewal = subscription_order(doctor, save_card=False)

    def timeout(card, payment):
        service.set_gateway_payment_id(payment, f'fake_{payment.pk.hex}')
        raise socket.timeout('timed out')
    fake_charge(monkeypatch, timeout)
    result = repeat.charge_saved_card(renewal, 'P1')
    assert result.outcome == 'unknown' and result.payment.state == 'needs_attention'
    assert sentry == [f'payment repeat charge outcome unknown: order {renewal.pk}']
    calls = []
    fake_charge(monkeypatch, lambda *a: calls.append(a))
    assert repeat.charge_saved_card(renewal, 'P1').outcome == 'unknown' and calls == []
    assert Order.objects.get(pk=renewal.pk).state == 'pending_payment'


def test_a_hook_that_fails_after_the_charge_needs_attention(doctor, entity, fake_on, sentry):
    from payments import hooks
    saved_on_fake(doctor)
    renewal = subscription_order(doctor, save_card=False)

    def broken(order):
        raise RuntimeError('boom')
    hooks.register('paid', 'test_broken', broken)
    try:
        result = repeat.charge_saved_card(renewal, 'P1')
    finally:
        hooks.unregister('paid', 'test_broken')
    assert result.outcome == 'unknown' and result.payment.state == 'needs_attention'
    assert Order.objects.get(pk=renewal.pk).state == 'pending_payment'


def test_an_open_attempt_refuses_the_charge(doctor, entity, fake_on):
    saved_on_fake(doctor)
    renewal, _payment = attempt(doctor, 'fake', order=subscription_order(doctor, False))
    with pytest.raises(service.AttemptOpen):
        repeat.charge_saved_card(renewal, 'P1')


def test_the_database_refuses_a_second_live_charge_of_a_period(doctor, entity, fake_on):
    saved_on_fake(doctor)
    renewal = subscription_order(doctor, save_card=False)
    first = repeat.charge_saved_card(renewal, 'P1').payment
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.create(order=renewal, gateway='fake', amount=first.amount,
                               currency='EUR', state='needs_attention', period='P1')
    Payment.objects.create(order=renewal, gateway='fake', amount=first.amount,
                           currency='EUR', state='failed', period='P1')


def test_a_period_is_required(doctor, entity, fake_on):
    with pytest.raises(ValueError):
        repeat.charge_saved_card(subscription_order(doctor, False), '')


def test_account_erasure_deletes_the_saved_cards(doctor, entity, fake_on, settings):
    from accounts.erasure import erase_user
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 30
    saved_on_fake(doctor)
    staff = get_user_model().objects.create_superuser('staff@example.net', 'x-Long-pass-91')
    assert erase_user(doctor, by=staff)
    assert not SavedCard.objects.exists()


# --- Repeat charges: Redsys --------------------------------------------------------------------

def test_a_redsys_repeat_charge_is_a_signed_mit_rest_request_and_pays_once(
        doctor, entity, redsys_on, events, monkeypatch):
    first = saved_on_redsys(doctor)
    answer = ChargeAnswer()
    monkeypatch.setattr('accounts.http.post_json', answer)
    renewal = subscription_order(doctor, save_card=False)
    result = repeat.charge_saved_card(renewal, '2027-01')
    assert result.outcome == 'paid' and Order.objects.get(pk=renewal.pk).state == 'paid'
    sent = answer.sent()
    assert answer.calls[0][0] == 'https://sis-t.redsys.es:25443/sis/rest/trataPeticionREST'
    assert sent['ds_merchant_order'] == result.payment.gateway_payment_id
    assert sent['ds_merchant_order'] != first.payments.get().gateway_payment_id
    assert {name: sent[name] for name in (
        'ds_merchant_amount', 'ds_merchant_currency', 'ds_merchant_transactiontype',
        'ds_merchant_identifier', 'ds_merchant_cof_ini', 'ds_merchant_cof_type',
        'ds_merchant_cof_txnid', 'ds_merchant_directpayment', 'ds_merchant_excep_sca')} == {
        'ds_merchant_amount': str(redsys.to_cents(renewal.total)),
        'ds_merchant_currency': '978', 'ds_merchant_transactiontype': '0',
        'ds_merchant_identifier': CARD['Ds_Merchant_Identifier'], 'ds_merchant_cof_ini': 'N',
        'ds_merchant_cof_type': 'R', 'ds_merchant_cof_txnid': CARD['Ds_Merchant_Cof_Txnid'],
        'ds_merchant_directpayment': 'true', 'ds_merchant_excep_sca': 'MIT'}
    assert repeat.charge_saved_card(renewal, '2027-01').outcome == 'paid'
    assert len(answer.calls) == 1 and paid_hooks(events) == 2


def test_redsys_notifying_the_repeat_charge_too_pays_once_and_stores_no_card(
        doctor, entity, redsys_on, events, monkeypatch):
    saved_on_redsys(doctor)
    answer = ChargeAnswer()
    monkeypatch.setattr('accounts.http.post_json', answer)
    renewal = subscription_order(doctor, save_card=True)
    result = repeat.charge_saved_card(renewal, 'P1')
    notify_paid(result.payment, **{**CARD, 'Ds_Merchant_Identifier': 'other'})
    assert paid_hooks(events) == 2
    assert SavedCard.objects.get().token == CARD['Ds_Merchant_Identifier']


@pytest.mark.parametrize('code', ['0116', '0190', '0195', '9915'])
def test_a_redsys_decline_fails_the_charge_with_its_code(doctor, entity, redsys_on, code,
                                                         monkeypatch):
    saved_on_redsys(doctor)
    monkeypatch.setattr('accounts.http.post_json', ChargeAnswer(response=code))
    renewal = subscription_order(doctor, save_card=False)
    result = repeat.charge_saved_card(renewal, 'P1')
    assert (result.outcome, result.code, result.payment.state) == ('declined', code, 'failed')
    assert Order.objects.get(pk=renewal.pk).state == 'pending_payment'
    answer = ChargeAnswer()
    monkeypatch.setattr('accounts.http.post_json', answer)
    again = repeat.charge_saved_card(renewal, 'P1')
    assert again.outcome == 'paid'
    assert answer.sent()['ds_merchant_order'] != result.payment.gateway_payment_id


@pytest.mark.parametrize('answer', [
    ChargeAnswer(fault=urllib.error.URLError('down')),
    ChargeAnswer(fault=socket.timeout('timed out')),
    ChargeAnswer(fault=urllib.error.HTTPError('https://x', 500, 'no', {}, None)),
    ChargeAnswer(key='Zm9vYmFyYmF6cXV4cXV1eHF1dXhxdXV4'),  # another key: bad signature
    ChargeAnswer(order='9999ZZZZZZZZ'),
    ChargeAnswer(Ds_Amount='1'),
    ChargeAnswer(Ds_Currency='840'),
    ChargeAnswer(Ds_TransactionType='3'),
    ChargeAnswer(error='SIS0051'),
    ChargeAnswer(Ds_Response='nonsense'),
], ids=['url', 'timeout', 'http500', 'signature', 'order', 'amount', 'currency', 'type',
        'errorCode', 'code'])
def test_an_untrustworthy_redsys_answer_needs_attention_and_never_charges_again(
        doctor, entity, redsys_on, answer, sentry, monkeypatch, caplog):
    saved_on_redsys(doctor)
    monkeypatch.setattr('accounts.http.post_json', answer)
    renewal = subscription_order(doctor, save_card=False)
    result = repeat.charge_saved_card(renewal, 'P1')
    assert result.outcome == 'unknown' and result.payment.state == 'needs_attention'
    assert sentry == [f'payment repeat charge outcome unknown: order {renewal.pk}']
    again = ChargeAnswer()
    monkeypatch.setattr('accounts.http.post_json', again)
    assert repeat.charge_saved_card(renewal, 'P1').outcome == 'unknown' and again.calls == []
    for secret in (KEY, CARD['Ds_Merchant_Identifier'], CARD['Ds_Merchant_Cof_Txnid']):
        assert secret not in caplog.text


def test_the_redsys_answer_is_read_from_the_rest_json(doctor, entity, redsys_on, monkeypatch):
    saved_on_redsys(doctor)
    monkeypatch.setattr('accounts.http.post_json', lambda *a, **k: json.dumps([1]))
    result = repeat.charge_saved_card(subscription_order(doctor, False), 'P1')
    assert result.outcome == 'unknown'


def test_a_redsys_card_is_charged_only_through_redsys(doctor, entity, redsys_on):
    saved_on_redsys(doctor)
    card = SavedCard.objects.get()
    card.gateway = 'fake'
    with pytest.raises(gateways.GatewayError):
        redsys.charge_saved_card(card, Payment(amount=Decimal('1.00'), currency='EUR'))
