"""Buying Advanced or Premium (epic-subscriptions entry 5): one test (or more) per row of the
plan's matrix. The purchase page creates the order (`payments.service.
create_subscription_order`) and sends the doctor to the checkout; the `paid` receiver
`subscriptions_activate` (`service.activate`) moves the record; the return page leads to
the welcome page; the confirmation email is queued after commit and sent once by
`subscriptions.tasks.send_purchase_email` (run by hand here). On the fake gateway and on
Redsys (never called: its notification is built and signed here with Redsys's public test
key); the IE doctor is billed by a Rest-of-EU `subscription` rule with Revolut only. The
invoice PDF is mocked."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from dateutil.relativedelta import relativedelta
from django.core import mail
from django.test import Client
from django_tasks_db.models import DBTaskResult

from accounts import fiscal
from accounts.models import Profile
from catalogue import resolver
from integration.tests.snapshot_rows import (ADA, EU, eu_subscription_rule,
                                             full_subscription, ALL, pull, voucher)
from invoices import pdf
from invoices.models import Invoice
from payments import fake, gateways, service as payments, webhooks
from payments.models import Order, SavedCard
from payments.tests.conftest import COMPANY, country_map, fake_on  # noqa: F401
from payments.tests.test_redsys import notification, post, redsys_on, sentry  # noqa: F401
from payments.tests.test_repeat import CARD
from payments.tests.test_webhooks import attempt, last_log
from subscriptions import service
from subscriptions.tasks import send_purchase_email
from subscriptions.tests.helpers import (ADVANCED, MANUAL, PREMIUM, catalogue, doctor,
                                         priced)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

SUBSCRIPTION_VOUCHER = 'f0000002-0000-4000-8000-000000000002'
BIG_VOUCHER = 'f0000004-0000-4000-8000-000000000004'
TRY_ME = 'f0000001-0000-4000-8000-000000000001'
USD_VOUCHER = 'f0000005-0000-4000-8000-000000000005'
CODES = {SUBSCRIPTION_VOUCHER: 'SUBSC23456', BIG_VOUCHER: 'BIGSU23456',
         TRY_ME: 'TRYME23456', USD_VOUCHER: 'USDSU23456'}
EU_ADVANCED = 'e0000020-0000-4000-8000-000000000020'
ADVANCED_URL, PREMIUM_URL = '/en/upgrade/advanced/', '/en/upgrade/premium/'


@pytest.fixture(autouse=True)
def fake_pdf(monkeypatch):
    monkeypatch.setattr(pdf, 'render', lambda invoice, user, build=pdf.context: b'%PDF-fake')


@pytest.fixture
def loaded(admin, fake_on):
    """The catalogue (Iberia's Advanced and Premium; Rest of EU's Advanced billed by a
    Revolut-only rule) and the vouchers: a subscription voucher of 100.00 EUR, one of
    1000.00 EUR, a try-me one and a subscription voucher in USD."""
    section = catalogue()
    section['billing_rules'].append(eu_subscription_rule())
    section['subscriptions'].append(full_subscription(
        EU_ADVANCED, 'advanced', EU, 'ADV-EU', 'Advanced', ALL, {'tps_branded': '6.00'},
        price={'currency': 'EUR', 'monthly': '60.00', 'upfront': '600.00'}))
    assert pull(admin, 1, section, vouchers=[
        voucher(SUBSCRIPTION_VOUCHER, CODES[SUBSCRIPTION_VOUCHER], type_='subscription',
                amount='100.00'),
        voucher(BIG_VOUCHER, CODES[BIG_VOUCHER], type_='subscription', amount='1000.00'),
        voucher(TRY_ME, CODES[TRY_ME]),
        voucher(USD_VOUCHER, CODES[USD_VOUCHER], type_='subscription', currency='USD'),
    ]) == ('applied', 1)
    return admin


def spanish(email='bea@example.com'):
    user = doctor(email)
    fiscal.create(user, dict(COMPANY))
    return user


def irish(email='ian@example.com'):
    user = doctor(email, country='IE')
    fiscal.create(user, dict(COMPANY, country='IE', tax_id='IE6388047V',
                             legal_name='Dublin Smiles Ltd'))
    return user


def browser(user):
    client = Client()
    client.force_login(user)
    return client


def html(response):
    assert response.status_code == 200
    return response.content.decode()


def buy(user, period='upfront', code='', url=ADVANCED_URL):
    return browser(user).post(url, {'period': period, 'voucher': code})


def order_of(user):
    return Order.objects.get(user=user)


def pay_on_fake(order, user):
    """The doctor pays on the fake gateway's page: its signed webhook, then the task."""
    order, payment = attempt(user, 'fake', order=order)
    fake.pay(payment)
    webhooks.process(last_log().pk)
    return Order.objects.get(pk=order.pk)


@pytest.fixture
def capture(django_capture_on_commit_callbacks):
    def run(fn):
        with django_capture_on_commit_callbacks(execute=True):
            return fn()
    return run


def queued():
    return [row.args_kwargs['args'] for row in DBTaskResult.objects.all()
            if row.task_path.endswith('send_purchase_email')]


def send(order):
    return send_purchase_email.call(str(order.pk))


def record(user):
    return service.record_of(user)


def term():
    today = resolver.today()
    return today, today + relativedelta(months=12) - timedelta(days=1)


# --- Annual, card --------------------------------------------------------------------------

def test_an_annual_purchase_saves_the_card_and_activates_advanced(loaded, redsys_on, capture):
    user = spanish()
    response = buy(user)
    order = order_of(user)
    assert response.status_code == 302 and response.url == f'/en/checkout/{order.pk}/'
    assert order.kind == Order.SUBSCRIPTION and order.save_card and order.state == 'checkout'
    line = order.lines.get()
    assert (str(line.subscription_id), line.period, line.net) == (
        ADVANCED, 'upfront', Decimal('600.00'))
    assert (order.vat_total, order.total) == (Decimal('126.00'), Decimal('726.00'))
    order = capture(lambda: pay_on_fake(order, user))
    assert order.state == 'paid'
    found = record(user)
    started_on, ends_on = term()
    assert (found.tier, str(found.subscription_id), found.billing_period, found.auto_renew,
            found.status, found.manual) == ('advanced', ADVANCED, 'upfront', True, 'active',
                                            False)
    assert (found.started_on, found.ends_on) == (started_on, ends_on)
    change = found.changes.order_by('-version').first()
    assert (change.reason, change.order_id, change.actor_type, change.actor_uuid) == (
        'purchase', order.pk, 'doctor', user.uid)
    assert SavedCard.objects.filter(user=user, gateway='fake').exists()
    page = html(browser(user).get(f'/en/checkout/{order.pk}/return/'))
    assert 'href="/en/welcome/"' in page and 'data-continue' in page
    assert 'Back to home' not in page
    assert queued() == [[str(order.pk)]]
    assert send(order) is True
    message = mail.outbox[0]
    assert len(mail.outbox) == 1 and message.to == [user.email]
    assert 'Advanced' in message.subject
    body = message.body
    assert 'a year, paid upfront' in body and '726.00 EUR' in body
    assert order.reference in body and '/en/support/' in body
    assert 'renewals of this subscription will be charged to it' in body
    invoice = Invoice.objects.get(order=order)
    assert message.attachments == [(f'{invoice.number}.pdf', b'%PDF-fake', 'application/pdf')]


def test_the_record_is_advanced_and_the_doctor_sees_advanced_prices(loaded, capture):
    """Acceptance 1: a partial subscription voucher, then Advanced prices."""
    user = spanish()
    before = priced(user).total
    buy(user, code=CODES[SUBSCRIPTION_VOUCHER])
    capture(lambda: pay_on_fake(order_of(user), user))
    assert service.effective(user).tier == 'advanced'
    assert priced(user).total < before  # Advanced's 6 % off TPS branded


def test_the_upgrade_screen_marks_the_bought_level(loaded, capture):
    user = spanish()
    buy(user)
    capture(lambda: pay_on_fake(order_of(user), user))
    page = html(browser(user).get('/en/upgrade/'))
    assert 'Your current level is <strong class="text-ink">Advanced</strong>' in page


# --- Annual, Revolut -----------------------------------------------------------------------

def test_an_irish_doctor_buys_upfront_without_saving_the_card(loaded, redsys_on, capture):
    user = irish()
    page = html(browser(user).get(ADVANCED_URL))
    assert 'data-period="upfront"' in page and 'data-period="monthly"' not in page
    buy(user)
    order = order_of(user)
    assert not order.save_card and str(order.lines.get().subscription_id) == EU_ADVANCED
    assert gateways.offered(order) == ['fake']  # Revolut is not set up here
    order = capture(lambda: pay_on_fake(order, user))
    assert record(user).tier == 'advanced' and not SavedCard.objects.filter(user=user).exists()
    assert send(order) is True
    assert 'Your card is not saved: before each renewal we will send you a link to pay ' \
           'it.' in mail.outbox[0].body


# --- The email once ------------------------------------------------------------------------

def test_the_email_is_sent_once(loaded, capture):
    user = spanish()
    buy(user)
    order = capture(lambda: pay_on_fake(order_of(user), user))
    assert send(order) is True
    assert send(order) is False
    # `paid` again (a replay) changes nothing and queues nothing.
    capture(lambda: payments.mark_paid(order, expected_version=order.version,
                                       actor=payments.SYSTEM_ACTOR))
    assert service.activate(order) is None
    assert len(queued()) == 1 and len(mail.outbox) == 1


def test_a_failed_email_is_logged_by_order_uuid_and_not_retried(loaded, capture, caplog,
                                                                 monkeypatch):
    user = spanish()
    buy(user)
    order = capture(lambda: pay_on_fake(order_of(user), user))

    def broken(*args, **kwargs):
        raise OSError('smtp down')
    monkeypatch.setattr('django.core.mail.EmailMessage.send', broken)
    assert send(order) is None
    assert f'order {order.pk} (OSError)' in caplog.text and user.email not in caplog.text
    assert not mail.outbox and len(queued()) == 1


def test_no_email_for_an_order_that_changed_no_record(loaded, sentry):
    user = spanish()
    buy(user)
    order = order_of(user)
    manual(user)
    order = pay_on_fake(order, user)
    assert send(order) is False and not mail.outbox


def test_the_email_is_in_the_doctors_language(loaded, capture):
    user = spanish()
    Profile.objects.create(user=user, language='es')
    buy(user)
    order = capture(lambda: pay_on_fake(order_of(user), user))
    assert send(order) is True
    message = mail.outbox[0]
    assert message.subject == 'Su suscripción MyTreatPlan Advanced está activa'
    assert 'un año, pagado por adelantado' in message.body and '/es/support/' in message.body


# --- Monthly (Redsys) ----------------------------------------------------------------------

def test_a_monthly_purchase_on_redsys_saves_the_card_and_bills_the_first_month(
        loaded, redsys_on, capture):
    """Acceptance 2: monthly on Redsys's test terminal, the first month invoiced."""
    user = spanish()
    page = html(browser(user).get(PREMIUM_URL))
    assert 'data-period="monthly"' in page and '120.00 EUR per month' in page
    assert '+ VAT where applicable' in page
    buy(user, 'monthly', url=PREMIUM_URL)
    order = order_of(user)
    assert order.save_card and order.lines.get().period == 'monthly'
    assert (order.net_total, order.total) == (Decimal('120.00'), Decimal('145.20'))
    assert 'redsys' in gateways.offered(order)
    checkout = html(browser(user).get(f'/en/checkout/{order.pk}/'))
    assert 'data-save-card-consent' in checkout
    order, payment = attempt(user, 'redsys', order=order)
    data = notification(payment)
    data.update(CARD)
    from payments.tests.test_redsys import body_of
    assert post(body_of(data)).status_code == 200
    capture(lambda: webhooks.process(last_log().pk))
    order = Order.objects.get(pk=order.pk)
    assert order.state == 'paid' and order.gateway == 'redsys'
    found = record(user)
    assert (found.tier, str(found.subscription_id), found.billing_period) == (
        'premium', PREMIUM, 'monthly')
    assert (found.started_on, found.ends_on) == term()
    assert SavedCard.objects.filter(user=user, gateway='redsys').exists()
    invoice = Invoice.objects.get(order=order)
    assert invoice.total == Decimal('145.20')
    assert send(order) is True
    body = mail.outbox[0].body
    assert 'monthly, with a 12-month commitment' in body and '145.20 EUR' in body
    assert 'Next charge' in body


# --- A Revolut country ---------------------------------------------------------------------

def test_monthly_is_not_possible_without_a_gateway_that_saves_cards(loaded, redsys_on):
    assert service.monthly_possible(spanish())
    assert not service.monthly_possible(irish())


def test_the_fake_gateway_never_makes_monthly_possible(loaded):
    user = spanish()
    assert not service.monthly_possible(user)  # Redsys is not set up: fake only
    page = html(browser(user).get(ADVANCED_URL))
    assert 'data-period="monthly"' not in page and 'data-period="upfront"' in page


def test_a_forged_monthly_post_is_refused(loaded, redsys_on):
    user = irish()
    page = html(buy(user, 'monthly', code='ABC'))
    assert 'This subscription is not sold for that period.' in page
    assert 'value="ABC"' in page
    assert not Order.objects.exists()


def test_a_voucher_code_posted_with_monthly_is_refused(loaded, redsys_on):
    user = spanish()
    page = html(buy(user, 'monthly', code=CODES[SUBSCRIPTION_VOUCHER]))
    assert 'A voucher applies to the year paid upfront only.' in page
    assert f'value="{CODES[SUBSCRIPTION_VOUCHER]}"' in page and 'data-error' in page
    # Without JavaScript the doctor must still see and clear the code.
    assert 'data-voucher-field>' in page and 'data-voucher-field hidden' not in page
    assert not Order.objects.exists()


def test_the_service_refuses_a_level_the_doctor_cannot_buy_now(loaded):
    from catalogue import pricing
    user = spanish()
    service.change(user, expected_version=1, actor=service.SYSTEM_ACTOR, reason='test',
                   tier='advanced', subscription_id=ADVANCED, billing_period='upfront')
    with pytest.raises(pricing.OrderRefused) as refused:
        service.purchase(user, resolver.subscription_by_id(PREMIUM), 'upfront')
    assert refused.value.reason == pricing.SUBSCRIPTION_NOT_OFFERED
    assert not Order.objects.exists()


def test_the_voucher_field_is_for_the_year_upfront_only(loaded, redsys_on):
    page = html(browser(spanish()).get(ADVANCED_URL))
    assert 'data-voucher-field hidden' not in page
    assert 'data-period="monthly"' in page and 'data-voucher' in page


def test_the_service_refuses_a_forged_period_and_creates_nothing(loaded):
    from catalogue import pricing
    user = spanish()
    advanced = resolver.subscription_by_id(ADVANCED)
    for period in ('monthly', 'weekly', ''):  # monthly: the fake gateway never allows it
        with pytest.raises(pricing.OrderRefused) as refused:
            service.purchase(user, advanced, period)
        assert refused.value.reason == pricing.PERIOD_NOT_OFFERED
    assert not Order.objects.exists()


def test_the_service_refuses_a_voucher_with_monthly(loaded, redsys_on):
    from catalogue import pricing
    user = spanish()
    with pytest.raises(pricing.OrderRefused) as refused:
        service.purchase(user, resolver.subscription_by_id(ADVANCED), 'monthly',
                         CODES[BIG_VOUCHER])
    assert refused.value.reason == pricing.VOUCHER_PERIOD
    assert not Order.objects.exists()


def test_the_service_returns_the_open_order_instead_of_a_second_one(loaded):
    user = spanish()
    assert service.open_purchase(user) is None
    first = service.purchase(user, resolver.subscription_by_id(ADVANCED), 'upfront')
    assert service.open_purchase(user).pk == first.pk
    again = service.purchase(user, resolver.subscription_by_id(PREMIUM), 'upfront')
    assert again.pk == first.pk and Order.objects.count() == 1


@pytest.mark.parametrize('period', ['', 'weekly'])
def test_an_unknown_period_is_refused(loaded, period):
    user = spanish()
    assert 'data-error' in html(buy(user, period))
    assert not Order.objects.exists()


# --- Vouchers ------------------------------------------------------------------------------

def test_a_partial_voucher_reduces_the_order_and_is_consumed_on_payment(loaded, capture):
    from subscriptions.models import VoucherRedemption
    user = spanish()
    buy(user, code=CODES[SUBSCRIPTION_VOUCHER].lower())
    order = order_of(user)
    assert (order.voucher_taken, order.net_total, order.total) == (
        Decimal('100.00'), Decimal('500.00'), Decimal('605.00'))
    assert VoucherRedemption.objects.get(order_id=order.pk).state == 'reserved'
    capture(lambda: pay_on_fake(order, user))
    assert VoucherRedemption.objects.get(order_id=order.pk).state == 'consumed'


def test_a_full_voucher_pays_the_order_at_once_with_a_zero_invoice(loaded, capture):
    """Acceptance 3: no gateway, a 0.00 invoice."""
    user = spanish()
    response = capture(lambda: buy(user, code=CODES[BIG_VOUCHER]))
    order = order_of(user)
    assert response.status_code == 302
    assert response.url == f'/en/checkout/{order.pk}/return/'
    assert (order.state, order.total, order.gateway) == ('paid', Decimal('0.00'), '')
    assert not order.payments.exists()
    assert Invoice.objects.get(order=order).total == Decimal('0.00')
    assert record(user).tier == 'advanced'
    assert queued() == [[str(order.pk)]] and send(order) is True


@pytest.mark.parametrize('code, text', [
    ('NOPE23456X', 'This voucher code is not valid.'),
    (CODES[TRY_ME], 'This voucher is not for this kind of order.'),
    (CODES[USD_VOUCHER], 'This voucher is in another currency.'),
])
def test_a_refused_voucher_shows_the_page_again(loaded, code, text):
    user = spanish()
    page = html(buy(user, code=code))
    assert text in page and f'value="{code}"' in page and 'data-error' in page
    assert not Order.objects.exists()


def test_a_used_voucher_is_refused(loaded, capture):
    first = spanish()
    buy(first, code=CODES[SUBSCRIPTION_VOUCHER])
    user = spanish('carla@example.com')
    page = html(buy(user, code=CODES[SUBSCRIPTION_VOUCHER]))
    assert 'This voucher has already been used.' in page
    assert Order.objects.count() == 1


# --- An open order -------------------------------------------------------------------------

def test_an_open_order_sends_the_doctor_to_its_checkout(loaded):
    user = spanish()
    buy(user)
    order = order_of(user)
    for response in (browser(user).get(PREMIUM_URL), buy(user, url=PREMIUM_URL)):
        assert response.status_code == 302 and response.url == f'/en/checkout/{order.pk}/'
    assert Order.objects.count() == 1


def test_a_cancelled_order_lets_the_doctor_buy_again(loaded):
    user = spanish()
    buy(user)
    order = order_of(user)
    payments.cancel(order, expected_version=order.version, actor=payments.doctor_actor(user))
    assert 'data-purchase-form' in html(browser(user).get(ADVANCED_URL))


# --- A paid tier or a manual record --------------------------------------------------------

def manual(user):
    return service.assign_manual(user, resolver.subscription_by_id(MANUAL),
                                 expected_version=record(user).version,
                                 actor=service.admin_actor(ADA))


@pytest.mark.parametrize('how', ['advanced', 'manual', 'cancelled'])
def test_a_paid_or_manual_record_goes_back_to_the_levels(loaded, how):
    user = spanish()
    if how == 'manual':
        manual(user)
    else:
        service.change(user, expected_version=1, actor=service.SYSTEM_ACTOR, reason='test',
                       tier='advanced', subscription_id=ADVANCED, billing_period='upfront',
                       **({'status': 'cancelled', 'ends_on': date(2099, 1, 1)}
                          if how == 'cancelled' else {}))
    for response in (browser(user).get(PREMIUM_URL), buy(user, url=PREMIUM_URL)):
        assert response.status_code == 302 and response.url == '/en/upgrade/'
    assert not Order.objects.exists()


def test_an_expired_paid_record_may_buy_again(loaded, capture):
    user = spanish()
    service.change(user, expected_version=1, actor=service.SYSTEM_ACTOR, reason='test',
                   tier='advanced', subscription_id=ADVANCED, billing_period='upfront',
                   status='expired')
    buy(user, url=PREMIUM_URL)
    capture(lambda: pay_on_fake(order_of(user), user))
    assert record(user).tier == 'premium'


# --- Paid twice, and a record moved meanwhile ----------------------------------------------

def test_paid_twice_changes_the_record_once(loaded, capture):
    user = spanish()
    buy(user)
    order = capture(lambda: pay_on_fake(order_of(user), user))
    version = record(user).version
    assert service.activate(order) is None
    assert record(user).version == version
    assert record(user).changes.filter(reason='purchase').count() == 1


@pytest.mark.parametrize('how', ['manual', 'paid'])
def test_a_record_moved_meanwhile_is_left_and_reported(loaded, sentry, how):
    user = spanish()
    buy(user)
    order = order_of(user)
    if how == 'manual':
        manual(user)
    else:
        service.change(user, expected_version=record(user).version,
                       actor=service.SYSTEM_ACTOR, reason='test', tier='premium',
                       subscription_id=PREMIUM, billing_period='upfront')
    before = record(user).business_values(), record(user).version
    order = pay_on_fake(order, user)
    assert order.state == 'paid'  # the payment is kept: staff decide
    assert (record(user).business_values(), record(user).version) == before
    assert sentry == [f'subscription purchase record cannot take it: order {order.pk}']


def test_a_doctor_without_a_record_is_backfilled_then_bought(loaded, capture):
    from subscriptions.models import DoctorSubscription
    user = spanish()
    buy(user)
    DoctorSubscription.objects.filter(doctor_id=user.pk).delete()
    capture(lambda: pay_on_fake(order_of(user), user))
    assert [change.reason for change in record(user).changes.order_by('version')] == [
        'backfill', 'purchase']
    assert record(user).tier == 'advanced'


def test_a_service_order_is_not_a_purchase(loaded, capture):
    user = spanish()
    order = payments.create_order(user, [resolver.OrderLine(resolver.product(
        'b0000001-0000-4000-8000-000000000001'))], actor=payments.doctor_actor(user))
    capture(lambda: pay_on_fake(order, user))
    assert record(user).tier == 'basic' and not queued()


# --- The page ------------------------------------------------------------------------------

def test_the_page_shows_net_prices_and_the_voucher_field(loaded, redsys_on):
    page = html(browser(spanish()).get(ADVANCED_URL))
    assert '60.00 EUR per month' in page and '600.00 EUR for a year, paid upfront' in page
    assert '12-month commitment' in page and '+ VAT where applicable' in page
    assert 'name="voucher"' in page and 'Continue to payment' in page
    assert 'value="upfront" class="peer sr-only" checked' in page


def test_the_spanish_page(loaded):
    page = html(browser(spanish()).get('/es/upgrade/advanced/'))
    assert 'Continuar al pago' in page and '+ IVA cuando corresponda' in page
