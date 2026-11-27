"""Annual renewals (epic-subscriptions 8.6): one test (or more) per row of the plan's matrix,
rerun idempotence, and the included cases of the new term. The daily job
(`renew_subscriptions`: `service.due_reminders`, `renew_due`, `drop_unrenewed`) charges the
saved card outside any transaction, so these tests commit for real. The emails are queued
after commit and sent here by hand (`subscriptions.tasks`). On the fake gateway and on
Redsys (never called: its notification and REST answer are built and signed here with
Redsys's public test key); the IE doctor is billed by a Rest-of-EU rule with Revolut only
(no card). The invoice PDF is mocked."""
import io
from datetime import timedelta
from decimal import Decimal

import pytest
from dateutil.relativedelta import relativedelta
from django.core import mail
from django.core.management import call_command
from django_tasks_db.models import DBTaskResult

from accounts.models import Profile
from catalogue import pricing, resolver
from integration.tests.snapshot_rows import (ADA, ALL, EU, eu_subscription_rule,
                                             full_subscription, pull, voucher)
from invoices.models import Invoice
from payments import fake, webhooks
from payments.models import Order, SavedCard
from payments.tests.conftest import country_map, fake_on  # noqa: F401
from payments.tests.test_redsys import (SITE, body_of, notification, post,  # noqa: F401
                                        redsys_on, sentry)
from payments.tests.test_repeat import CARD, ChargeAnswer, fake_charge
from payments.tests.test_webhooks import attempt, last_log
from subscriptions import included, service, tasks
from subscriptions.tests.helpers import ADVANCED, MANUAL, catalogue
from subscriptions.tests.test_purchase import (browser, buy, fake_pdf, html,  # noqa: F401
                                               irish, order_of, pay_on_fake, spanish)

# A renewal charge runs outside any transaction: real commits.
pytestmark = [pytest.mark.django_db(transaction=True),
              pytest.mark.usefixtures('clear_cache', 'portal_urls')]

SUBSCRIPTION_VOUCHER = 'f0000002-0000-4000-8000-000000000002'
VOUCHER_CODE = 'SUBSC23456'
EU_ADVANCED = 'e0000020-0000-4000-8000-000000000020'
INCLUDED = 12
DAY = timedelta(days=1)


def section(advanced_active=True):
    """The subscriptions tests' catalogue, Rest of EU's Advanced (Revolut only) and Advanced
    of Iberia including 12 TPS branded cases."""
    data = catalogue(advanced_active=advanced_active)
    data['billing_rules'].append(eu_subscription_rule())
    data['subscriptions'].append(full_subscription(
        EU_ADVANCED, 'advanced', EU, 'ADV-EU', 'Advanced', ALL, {'tps_branded': '6.00'},
        price={'currency': 'EUR', 'monthly': '60.00', 'upfront': '600.00'}))
    for row in data['subscriptions']:
        if row['id'] == ADVANCED:
            row['included_cases'] = {'tps_branded': INCLUDED}
    return data


VOUCHERS = [voucher(SUBSCRIPTION_VOUCHER, VOUCHER_CODE, type_='subscription',
                    amount='100.00')]


@pytest.fixture
def loaded(admin, fake_on, redsys_on):
    """The catalogue with a subscription voucher of 100.00 EUR; Redsys and the fake gateway
    on (a Spanish doctor's card is saved; paid here on the fake gateway)."""
    assert pull(admin, 1, section(), vouchers=VOUCHERS) == ('applied', 1)
    return admin


def today():
    return resolver.today()


def record(user):
    return service.record_of(user)


def end_on(user, ends_on, **fields):
    """Move the record's term to end on `ends_on` (a test change by the system)."""
    found = record(user)
    return service.change(user, expected_version=found.version, actor=service.SYSTEM_ACTOR,
                          reason='test', started_on=ends_on - relativedelta(months=12) + DAY,
                          ends_on=ends_on, **fields)


def annual(user=None, days_left=0):
    """A doctor who bought Advanced upfront (paid on the fake gateway: the card saved for a
    Spanish doctor), the term ending in `days_left` days."""
    user = user or spanish()
    buy(user)
    order = pay_on_fake(order_of(user), user)
    assert order.state == 'paid' and record(user).tier == 'advanced'
    end_on(user, today() + timedelta(days=days_left))
    return user


def queued(name):
    return [row.args_kwargs['args'] for row in DBTaskResult.objects.order_by('enqueued_at')
            if row.task_path.endswith(f'.{name}')]


def send_all(name):
    """Run each queued task `name` by hand; returns their results."""
    task = getattr(tasks, name)
    return [task.call(*args) for args in queued(name)]


def renewal_order(user):
    return Order.objects.filter(user=user).order_by('-created_at').first()


def orders(user):
    return Order.objects.filter(user=user).count()


def run_job():
    out = io.StringIO()
    call_command('renew_subscriptions', stdout=out)
    return out.getvalue()


def renewed_term(ends_on):
    started_on = ends_on + DAY
    return started_on, started_on + relativedelta(months=12) - DAY


# --- Reminder ------------------------------------------------------------------------------

def test_one_reminder_30_days_before_with_date_price_card_and_how_to_turn_off(loaded):
    user = annual(days_left=30)
    ends_on = record(user).ends_on
    assert service.due_reminders(today() - DAY) == 0  # 31 days before: not yet
    assert service.due_reminders(today()) == 1
    assert send_all('send_renewal_reminder') == [True]
    message = mail.outbox[-1]
    assert message.to == [user.email] and 'Advanced' in message.subject
    body = message.body
    last4 = SavedCard.objects.get(user=user).last4
    assert f'ending in {last4}' in body and '600.00 EUR for a year, paid upfront' in body
    assert '+ VAT where applicable' in body and 'Turn off' in body
    assert '/en/upgrade/' in body
    assert (ends_on + DAY).strftime('%Y') in body
    # Rerun (the job, and the task): none more.
    assert service.due_reminders(today()) == 0
    assert tasks.send_renewal_reminder.call(str(record(user).pk), ends_on.isoformat()) is False
    assert len(mail.outbox) == 1


def test_the_reminder_without_a_card_says_payment_link_in_the_doctors_language(loaded):
    user = irish()
    Profile.objects.create(user=user, language='es')
    annual(user, days_left=10)
    assert service.due_reminders(today()) == 1
    assert send_all('send_renewal_reminder') == [True]
    body = mail.outbox[-1].body
    assert 'enlace de pago' in body and '/es/upgrade/' in body
    assert 'Su suscripción MyTreatPlan Advanced se renueva el' in mail.outbox[-1].subject


# --- Renewed (acceptance) ------------------------------------------------------------------

def bought_on_redsys(user):
    buy(user)
    order, payment = attempt(user, 'redsys', order=order_of(user))
    data = notification(payment)
    data.update(CARD)
    assert post(body_of(data)).status_code == 200
    webhooks.process(last_log().pk)
    assert Order.objects.get(pk=order.pk).state == 'paid'
    return order


def test_the_job_twice_charges_the_redsys_card_once_invoices_and_runs_to_next_year(
        loaded, monkeypatch):
    """Acceptance 1: the job runs twice on `ends_on`: one charge, invoiced, next year."""
    user = spanish()
    first = bought_on_redsys(user)
    ends_on = today()
    end_on(user, ends_on)
    answer = ChargeAnswer()
    monkeypatch.setattr('accounts.http.post_json', answer)
    out = run_job()
    assert 'renewals: paid 1' in out and '0 dropped to Basic' in out
    assert 'renewals: none' in run_job()
    assert len(answer.calls) == 1 and orders(user) == 2
    order = renewal_order(user)
    assert order.pk != first.pk and order.state == 'paid' and order.gateway == 'redsys'
    assert (order.actor_type, order.voucher_id, order.total) == (
        'system', None, Decimal('726.00'))
    assert str(order.lines.get().subscription_id) == ADVANCED
    assert order.lines.get().period == 'upfront'
    found = record(user)
    assert (found.started_on, found.ends_on) == renewed_term(ends_on)
    assert (found.tier, found.auto_renew, found.status) == ('advanced', True, 'active')
    change = found.changes.order_by('-version').first()
    assert (change.reason, change.order_id, change.actor_type) == ('renewal', order.pk,
                                                                   'system')
    invoice = Invoice.objects.get(order=order)
    assert invoice.total == Decimal('726.00')
    assert send_all('send_renewal_receipt') == [True]
    message = mail.outbox[-1]
    assert 'renewed' in message.subject and order.reference in message.body
    assert message.attachments == [(f'{invoice.number}.pdf', b'%PDF-fake',
                                    'application/pdf')]
    assert send_all('send_renewal_receipt') == [False]
    assert not queued('send_renewal_link') and not queued('send_purchase_email')[1:]
    # The next day nothing drops; the screen shows next year's renewal date.
    assert service.drop_unrenewed(ends_on + DAY) == 0
    assert record(user).tier == 'advanced'


def test_paid_twice_changes_the_record_once(loaded):
    user = annual()
    service.renew_due(today())
    order = renewal_order(user)
    version = record(user).version
    assert service.activate(order) is None
    assert record(user).version == version
    assert record(user).changes.filter(reason='renewal').count() == 1


# --- Auto-renew off ------------------------------------------------------------------------

def test_the_screen_shows_the_renewal_and_turns_auto_renew_off_and_on(loaded):
    user = annual(days_left=40)
    client = browser(user)
    page = html(client.get('/en/upgrade/'))
    last4 = SavedCard.objects.get(user=user).last4
    assert 'data-renewal' in page and 'Renews automatically on' in page
    assert f'ending in {last4}' in page and 'data-auto-renew="off"' in page
    assert 'data-renew-now' not in page  # 40 days left
    version = record(user).version
    response = client.post('/en/upgrade/auto-renew/', {'auto_renew': 'off',
                                                        'version': version})
    assert response.status_code == 302 and response.url == '/en/upgrade/'
    assert record(user).auto_renew is False
    change = record(user).changes.order_by('-version').first()
    assert (change.reason, change.actor_type, change.actor_uuid) == ('auto_renew', 'doctor',
                                                                     user.uid)
    page = html(client.get('/en/upgrade/'))
    assert 'Automatic renewal is off' in page and 'data-auto-renew="on"' in page
    # A stale version changes nothing.
    client.post('/en/upgrade/auto-renew/', {'auto_renew': 'on', 'version': version})
    assert record(user).auto_renew is False
    client.post('/en/upgrade/auto-renew/', {'auto_renew': 'on',
                                            'version': record(user).version})
    assert record(user).auto_renew is True


def test_a_basic_doctor_cannot_turn_auto_renew_on(loaded):
    user = spanish()
    browser(user).post('/en/upgrade/auto-renew/', {'auto_renew': 'on', 'version': 1})
    assert record(user).auto_renew is False and record(user).version == 1
    with pytest.raises(service.ChangeRefused):
        service.set_auto_renew(user, True, expected_version=1)


def test_auto_renew_off_no_reminder_no_order_no_charge_then_basic(loaded, monkeypatch):
    user = annual(days_left=30)
    service.set_auto_renew(user, False, expected_version=record(user).version)
    assert service.due_reminders(today()) == 0
    end_on(user, today())
    calls = []
    fake_charge(monkeypatch, lambda *a: calls.append(a))
    assert service.renew_due(today()) == {}
    assert orders(user) == 1 and calls == []
    assert service.drop_unrenewed(today()) == 0  # the last day: still Advanced
    assert service.drop_unrenewed(today() + DAY) == 1
    found = record(user)
    assert (found.tier, found.subscription_id, found.billing_period, found.ends_on) == (
        'basic', None, '', None)
    assert found.changes.order_by('-version').first().reason == 'renewal_unpaid'
    assert service.drop_unrenewed(today() + DAY) == 0
    assert not mail.outbox


# --- Declined, then unpaid at the end, then paid late --------------------------------------

def test_a_declined_card_emails_the_link_then_basic_then_a_late_payment_restores(loaded):
    user = annual()
    ends_on = today()
    fake.decline_next_charge('0190')
    assert service.renew_due(ends_on) == {'declined': 1}
    order = renewal_order(user)
    assert order.state == 'pending_payment' and record(user).tier == 'advanced'
    assert service.renew_due(ends_on) == {}  # rerun: no second order or charge
    assert orders(user) == 2 and order.payments.count() == 1
    assert send_all('send_renewal_link') == [True]
    message = mail.outbox[-1]
    assert f'{SITE}/en/checkout/{order.pk}/' in message.body  # absolute: from an email
    assert 'could not charge your saved card' in message.body
    assert service.effective(user, ends_on).tier == 'advanced'
    # Unpaid at the end: Basic the next day.
    assert service.drop_unrenewed(ends_on + DAY) == 1
    assert record(user).tier == 'basic'
    assert service.effective(user, ends_on + DAY).tier == 'basic'
    # Paid late (the order still open): back on Advanced for the full new term.
    order = pay_on_fake(order, user)
    assert order.state == 'paid'
    found = record(user)
    assert (found.tier, str(found.subscription_id), found.billing_period) == (
        'advanced', ADVANCED, 'upfront')
    assert (found.started_on, found.ends_on) == renewed_term(ends_on)
    assert (found.auto_renew, found.status) == (True, 'active')
    assert found.changes.order_by('-version').first().reason == 'renewal'
    assert send_all('send_renewal_receipt') == [True]
    assert send_all('send_renewal_link') == [False]  # sent once
    assert service.drop_unrenewed(ends_on + DAY) == 0


def test_no_card_for_an_irish_doctor_makes_an_order_without_a_charge_and_a_link(loaded):
    user = annual(irish())
    assert not SavedCard.objects.filter(user=user).exists()
    assert service.renew_due(today()) == {'no_card': 1}
    order = renewal_order(user)
    assert order.state == 'checkout' and not order.payments.exists()
    assert str(order.lines.get().subscription_id) == EU_ADVANCED
    assert send_all('send_renewal_link') == [True]
    body = mail.outbox[-1].body
    assert f'/en/checkout/{order.pk}/' in body and 'no saved card' in body
    assert service.renew_due(today()) == {} and orders(user) == 2
    page = html(browser(user).get(f'/en/checkout/{order.pk}/'))
    assert order.reference in page


# --- Renew now -----------------------------------------------------------------------------

def test_renew_now_with_a_voucher_adds_the_next_term_and_the_job_charges_nothing(
        loaded, monkeypatch):
    user = annual(days_left=20)
    ends_on = record(user).ends_on
    client = browser(user)
    page = html(client.get('/en/upgrade/'))
    assert 'data-renew-now' in page and 'href="/en/upgrade/advanced/"' in page
    page = html(client.get('/en/upgrade/advanced/'))
    assert 'Renew Advanced' in page and 'data-period="upfront"' in page
    assert 'data-period="monthly"' not in page and 'name="voucher"' in page
    response = client.post('/en/upgrade/advanced/', {'period': 'monthly',
                                                     'voucher': VOUCHER_CODE})
    order = renewal_order(user)
    assert response.status_code == 302 and response.url == f'/en/checkout/{order.pk}/'
    assert (order.actor_type, order.voucher_taken, order.lines.get().period) == (
        'doctor', Decimal('100.00'), 'upfront')
    order = pay_on_fake(order, user)
    found = record(user)
    assert (found.started_on, found.ends_on) == renewed_term(ends_on)
    change = found.changes.order_by('-version').first()
    assert (change.reason, change.order_id, change.actor_type) == ('renewal', order.pk,
                                                                   'doctor')
    # The old term still runs: Advanced today; on the old end date the job does nothing.
    assert service.effective(user).tier == 'advanced'
    calls = []
    fake_charge(monkeypatch, lambda *a: calls.append(a))
    assert service.renew_due(ends_on) == {} and service.drop_unrenewed(ends_on + DAY) == 0
    assert orders(user) == 2 and calls == []
    assert send_all('send_renewal_receipt') == [True]


def test_renew_now_is_not_offered_before_the_last_30_days_and_a_forged_post_is_refused(
        loaded):
    user = annual(days_left=31)
    client = browser(user)
    assert 'data-renew-now' not in html(client.get('/en/upgrade/'))
    for response in (client.get('/en/upgrade/advanced/'),
                     client.post('/en/upgrade/advanced/', {'period': 'upfront'})):
        assert response.status_code == 302 and response.url == '/en/upgrade/'
    with pytest.raises(pricing.OrderRefused) as refused:
        service.renew_now(user)
    assert refused.value.reason == pricing.SUBSCRIPTION_NOT_OFFERED
    assert orders(user) == 1


def test_a_renew_now_order_left_unpaid_is_replaced_by_the_job(loaded):
    user = annual(days_left=5)
    abandoned = service.renew_now(user)
    Order.objects.filter(pk=abandoned.pk).update(state='expired')
    end_on(user, today())
    assert service.renew_due(today()) == {'paid': 1}
    order = renewal_order(user)
    assert order.pk != abandoned.pk and order.actor_type == 'system'
    assert record(user).ends_on == renewed_term(today())[1]


# --- The held subscription is no longer sold ------------------------------------------------

def test_a_deactivated_subscription_is_not_renewed_the_doctor_is_told_and_basic_after(
        loaded, sentry):
    user = annual()
    found = record(user)
    assert pull(loaded, 2, section(advanced_active=False), vouchers=VOUCHERS) == (
        'applied', 2)
    assert service.renew_due(today()) == {'not_sold': 1}
    assert orders(user) == 1
    assert sentry == [f'subscription renewal not sold (subscription_not_offered): '
                      f'record {found.pk}']
    assert service.renew_due(today()) == {}
    assert send_all('send_renewal_link') == [True]
    body = mail.outbox[-1].body
    assert 'cannot be renewed' in body and '/en/checkout/' not in body
    assert service.drop_unrenewed(today() + DAY) == 1
    assert record(user).tier == 'basic'


# --- Manual and monthly records ------------------------------------------------------------

def test_manual_and_monthly_records_are_ignored(loaded, monkeypatch):
    monthly = spanish('mo@example.com')
    service.change(monthly, expected_version=1, actor=service.SYSTEM_ACTOR, reason='test',
                   tier='advanced', subscription_id=ADVANCED, billing_period='monthly',
                   auto_renew=True, ends_on=today())
    manual = spanish('ma@example.com')
    service.assign_manual(manual, resolver.subscription_by_id(MANUAL), expected_version=1,
                          actor=service.admin_actor(ADA), ends_on=today())
    before = [record(user).business_values() for user in (monthly, manual)]
    calls = []
    fake_charge(monkeypatch, lambda *a: calls.append(a))
    assert service.due_reminders(today() - timedelta(days=5)) == 0
    assert service.renew_due(today()) == {}
    assert service.drop_unrenewed(today() + DAY) == 0
    assert [record(user).business_values() for user in (monthly, manual)] == before
    assert not Order.objects.exists() and calls == []


# --- Unknown outcome -----------------------------------------------------------------------

def test_an_unknown_outcome_sends_nothing_and_drops_nothing(loaded, sentry, monkeypatch):
    import socket
    user = annual()

    def timeout(card, payment):
        raise socket.timeout('timed out')
    fake_charge(monkeypatch, timeout)
    assert service.renew_due(today()) == {'unknown': 1}
    order = renewal_order(user)
    assert sentry == [f'payment repeat charge outcome unknown: order {order.pk}']
    assert service.renew_due(today()) == {}
    assert not queued('send_renewal_link')
    assert service.drop_unrenewed(today() + DAY) == 0  # staff decide
    assert record(user).tier == 'advanced'


# --- A failure for one record never stops the run -----------------------------------------

def test_a_failing_record_is_logged_and_the_others_renewed(loaded, monkeypatch, caplog):
    first = annual()
    second = annual(spanish('carla@example.com'))
    original = service._charge
    failing = record(first).pk

    def charge(renewal, order):
        if renewal.record_id == failing:
            raise RuntimeError('boom')
        return original(renewal, order)
    monkeypatch.setattr(service, '_charge', charge)
    assert service.renew_due(today()) == {'paid': 1}
    assert f'record {failing} (RuntimeError)' in caplog.text
    assert record(second).ends_on == renewed_term(today())[1]
    assert record(first).ends_on == today()


# --- Included cases of the new term (8.13) ------------------------------------------------

def test_the_new_terms_grant_counts_from_its_first_day_and_the_old_one_stops(loaded):
    user = annual()
    first = record(user).changes.get(reason='purchase').order_id
    ends_on = today()
    assert service.renew_due(ends_on) == {'paid': 1}
    order = renewal_order(user)
    assert included.has_term_grant(order.pk) and included.has_term_grant(first)
    assert service.counting_purchase(user, ends_on) == first
    assert service.included_left(user, ends_on) == (INCLUDED, INCLUDED)
    assert service.counting_purchase(user, ends_on + DAY) == order.pk
    assert service.included_left(user, ends_on + DAY) == (INCLUDED, INCLUDED)
    assert service.counting_purchase(user, renewed_term(ends_on)[1] + DAY) == order.pk
    assert service.included_left(user, renewed_term(ends_on)[1] + DAY) is None
