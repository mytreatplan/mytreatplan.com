"""Bank transfer (epic-payments entry 5): one test (or more) per row of the plan's matrix.
The admin site is the fake of integration/tests/snapshot_rows.py (no network)."""
import logging
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.core.mail import EmailMessage
from django.core.management import call_command
from django.utils import formats, timezone

from accounts import fiscal
from accounts.models import Profile
from catalogue.models import BillingCompany, BillingRule
from integration.tests.snapshot_rows import snapshot_writes
from payments import bank_transfer, gateways, service, transfers
from payments.models import Order, Payment
from payments.tests.conftest import COMPANY
from payments.tests.test_checkout import browser, html
from payments.tests.test_service import actor, new
from payments.tests.test_service import pay as pay_by

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

BANK = {'bank_account_holder': 'MyTPDSO Spain S.L.', 'iban': 'ES9121000418450200051332',
        'bic': 'CAIXESBBXXX', 'bank_name': 'CaixaBank'}


@pytest.fixture
def bank(doctor, fake_on):
    with snapshot_writes():
        BillingCompany.objects.filter(code='ES').update(**BANK)


def start_transfer(user):
    order = new(user)
    return service.start_payment(order, 'bank_transfer', expected_version=order.version,
                                 actor=actor(user))


def attempt(order):
    return Payment.objects.get(order=order, gateway='bank_transfer')


def backdate(order, days):
    """The transfer attempt was opened `days` days ago (its session still ends 14 days after
    that)."""
    opened = timezone.now() - timedelta(days=days)
    Payment.objects.filter(order=order).update(
        created_at=opened, session_expires_at=opened + bank_transfer.SESSION_LIFETIME)
    Order.objects.filter(pk=order.pk).update(pending_at=opened)


def staff():
    return service.SYSTEM_ACTOR


# --- Choosing transfer -------------------------------------------------------------------

def test_a_doctor_pays_by_transfer_and_sees_the_details(doctor, entity, bank, events):
    client = browser(doctor)
    order = new(doctor)
    checkout = f'/en/checkout/{order.pk}/'
    page = html(client.get(checkout))
    assert 'value="bank_transfer"' in page
    response = client.post(checkout, {
        'action': 'pay', 'entity': str(entity.pk), 'gateway': 'bank_transfer',
        'version': order.version, 'total': str(order.total)})
    assert response.status_code == 302 and response['Location'] == f'{checkout}transfer/'
    order.refresh_from_db()
    assert order.state == 'pending_payment' and order.gateway == 'bank_transfer'
    payment = attempt(order)
    assert payment.state == 'open'
    assert payment.session_expires_at - payment.created_at == timedelta(days=14)
    page = html(client.get(response['Location']))
    deadline = (payment.session_expires_at - timedelta(days=1)).astimezone(
        ZoneInfo('Europe/Madrid')).date()  # the last full day to pay
    for text in ('MyTPDSO Spain S.L.', 'ES91 2100 0418 4502 0005 1332', 'CAIXESBBXXX',
                 'CaixaBank', '100.00 EUR', order.reference,
                 formats.date_format(deadline, 'DATE_FORMAT')):
        assert text in page, text
    assert 'ES9121000418450200051332' not in page  # grouped for reading
    assert [event for event, *_ in events] == ['created']


def test_the_page_is_the_order_s_own_doctor_only(doctor, entity, bank):
    from subscriptions.tests.helpers import doctor as make_doctor
    order = start_transfer(doctor)
    other = make_doctor('other@example.com')
    assert browser(other).get(f'/en/checkout/{order.pk}/transfer/').status_code == 404


# --- Not offered -------------------------------------------------------------------------

def test_transfer_is_not_offered_without_bank_details(doctor, entity, fake_on):
    order = new(doctor)
    assert 'bank_transfer' in order.gateways
    assert 'bank_transfer' not in gateways.offered(order)
    assert 'value="bank_transfer"' not in html(browser(doctor).get(f'/en/checkout/{order.pk}/'))


def test_transfer_is_offered_in_spain_only_under_an_es_only_limit(doctor, entity, bank):
    from subscriptions.tests.helpers import doctor as make_doctor
    with snapshot_writes():
        for rule in [r for r in BillingRule.objects.all() if 'bank_transfer' in r.gateways]:
            rule.gateway_countries = {'bank_transfer': ['ES']}
            rule.save()
    portuguese = make_doctor('pt@example.com', country='PT')
    fiscal.create(portuguese, dict(COMPANY, country='PT', tax_id='PT501964843'))
    for user, offered in ((doctor, True), (portuguese, False)):
        try:
            order = new(user)
        except service.OrderError:
            assert not offered  # nothing to pay with: no order at all
            continue
        page = html(browser(user).get(f'/en/checkout/{order.pk}/'))
        assert ('value="bank_transfer"' in page) is offered


# --- Returning later -----------------------------------------------------------------------

def test_checkout_and_return_lead_to_the_details(doctor, entity, bank):
    order = start_transfer(doctor)
    client = browser(doctor)
    for page in ('', 'return/'):
        response = client.get(f'/en/checkout/{order.pk}/{page}')
        assert response.status_code == 302
        assert response['Location'] == f'/en/checkout/{order.pk}/transfer/'
    assert 'Try again' not in html(client.get(f'/en/checkout/{order.pk}/transfer/'))
    # The doctor can still go back to the checkout to pick another method.
    assert 'data-gateways' in html(client.get(f'/en/checkout/{order.pk}/?change=1'))


def test_the_page_of_an_order_without_an_open_transfer_shows_the_return(doctor, entity, bank):
    order = new(doctor)
    response = browser(doctor).get(f'/en/checkout/{order.pk}/transfer/')
    assert response.status_code == 302 and response['Location'].endswith('/return/')


# --- Switching to card -------------------------------------------------------------------

def test_switching_to_another_method_closes_the_transfer_attempt(doctor, entity, bank):
    order = start_transfer(doctor)
    first = attempt(order)
    order = service.start_payment(order, 'fake', expected_version=order.version,
                                  actor=actor(doctor))
    first.refresh_from_db()
    assert first.state == 'closed'
    assert order.gateway == 'fake'
    assert Payment.objects.get(order=order, state='open').gateway == 'fake'


# --- Pay requests that are refused, and re-choosing transfer ---------------------------------

def test_a_stale_pay_request_leaves_the_transfer_attempt_open(doctor, entity, bank):
    order = start_transfer(doctor)
    before = attempt(order)
    with pytest.raises(service.StaleVersion):
        service.start_payment(order, 'fake', expected_version=order.version - 1,
                              actor=actor(doctor))
    after = attempt(order)
    assert after.state == 'open' and after.session_expires_at == before.session_expires_at


def test_a_pay_request_without_a_gateway_leaves_the_transfer_attempt_open(doctor, entity,
                                                                         bank):
    order = start_transfer(doctor)
    before = attempt(order)
    response = browser(doctor).post(f'/en/checkout/{order.pk}/', {
        'action': 'pay', 'gateway': '', 'version': order.version})
    assert response.status_code == 302
    after = attempt(order)
    assert after.state == 'open' and after.session_expires_at == before.session_expires_at
    with pytest.raises(service.GatewayRefused):
        service.start_payment(order, '', expected_version=order.version, actor=actor(doctor))
    assert attempt(order).state == 'open'


def test_choosing_transfer_again_keeps_the_first_deadline(doctor, entity, bank):
    order = start_transfer(doctor)
    backdate(order, 5)
    deadline = attempt(order).session_expires_at
    order.refresh_from_db()
    again = service.start_payment(order, 'bank_transfer', expected_version=order.version,
                                  actor=actor(doctor))
    assert Payment.objects.filter(order=again, state='open').count() == 1
    assert Payment.objects.get(order=again, state='open').session_expires_at == deadline
    # card, then transfer again: still the day-14 deadline
    again = service.start_payment(again, 'fake', expected_version=again.version,
                                  actor=actor(doctor))
    again = service.start_payment(again, 'bank_transfer', expected_version=again.version,
                                  actor=actor(doctor))
    assert Payment.objects.get(order=again, state='open').session_expires_at == deadline


def test_transfer_cannot_be_chosen_once_the_14_days_ended(doctor, entity, bank):
    order = start_transfer(doctor)
    backdate(order, 15)
    order.refresh_from_db()
    order = service.start_payment(order, 'fake', expected_version=order.version,
                                  actor=actor(doctor))
    with pytest.raises(service.GatewayRefused):
        service.start_payment(order, 'bank_transfer', expected_version=order.version,
                              actor=actor(doctor))
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    assert not Payment.objects.filter(order=order, gateway='bank_transfer',
                                      state='open').exists()


def test_one_reminder_per_order_even_with_another_transfer_attempt(doctor, entity, bank):
    order = start_transfer(doctor)
    backdate(order, 7)
    assert transfers.remind_due() == 1
    order.refresh_from_db()
    service.start_payment(order, 'bank_transfer', expected_version=order.version,
                          actor=actor(doctor))
    assert Payment.objects.filter(order=order, gateway='bank_transfer').count() == 2
    assert transfers.remind_due() == 0 and len(mail.outbox) == 1


# --- Day 7 -------------------------------------------------------------------------------

def test_a_reminder_goes_on_day_7_once(doctor, entity, bank):
    order = start_transfer(doctor)
    backdate(order, 6)
    assert transfers.remind_due() == 0
    backdate(order, 7)
    assert transfers.remind_due() == 1
    assert transfers.remind_due() == 0
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [doctor.email]
    assert order.reference in message.subject
    for text in (order.reference, '100.00 EUR', BANK['bank_account_holder'],
                 'ES91 2100 0418 4502 0005 1332', BANK['bic'], BANK['bank_name'],
                 f'/en/checkout/{order.pk}/transfer/'):
        assert text in message.body, text
    assert attempt(order).reminded_at is not None


def test_the_reminder_is_in_the_doctor_s_language(doctor, entity, bank):
    Profile.objects.create(user=doctor, language='es')
    order = start_transfer(doctor)
    backdate(order, 7)
    transfers.remind_due()
    message = mail.outbox[0]
    assert 'Recordatorio' in message.subject and 'Titular de la cuenta' in message.body
    assert f'/es/checkout/{order.pk}/transfer/' in message.body


def test_a_failed_send_is_retried_by_the_next_run(doctor, entity, bank, monkeypatch, caplog):
    order = start_transfer(doctor)
    backdate(order, 8)
    real = EmailMessage.send

    def broken(self, *args, **kwargs):
        raise OSError(f'refused {self.to[0]} {BANK["iban"]}')

    monkeypatch.setattr(EmailMessage, 'send', broken)
    with caplog.at_level(logging.DEBUG):
        assert transfers.remind_due() == 0
    assert attempt(order).reminded_at is None and mail.outbox == []
    logged = caplog.text
    assert 'OSError' in logged and str(attempt(order).pk) in logged
    assert doctor.email not in logged
    monkeypatch.setattr(EmailMessage, 'send', real)
    assert transfers.remind_due() == 1 and len(mail.outbox) == 1


def test_the_bank_details_are_never_logged(doctor, entity, bank, caplog):
    order = start_transfer(doctor)
    backdate(order, 7)
    with caplog.at_level(logging.DEBUG):
        client = browser(doctor)
        html(client.get(f'/en/checkout/{order.pk}/transfer/'))
        transfers.remind_due()
        service.cancel(order, expected_version=Order.objects.get(pk=order.pk).version,
                       actor=actor(doctor))
    for secret in (BANK['iban'], 'ES91 2100', BANK['bic'], BANK['bank_account_holder']):
        assert secret not in caplog.text


def test_only_pending_orders_with_a_live_attempt_are_reminded(doctor, entity, bank):
    order = start_transfer(doctor)
    backdate(order, 15)  # the session ended
    assert transfers.remind_due() == 0
    backdate(order, 8)
    service.cancel(order, expected_version=Order.objects.get(pk=order.pk).version,
                   actor=actor(doctor))
    assert transfers.remind_due() == 0 and mail.outbox == []


def test_an_order_without_an_account_is_marked_reminded_without_an_email(doctor, entity,
                                                                         bank):
    order = start_transfer(doctor)
    backdate(order, 7)
    Order.objects.filter(pk=order.pk).update(user=None)
    assert transfers.remind_due() == 0
    assert mail.outbox == [] and attempt(order).reminded_at is not None


def test_the_command_prints_a_count_only(doctor, entity, bank):
    order = start_transfer(doctor)
    backdate(order, 7)
    out = StringIO()
    call_command('remind_transfers', stdout=out)
    assert out.getvalue() == 'remind_transfers: 1 reminder(s) sent\n'


# --- Day 14 ------------------------------------------------------------------------------

def test_the_order_expires_when_the_14_days_end(doctor, entity, bank, events):
    order = start_transfer(doctor)
    backdate(order, 13)
    assert service.expire_stale() == 0
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    backdate(order, 14)
    assert service.expire_stale() == 1
    assert service.expire_stale() == 0
    order.refresh_from_db()
    assert order.state == 'expired' and attempt(order).state == 'closed'
    assert [e for e, *_ in events].count('expired') == 1


# --- Mark paid ---------------------------------------------------------------------------

def test_staff_mark_the_transfer_paid(doctor, entity, bank, events):
    order = start_transfer(doctor)
    paid = service.mark_transfer_paid(order, Decimal('100.00'),
                                      expected_version=order.version, actor=staff())
    assert paid.state == 'paid' and paid.paid_at is not None
    payment = attempt(order)
    assert payment.state == 'confirmed' and payment.confirmed_at is not None
    assert [e for e, *_ in events].count('paid') == 1


def test_marking_paid_twice_changes_nothing(doctor, entity, bank, events):
    order = start_transfer(doctor)
    first = service.mark_transfer_paid(order, Decimal('100.00'),
                                       expected_version=order.version, actor=staff())
    again = service.mark_transfer_paid(order, Decimal('100.00'),
                                       expected_version=order.version, actor=staff())
    assert again.version == first.version and again.state == 'paid'
    assert [e for e, *_ in events].count('paid') == 1
    assert order.changes.filter(event='paid').count() == 1


def test_a_stale_version_is_refused(doctor, entity, bank):
    order = start_transfer(doctor)
    with pytest.raises(service.StaleVersion):
        service.mark_transfer_paid(order, Decimal('100.00'),
                                   expected_version=order.version - 1, actor=staff())
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    assert attempt(order).state == 'open'


@pytest.mark.parametrize('amount', ['99.99', '100.01', '0', 'abc'])
def test_another_amount_is_refused(doctor, entity, bank, events, amount):
    order = start_transfer(doctor)
    with pytest.raises(service.AmountMismatch):
        service.mark_transfer_paid(order, amount, expected_version=order.version,
                                   actor=staff())
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    assert attempt(order).state == 'open'
    assert 'paid' not in [e for e, *_ in events]


def test_an_expired_or_cancelled_order_is_refused(doctor, entity, bank):
    expired = start_transfer(doctor)
    backdate(expired, 14)
    service.expire_stale()
    expired.refresh_from_db()
    cancelled = start_transfer(doctor)
    service.cancel(cancelled, expected_version=cancelled.version, actor=actor(doctor))
    cancelled.refresh_from_db()
    for order in (expired, cancelled):
        with pytest.raises(service.InvalidTransition):
            service.mark_transfer_paid(order, Decimal('100.00'),
                                       expected_version=order.version, actor=staff())
        assert Order.objects.get(pk=order.pk).state == order.state
        assert not Payment.objects.filter(order=order, state='confirmed').exists()


def test_an_order_without_a_transfer_attempt_is_refused(doctor, entity, bank):
    order = new(doctor)
    order = service.start_payment(order, 'fake', expected_version=order.version,
                                  actor=actor(doctor))
    with pytest.raises(service.NoPaymentMethod):
        service.mark_transfer_paid(order, Decimal('100.00'),
                                   expected_version=order.version, actor=staff())


def test_a_doctor_cannot_record_a_transfer(doctor, entity, bank):
    order = start_transfer(doctor)
    with pytest.raises(service.InvalidActor):
        service.mark_transfer_paid(order, Decimal('100.00'),
                                   expected_version=order.version, actor=actor(doctor))


def test_a_paid_order_is_not_reported_as_transfer_paid_by_another_gateway(doctor, entity,
                                                                           bank):
    order = pay_by(new(doctor), doctor)
    with pytest.raises(service.InvalidTransition):
        service.mark_transfer_paid(order, Decimal('100.00'),
                                   expected_version=order.version, actor=staff())


def test_a_money_in_another_currency_is_refused(doctor, entity, bank):
    from mytp_core.money import Money
    order = start_transfer(doctor)
    with pytest.raises(service.AmountMismatch):
        service.mark_transfer_paid(order, Money(Decimal('100.00'), 'USD'),
                                   expected_version=order.version, actor=staff())
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    paid = service.mark_transfer_paid(order, Money(Decimal('100.00'), 'EUR'),
                                      expected_version=order.version, actor=staff())
    assert paid.state == 'paid'


# --- Cancelling --------------------------------------------------------------------------

def test_the_doctor_cancels_a_transfer_order(doctor, entity, bank, events):
    order = start_transfer(doctor)
    client = browser(doctor)
    response = client.post(f'/en/checkout/{order.pk}/', {
        'action': 'cancel', 'version': order.version})
    assert response.status_code == 302
    order.refresh_from_db()
    assert order.state == 'cancelled' and attempt(order).state == 'closed'
    assert [e for e, *_ in events].count('cancelled') == 1


def test_a_stale_cancel_leaves_the_attempt_open(doctor, entity, bank):
    order = start_transfer(doctor)
    with pytest.raises(service.StaleVersion):
        service.cancel(order, expected_version=order.version - 1, actor=actor(doctor))
    assert attempt(order).state == 'open'
    assert Order.objects.get(pk=order.pk).state == 'pending_payment'


def test_cancel_with_another_gateway_s_attempt_open_is_still_refused(doctor, entity, bank):
    gateways.register('stub', lambda payment, request: '/x/')
    try:
        order = new(doctor)
        Order.objects.filter(pk=order.pk).update(gateways=['stub'])
        order.refresh_from_db()
        order = service.start_payment(order, 'stub', expected_version=order.version,
                                      actor=actor(doctor))
        with pytest.raises(service.AttemptOpen):
            service.cancel(order, expected_version=order.version, actor=actor(doctor))
        assert Order.objects.get(pk=order.pk).state == 'pending_payment'
    finally:
        gateways.unregister('stub')
