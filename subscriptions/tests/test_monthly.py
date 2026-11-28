"""Monthly charges and failed payments (epic-subscriptions 8.7): one test (or more) per row of
the plan's matrix, rerun idempotence, and the included cases earned month by month. The
daily job's monthly step (`service.charge_months_due`, then `drop_unrenewed`) charges the
saved card outside any transaction, so these tests commit for real. Most tests pass the job's
day to the service (a month due in the future) rather than moving the record's term, so the
purchase's included-case grant keeps its term. The emails are queued after commit and sent
here by hand (`subscriptions.tasks`). On the fake gateway and on Redsys (never called: its
notification and REST answer are built and signed here with Redsys's public test key). The
invoice PDF is mocked."""
import socket
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from dateutil.relativedelta import relativedelta
from django.core import mail
from django.utils import translation
from django.utils.formats import date_format

from catalogue import resolver
from integration.tests.snapshot_rows import ADA
from invoices.models import Invoice
from payments import fake, webhooks
from payments.models import Order, SavedCard
from payments.tests.conftest import country_map, fake_on  # noqa: F401
from payments.tests.test_redsys import (SITE, body_of, notification, post,  # noqa: F401
                                        redsys_on, sentry)
from payments.tests.test_repeat import CARD, ChargeAnswer, fake_charge
from payments.tests.test_webhooks import attempt, last_log
from subscriptions import included, service, tasks
from subscriptions.tests.helpers import ADVANCED, MANUAL
from subscriptions.tests.test_purchase import (browser, buy, fake_pdf, html,  # noqa: F401
                                               order_of, pay_on_fake, spanish)
from subscriptions.tests.test_renewals import (INCLUDED, annual, loaded,  # noqa: F401
                                               queued, run_job, send_all)

pytestmark = [pytest.mark.django_db(transaction=True),
              pytest.mark.usefixtures('clear_cache', 'portal_urls')]

DAY = timedelta(days=1)
MONTH_TOTAL = Decimal('72.60')  # Advanced of Iberia, 60.00 EUR a month + 21 % VAT


def today():
    return resolver.today()


def record(user):
    return service.record_of(user)


def orders(user):
    return Order.objects.filter(user=user).count()


def latest_order(user):
    return Order.objects.filter(user=user).order_by('-created_at').first()


def monthly(user=None):
    """A doctor who bought Advanced monthly (paid on the fake gateway: the card saved)."""
    user = user or spanish()
    buy(user, 'monthly')
    order = pay_on_fake(order_of(user), user)
    found = record(user)
    assert order.state == 'paid' and (found.tier, found.billing_period) == ('advanced',
                                                                         'monthly')
    return user


def monthly_on_redsys(user):
    buy(user, 'monthly')
    order, payment = attempt(user, 'redsys', order=order_of(user))
    data = notification(payment)
    data.update(CARD)
    assert post(body_of(data)).status_code == 200
    webhooks.process(last_log().pk)
    assert Order.objects.get(pk=order.pk).state == 'paid'
    return order


def month(user, n):
    """The due date of month `n + 1` of the record's term (n = 1: month 2)."""
    return record(user).started_on + relativedelta(months=n)


def pay_months(user, through=11):
    """Charge months 2 to `through + 1` of the term on their due dates (the fake card)."""
    for n in range(1, through + 1):
        assert service.charge_months_due(month(user, n)) == {'paid': 1}


def charge_of(user, due_on):
    return record(user).monthly_charges.get(due_on=due_on)


def shown(day):
    with translation.override('en'):
        return date_format(day, 'DATE_FORMAT')


def send_failed(charge):
    return tasks.send_month_failed.call(str(charge.pk), charge.attempts)


# --- Due dates ---------------------------------------------------------------------------------

def test_month_n_is_due_started_on_plus_n_months_clamped_and_the_renewal_after_the_term():
    term = SimpleNamespace(started_on=date(2027, 1, 31), ends_on=date(2028, 1, 30))
    dates = service._due_dates(term)
    assert dates[0] == (date(2027, 2, 28), False)  # 31 January: month 2 on 28 February
    assert dates[1] == (date(2027, 3, 31), False)  # never drifts from the 31st
    assert dates[2] == (date(2027, 4, 30), False)
    assert len(dates) == 12 and dates[-1] == (date(2028, 1, 31), True)
    leap = SimpleNamespace(started_on=date(2028, 1, 31), ends_on=date(2029, 1, 30))
    assert service._due_dates(leap)[0] == (date(2028, 2, 29), False)


# --- Month charged (acceptance) ----------------------------------------------------------------

def test_the_job_twice_charges_month_two_on_redsys_once_with_its_invoice_and_receipt(
        loaded, monkeypatch):
    """Acceptance 1: month 2 due today, the job twice: one order, one charge, invoiced."""
    user = spanish()
    first = monthly_on_redsys(user)
    started_on = today() - relativedelta(months=1)
    found = record(user)
    service.change(user, expected_version=found.version, actor=service.SYSTEM_ACTOR,
                   reason='test', started_on=started_on,
                   ends_on=started_on + relativedelta(months=12) - DAY)
    answer = ChargeAnswer()
    monkeypatch.setattr('accounts.http.post_json', answer)
    out = run_job()
    assert 'months: paid 1' in out and '0 dropped to Basic' in out
    assert 'months: none' in run_job()
    assert len(answer.calls) == 1 and orders(user) == 2
    order = latest_order(user)
    assert order.pk != first.pk and (order.state, order.gateway) == ('paid', 'redsys')
    assert (order.actor_type, order.voucher_id, order.total) == ('system', None, MONTH_TOTAL)
    line = order.lines.get()
    assert (str(line.subscription_id), line.period) == (ADVANCED, 'monthly')
    charge = record(user).monthly_charges.get()
    assert (charge.due_on, charge.order_id, charge.outcome, charge.attempts) == (
        today(), order.pk, 'paid', 0)
    found = record(user)
    assert (found.tier, found.status, found.started_on) == ('advanced', 'active', started_on)
    invoice = Invoice.objects.get(order=order)
    assert invoice.total == MONTH_TOTAL
    assert send_all('send_month_receipt') == [True]
    message = mail.outbox[-1]
    assert message.subject == 'Your monthly payment for MyTreatPlan Advanced'
    body = message.body
    assert order.reference in body and '72.60 EUR' in body
    assert f'Next charge: {shown(started_on + relativedelta(months=2))}' in body
    assert message.attachments == [(f'{invoice.number}.pdf', b'%PDF-fake',
                                    'application/pdf')]
    assert send_all('send_month_receipt') == [False]
    assert not queued('send_month_failed') and not queued('send_renewal_receipt')


def test_the_included_cases_are_earned_month_by_month(loaded):
    user = monthly()
    assert service.included_left(user, today()) == (1, 1)  # 12 × 1 // 12
    second = month(user, 1)
    assert service.included_left(user, second) == (1, 1)  # not charged yet
    assert service.charge_months_due(second) == {'paid': 1}
    assert service.charge_months_due(second) == {}  # rerun
    assert service.included_left(user, second) == (2, 2)
    assert service.charge_months_due(month(user, 2)) == {'paid': 1}
    assert service.included_left(user, month(user, 2)) == (3, 3)
    assert orders(user) == 3


# --- Reminder ----------------------------------------------------------------------------------

def test_the_reminder_goes_30_days_before_the_commitment_renews_once(loaded):
    user = monthly()
    found = record(user)
    renews_on = found.ends_on + DAY
    assert service.due_reminders(renews_on - timedelta(days=31)) == 0
    assert service.due_reminders(renews_on - timedelta(days=30)) == 1
    assert send_all('send_renewal_reminder') == [True]
    assert service.due_reminders(renews_on - timedelta(days=30)) == 0
    message = mail.outbox[-1]
    assert message.subject == f'Your MyTreatPlan Advanced subscription renews on {shown(renews_on)}'
    body = message.body
    assert 'paid monthly with a 12-month commitment' in body
    assert f'Renewal date: {shown(renews_on)}' in body
    assert '60.00 EUR per month, 12-month commitment' in body
    last4 = SavedCard.objects.get(user=user).last4
    assert f'ending in {last4}' in body and 'Turn off' in body
    assert send_all('send_renewal_reminder') == [False]


# --- Declined, retried, then Basic -------------------------------------------------------------

def test_a_declined_month_is_past_due_emailed_and_retried_on_days_1_3_7_then_basic(
        loaded):
    user = monthly()
    pay_months(user, through=1)
    due = month(user, 2)  # month 3
    order = None
    for day, retry in ((due, due + DAY), (due + DAY, due + 3 * DAY),
                       (due + 3 * DAY, due + 7 * DAY)):
        assert service.charge_months_due(day - DAY) == {}  # not due yet
        fake.decline_next_charge('0190')
        assert service.charge_months_due(day) == {'declined': 1}
        assert service.charge_months_due(day) == {}  # rerun: no second charge
        found = record(user)
        assert (found.tier, found.status) == ('advanced', 'past_due')
        assert service.effective(user, day).tier == 'advanced'
        assert service.included_left(user, day) == (2, 2)  # past due keeps the cases
        charge = charge_of(user, due)
        assert (charge.outcome, charge.next_try_on) == ('declined', retry)
        order = order or latest_order(user)
        assert charge.order_id == order.pk and order.payments.count() == charge.attempts
        assert send_failed(charge) is True and send_failed(charge) is False
        message = mail.outbox[-1]
        assert message.subject == 'Pay this month of your MyTreatPlan Advanced subscription'
        assert f'{SITE}/en/checkout/{order.pk}/' in message.body
        assert f'We will try your card again on {shown(retry)}' in message.body
        assert f'after our last try on {shown(due + 7 * DAY)}' in message.body
    assert orders(user) == 3  # the purchase, month 2, and one order for month 3
    changes = record(user).changes.filter(reason='month_unpaid')
    assert changes.count() == 1 and changes.get().actor_type == 'system'
    # Day 7: the last retry declined, Basic that day.
    fake.decline_next_charge('0190')
    assert service.charge_months_due(due + 7 * DAY) == {'dropped': 1}
    found = record(user)
    assert (found.tier, found.subscription_id, found.billing_period, found.started_on) == (
        'basic', None, '', due + 7 * DAY)
    assert found.changes.order_by('-version').first().reason == 'renewal_unpaid'
    assert service.included_left(user, due + 7 * DAY) is None
    charge = charge_of(user, due)
    assert (charge.attempts, charge.next_try_on) == (4, None)
    assert send_failed(charge) is True
    message = mail.outbox[-1]
    assert 'has ended' in message.subject and 'Basic from today' in message.body
    assert service.charge_months_due(due + 8 * DAY) == {}
    assert service.drop_unrenewed(due + 8 * DAY) == 0


def test_a_retry_that_pays_on_day_3_makes_the_record_active_and_retries_no_more(loaded):
    user = monthly()
    due = month(user, 1)
    for day in (due, due + DAY):
        fake.decline_next_charge('0190')
        assert service.charge_months_due(day) == {'declined': 1}
    assert record(user).status == 'past_due'
    assert service.charge_months_due(due + 2 * DAY) == {}
    assert service.charge_months_due(due + 3 * DAY) == {'paid': 1}
    found = record(user)
    assert (found.tier, found.status) == ('advanced', 'active')
    change = found.changes.order_by('-version').first()
    assert (change.reason, change.order_id) == ('month_paid', latest_order(user).pk)
    charge = charge_of(user, due)
    assert (charge.outcome, charge.next_try_on, charge.attempts) == ('paid', None, 2)
    assert service.charge_months_due(due + 7 * DAY) == {}
    assert service.included_left(user, due + 7 * DAY) == (2, 2)
    assert send_all('send_month_receipt') == [True]


def test_paying_the_emailed_link_ends_the_retries(loaded):
    user = monthly()
    due = month(user, 1)
    fake.decline_next_charge('0190')
    assert service.charge_months_due(due) == {'declined': 1}
    order = pay_on_fake(latest_order(user), user)
    assert order.state == 'paid'
    assert (record(user).status, charge_of(user, due).outcome) == ('active', 'paid')
    assert service.charge_months_due(due + DAY) == {}
    assert send_all('send_month_failed') == [False]  # paid since: not sent any more
    assert send_all('send_month_receipt') == [True]


def test_a_retry_makes_a_fresh_order_when_the_previous_one_expired(loaded):
    user = monthly()
    due = month(user, 1)
    fake.decline_next_charge('0190')
    assert service.charge_months_due(due) == {'declined': 1}
    first = latest_order(user)
    Order.objects.filter(pk=first.pk).update(state='expired')
    assert service.charge_months_due(due + DAY) == {'paid': 1}
    order = latest_order(user)
    assert order.pk != first.pk and order.state == 'paid'
    assert charge_of(user, due).order_id == order.pk
    assert record(user).status == 'active'


def test_a_payment_after_the_drop_gives_the_term_back(loaded):
    user = monthly()
    found = record(user)
    term = (found.started_on, found.ends_on)
    due = month(user, 1)
    for day in (due, due + DAY, due + 3 * DAY, due + 7 * DAY):
        fake.decline_next_charge('0190')
        service.charge_months_due(day)
    assert record(user).tier == 'basic'
    pay_on_fake(latest_order(user), user)
    found = record(user)
    assert (found.tier, str(found.subscription_id), found.billing_period) == (
        'advanced', ADVANCED, 'monthly')
    assert ((found.started_on, found.ends_on), found.status, found.auto_renew) == (
        term, 'active', True)
    assert found.changes.order_by('-version').first().reason == 'month_paid'
    assert service.included_left(user, due + 7 * DAY) == (2, 2)


# --- No card, unknown outcome ------------------------------------------------------------------

def test_no_saved_card_is_treated_as_declined_with_a_payment_link(loaded):
    user = monthly()
    SavedCard.objects.filter(user=user).delete()
    due = month(user, 1)
    assert service.charge_months_due(due) == {'declined': 1}
    order = latest_order(user)
    assert order.state == 'checkout' and not order.payments.exists()
    assert record(user).status == 'past_due'
    assert send_all('send_month_failed') == [True]
    assert f'/en/checkout/{order.pk}/' in mail.outbox[-1].body
    assert service.charge_months_due(due + DAY) == {'declined': 1}
    assert orders(user) == 2


def test_an_unknown_outcome_keeps_the_level_and_sends_nothing(loaded, sentry, monkeypatch):
    user = monthly()

    def timeout(card, payment):
        raise socket.timeout('timed out')
    fake_charge(monkeypatch, timeout)
    due = month(user, 1)
    assert service.charge_months_due(due) == {'unknown': 1}
    order = latest_order(user)
    assert sentry == [f'payment repeat charge outcome unknown: order {order.pk}']
    for day in (due + DAY, due + 3 * DAY, due + 7 * DAY):
        assert service.charge_months_due(day) == {}
    assert not queued('send_month_failed')
    found = record(user)
    assert (found.tier, found.status) == ('advanced', 'active')
    assert service.drop_unrenewed(found.ends_on + DAY) == 0  # staff decide


# --- The commitment renews, or ends ------------------------------------------------------------

def test_the_first_month_after_the_term_renews_the_commitment(loaded):
    user = monthly()
    first = record(user).changes.get(reason='purchase').order_id
    pay_months(user)
    found = record(user)
    ends_on = found.ends_on
    assert service.included_left(user, ends_on) == (INCLUDED, INCLUDED)  # 12 months paid
    assert service.charge_months_due(ends_on) == {}
    assert service.charge_months_due(ends_on + DAY) == {'paid': 1}
    order = latest_order(user)
    found = record(user)
    assert (found.started_on, found.ends_on) == (
        ends_on + DAY, ends_on + relativedelta(months=12))
    assert (found.tier, found.status, found.billing_period, found.auto_renew) == (
        'advanced', 'active', 'monthly', True)
    change = found.changes.order_by('-version').first()
    assert (change.reason, change.order_id, change.actor_type) == ('renewal', order.pk,
                                                                   'system')
    assert included.has_term_grant(order.pk) and included.has_term_grant(first)
    assert service.counting_purchase(user, ends_on + DAY) == order.pk
    assert service.included_left(user, ends_on + DAY) == (1, 1)
    assert service.drop_unrenewed(ends_on + 2 * DAY) == 0
    assert service.charge_months_due(ends_on + 2 * DAY) == {}
    receipts = send_all('send_month_receipt')
    assert receipts == [True] * 12
    assert 'renewed for another 12 months' in mail.outbox[-1].body
    # The new term's month 2.
    assert service.charge_months_due(ends_on + DAY + relativedelta(months=1)) == {'paid': 1}


def test_auto_renew_off_charges_nothing_after_the_term_then_basic(loaded, monkeypatch):
    user = monthly()
    service.set_auto_renew(user, False, expected_version=record(user).version)
    assert service.due_reminders(record(user).ends_on - timedelta(days=10)) == 0
    pay_months(user)  # still paying until the commitment ends
    ends_on = record(user).ends_on
    calls = []
    fake_charge(monkeypatch, lambda *a: calls.append(a))
    assert service.charge_months_due(ends_on + DAY) == {}
    assert calls == [] and orders(user) == 12
    assert service.drop_unrenewed(ends_on) == 0
    assert service.drop_unrenewed(ends_on + DAY) == 1
    found = record(user)
    assert (found.tier, found.billing_period, found.ends_on) == ('basic', '', None)
    assert found.changes.order_by('-version').first().reason == 'renewal_unpaid'


def test_the_renewal_month_declined_is_retried_then_basic_on_day_7(loaded):
    user = monthly()
    pay_months(user)
    renews_on = record(user).ends_on + DAY
    fake.decline_next_charge('0190')
    assert service.charge_months_due(renews_on) == {'declined': 1}
    assert service.drop_unrenewed(renews_on + DAY) == 0  # retried: not dropped yet
    assert record(user).status == 'past_due'
    for day in (renews_on + DAY, renews_on + 3 * DAY):
        fake.decline_next_charge('0190')
        assert service.charge_months_due(day) == {'declined': 1}
    fake.decline_next_charge('0190')
    assert service.charge_months_due(renews_on + 7 * DAY) == {'dropped': 1}
    assert record(user).tier == 'basic'


# --- Upgrade screen ----------------------------------------------------------------------------

def test_the_screen_shows_the_next_charge_and_card_and_turns_auto_renew_off(loaded):
    user = monthly()
    client = browser(user)
    page = html(client.get('/en/upgrade/'))
    last4 = SavedCard.objects.get(user=user).last4
    found = record(user)
    assert 'data-next-charge' in page and shown(month(user, 1)) in page
    assert f'ending in {last4}' in page
    assert f'renews automatically on <strong>{shown(found.ends_on + DAY)}</strong>' in page
    assert 'data-auto-renew="off"' in page and 'data-renew-now' not in page
    response = client.get('/en/upgrade/advanced/')
    assert response.status_code == 302 and response.url == '/en/upgrade/'
    response = client.post('/en/upgrade/auto-renew/', {'auto_renew': 'off',
                                                        'version': found.version})
    assert response.status_code == 302 and record(user).auto_renew is False
    page = html(client.get('/en/upgrade/'))
    assert 'you keep paying monthly until your commitment ends' in page
    assert 'data-auto-renew="on"' in page


def test_the_screen_says_a_failed_month_is_retried(loaded):
    user = monthly()
    due = month(user, 1)
    fake.decline_next_charge('0190')
    assert service.charge_months_due(due) == {'declined': 1}
    info = service.renewal_info(user, due)
    assert (info.monthly, info.past_due, info.next_charge_on) == (True, True, due + DAY)


# --- Upfront and manual records ----------------------------------------------------------------

def test_upfront_and_manual_records_are_ignored(loaded, monkeypatch):
    upfront = annual(days_left=200)
    manual = spanish('ma@example.com')
    service.assign_manual(manual, resolver.subscription_by_id(MANUAL), expected_version=1,
                          actor=service.admin_actor(ADA), ends_on=today())
    before = [record(user).business_values() for user in (upfront, manual)]
    calls = []
    fake_charge(monkeypatch, lambda *a: calls.append(a))
    for months in range(1, 13):
        assert service.charge_months_due(today() + relativedelta(months=months)) == {}
    assert [record(user).business_values() for user in (upfront, manual)] == before
    assert calls == [] and orders(upfront) == 1 and orders(manual) == 0


# --- A failure for one record never stops the run ----------------------------------------------

def test_a_failing_record_is_logged_and_the_others_charged(loaded, monkeypatch, caplog):
    first = monthly()
    second = monthly(spanish('carla@example.com'))
    failing = record(first).pk
    original = service._charge_month

    def charge(found, order, day):
        if found.record_id == failing:
            raise RuntimeError('boom')
        return original(found, order, day)
    monkeypatch.setattr(service, '_charge_month', charge)
    assert service.charge_months_due(month(first, 1)) == {'paid': 1}
    assert f'record {failing} (RuntimeError)' in caplog.text
    assert record(second).monthly_charges.get().outcome == 'paid'


# --- Review fixes ------------------------------------------------------------------------------

def test_a_row_no_longer_sold_mid_term_says_so_and_drops_on_day_7(loaded):
    from integration.tests.snapshot_rows import pull
    from subscriptions.tests.test_renewals import VOUCHERS, section
    user = monthly()
    due = month(user, 1)
    assert pull(loaded, 2, section(advanced_active=False), vouchers=VOUCHERS) == (
        'applied', 2)
    info = service.renewal_info(user)
    assert (info.sold, info.drop_on) == (False, due + 7 * DAY)
    page = html(browser(user).get('/en/upgrade/'))
    assert 'its monthly payments cannot be charged' in page
    assert f'your level ends on <strong>{shown(due + 7 * DAY)}</strong> unless you contact us' \
        in page
    assert 'data-auto-renew' not in page
    for day in (due, due + DAY, due + 3 * DAY):
        assert service.charge_months_due(day) == {'declined': 1}
    assert orders(user) == 1 and charge_of(user, due).order_id is None
    info = service.renewal_info(user, due + 3 * DAY)
    assert (info.past_due, info.pay_link, info.drop_on) == (True, False, due + 7 * DAY)
    assert send_failed(charge_of(user, due)) is True  # before the drop
    body = mail.outbox[-1].body
    assert 'is no longer offered' in body and f'until {shown(due + 7 * DAY)}' in body
    assert 'could not charge your saved card' not in body
    assert service.charge_months_due(due + 7 * DAY) == {'dropped': 1}
    assert record(user).tier == 'basic'
    charge = charge_of(user, due)
    assert send_failed(charge) is True
    message = mail.outbox[-1]
    assert message.subject == 'Your MyTreatPlan Advanced subscription has ended'
    assert 'is no longer offered' in message.body and 'Basic from today' in message.body
    assert 'could not charge your saved card' not in message.body
    with translation.override('es'):
        from django.utils.translation import gettext
        assert gettext('Your last monthly payment could not be charged.') != (
            'Your last monthly payment could not be charged.')


def test_the_past_due_line_mentions_the_link_only_with_an_order_to_pay(loaded):
    user = monthly()
    due = month(user, 1)
    fake.decline_next_charge('0190')
    assert service.charge_months_due(due) == {'declined': 1}
    assert service.renewal_info(user, due).pay_link is True
    Order.objects.filter(pk=latest_order(user).pk).update(state='expired')
    assert service.renewal_info(user, due).pay_link is False


def test_the_block_and_auto_renew_show_while_the_renewal_month_is_retried(loaded):
    user = monthly()
    ends_on = today() - 3 * DAY
    started_on = ends_on + DAY - relativedelta(months=12)
    service.change(user, expected_version=record(user).version,
                   actor=service.SYSTEM_ACTOR, reason='test', started_on=started_on,
                   ends_on=ends_on)
    pay_months(user)
    renews_on = ends_on + DAY
    for day in (renews_on, renews_on + DAY):
        fake.decline_next_charge('0190')
        assert service.charge_months_due(day) == {'declined': 1}
    assert service.drop_unrenewed(today()) == 0
    info = service.renewal_info(user)  # today: ends_on + 3
    assert (info.monthly, info.past_due, info.next_charge_on) == (True, True,
                                                                  renews_on + 3 * DAY)
    client = browser(user)
    page = html(client.get('/en/upgrade/'))
    assert 'data-renewal' in page and 'data-auto-renew="off"' in page
    assert shown(renews_on + 3 * DAY) in page
    client.post('/en/upgrade/auto-renew/', {'auto_renew': 'off',
                                            'version': record(user).version})
    assert record(user).auto_renew is False


def test_a_renewal_month_skipped_today_is_not_dropped_and_charged_next_run(
        loaded, monkeypatch):
    user = monthly()
    pay_months(user)
    renews_on = record(user).ends_on + DAY
    other = latest_order(user)
    original = service.open_purchase
    monkeypatch.setattr(service, 'open_purchase', lambda doctor: other)
    assert service.charge_months_due(renews_on) == {}
    assert service.drop_unrenewed(renews_on) == 0
    assert record(user).tier == 'advanced'
    monkeypatch.setattr(service, 'open_purchase', original)
    assert service.charge_months_due(renews_on + DAY) == {'paid': 1}
    assert record(user).started_on == renews_on
