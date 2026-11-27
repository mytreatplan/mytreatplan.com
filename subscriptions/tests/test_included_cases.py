"""Included cases (epic-subscriptions 13): one test (or more) per row of the plan's matrix,
plus the concurrency guard (one use per order line) and the soonest-ending grant first.
A grant is given by `service.activate` when a paid subscription order activates the record
(frozen from the catalogue); a use follows its order through the `payments.hooks` receivers
of `subscriptions.included`. Nothing sets `OrderLine.included_case` yet (the cart's tick is
9.3/9.6): the tests mark every line of a service order as included by wrapping
`payments.service._line_rows`."""
import re
import uuid
from datetime import date, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction

from catalogue import resolver
from integration.tests.snapshot_rows import ALL, EU, TPS, VIRTUA, eu_subscription_rule
from integration.tests.snapshot_rows import full_subscription, pull
from payments import hooks
from payments import service as payments
from payments.models import Order, Payment
from payments.tests.conftest import country_map, fake_on  # noqa: F401
from subscriptions import included, service
from subscriptions.models import CaseGrant, CaseUse
from subscriptions.tests.helpers import ADVANCED, PREMIUM, catalogue, doctor
from subscriptions.tests.test_purchase import (browser, buy, capture, fake_pdf,  # noqa: F401
                                               html, order_of, pay_on_fake, spanish)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

EU_ADVANCED = 'e0000020-0000-4000-8000-000000000020'


def section(advanced=12, premium=25):
    """The subscriptions tests' catalogue with Advanced and Premium including `advanced`
    and `premium` TPS branded cases."""
    data = catalogue()
    data['billing_rules'].append(eu_subscription_rule())
    data['subscriptions'].append(full_subscription(
        EU_ADVANCED, 'advanced', EU, 'ADV-EU', 'Advanced', ALL, {'tps_branded': '6.00'},
        price={'currency': 'EUR', 'monthly': '60.00', 'upfront': '600.00'}))
    for row in data['subscriptions']:
        if row['id'] == ADVANCED:
            row['included_cases'] = {'tps_branded': advanced}
        elif row['id'] == PREMIUM:
            row['included_cases'] = {'tps_branded': premium}
    return data


@pytest.fixture
def loaded(admin, fake_on):
    assert pull(admin, 1, section()) == ('applied', 1)
    return admin


@pytest.fixture
def included_lines(monkeypatch):
    """Every line of a service order is an included case."""
    original = payments._line_rows

    def marked(order, priced, asked):
        rows = original(order, priced, asked)
        if order.kind == Order.SERVICE:
            for row in rows:
                row.included_case = True
        return rows

    monkeypatch.setattr(payments, '_line_rows', marked)


@pytest.fixture
def advanced(loaded, capture):
    """A Spanish doctor who bought Advanced upfront (paid on the fake gateway)."""
    user = spanish()
    buy(user)
    capture(lambda: pay_on_fake(order_of(user), user))
    return user


def actor(user):
    return payments.doctor_actor(user)


def new(user, product=TPS, lines=1):
    return payments.create_order(
        user, [resolver.OrderLine(resolver.product(product)) for _ in range(lines)],
        actor=actor(user))


def pay(order, user):
    order = payments.start_payment(order, 'fake', expected_version=order.version,
                                   actor=actor(user))
    return payments.mark_paid(order, expected_version=order.version,
                              actor=payments.SYSTEM_ACTOR)


def fresh(order):
    return Order.objects.get(pk=order.pk)


def uses(order):
    return list(CaseUse.objects.filter(order_id=order.pk).values_list('state', flat=True))


def left(user, day=None):
    return service.included_left(user, day)


def home_line(page):
    found = re.search(r'data-included-cases="(\d+)">([^<]*)<', page)
    return None if found is None else (int(found.group(1)), found.group(2).strip())


# --- Upfront Advanced ------------------------------------------------------------------------

def test_an_upfront_advanced_purchase_grants_the_frozen_number(advanced):
    grant = CaseGrant.objects.get()
    order = Order.objects.get(user=advanced, kind=Order.SUBSCRIPTION)
    record = service.record_of(advanced)
    assert (grant.doctor_uid, grant.source, grant.source_id, grant.category, grant.count,
            grant.accrual) == (advanced.uid, 'subscription_term', order.pk, 'tps_branded',
                               12, 'upfront')
    assert (grant.starts_on, grant.ends_on) == (record.started_on, record.ends_on)
    assert left(advanced) == (12, 12)
    assert included.available(advanced) == 12


def test_the_home_shows_the_cases_left(advanced):
    page = html(browser(advanced).get('/en/home/'))
    assert home_line(page) == (12, '12 of 12 included cases left this term')


def test_the_home_shows_the_cases_left_in_spanish(advanced):
    page = html(browser(advanced).get('/es/home/'))
    assert home_line(page) == (12, 'Casos incluidos restantes en este periodo: 12 de 12')


def test_a_repeated_paid_creates_no_second_grant(advanced):
    order = Order.objects.get(user=advanced, kind=Order.SUBSCRIPTION)
    with transaction.atomic():
        service.activate(order)
        hooks.fire('paid', order)
    assert CaseGrant.objects.count() == 1
    grant, created = included.grant_term(
        doctor_uid=advanced.uid, order_id=order.pk, count=99, accrual='monthly',
        starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
    assert not created and grant.count == 12 and grant.accrual == 'upfront'


# --- Monthly Premium ---------------------------------------------------------------------------

def test_a_monthly_payer_earns_the_cases_month_by_month(loaded):
    user = spanish()
    order = payments.create_subscription_order(
        user, resolver.subscription_by_id(PREMIUM), 'upfront', actor=actor(user))
    record = service._write(user, 1, service.SYSTEM_ACTOR, service.PURCHASE, {
        'tier': 'premium', 'subscription_id': PREMIUM, 'billing_period': 'monthly',
        'started_on': resolver.today(),
        'ends_on': resolver.today() + timedelta(days=364)}, order_id=order.pk)
    with transaction.atomic():
        included.grant_term(doctor_uid=user.uid, order_id=order.pk, count=25,
                            accrual='monthly', starts_on=record.started_on,
                            ends_on=record.ends_on)
    assert left(user) is None  # nothing paid yet: nothing earned
    for period in ('', '2027-01', '2027-02'):
        Payment.objects.create(order=order, gateway='fake', amount=order.total,
                               currency='EUR', state=Payment.CONFIRMED, period=period)
    Payment.objects.create(order=order, gateway='fake', amount=order.total, currency='EUR',
                           state=Payment.FAILED, period='2027-03')
    assert payments.months_paid(order.pk) == 3
    assert left(user) == (6, 6)  # 25 × 3 // 12


# --- Reserve, consume, release -----------------------------------------------------------------

def test_a_use_is_reserved_at_created_and_consumed_at_paid(advanced, included_lines):
    order = new(advanced)
    use = CaseUse.objects.get(order_id=order.pk)
    line = order.lines.get()
    assert (use.state, use.line_id, use.doctor_uid, use.version) == (
        'reserved', line.pk, advanced.uid, 1)
    assert left(advanced) == (11, 12)
    order = pay(order, advanced)
    assert uses(order) == ['consumed'] and left(advanced) == (11, 12)
    assert CaseUse.objects.get(order_id=order.pk).version == 2


def test_an_order_without_included_lines_takes_none(advanced):
    new(advanced)
    assert not CaseUse.objects.exists() and left(advanced) == (12, 12)


@pytest.mark.parametrize('how', ['cancel', 'expire'])
def test_a_cancelled_or_expired_order_releases_its_case(advanced, included_lines, how):
    order = new(advanced, lines=2)
    assert left(advanced) == (10, 12)
    move = getattr(payments, how)
    move(fresh(order), expected_version=fresh(order).version, actor=actor(advanced)
         if how == 'cancel' else payments.SYSTEM_ACTOR)
    assert uses(order) == ['released', 'released'] and left(advanced) == (12, 12)


def test_a_consumed_case_is_never_released_by_a_cancel(advanced, included_lines):
    order = pay(new(advanced), advanced)
    with transaction.atomic():
        included.release(fresh(order))
    assert uses(order) == ['consumed'] and left(advanced) == (11, 12)


# --- Refund ------------------------------------------------------------------------------------

def test_a_refund_in_term_gives_the_case_back(advanced, included_lines):
    order = pay(new(advanced), advanced)
    assert ('included_refund', included.refund) in hooks.receivers('refunded')
    with transaction.atomic():
        hooks.fire('refunded', fresh(order))
    assert uses(order) == ['released'] and left(advanced) == (12, 12)
    with transaction.atomic():
        included.refund(fresh(order))  # again: nothing changes
    assert CaseUse.objects.get(order_id=order.pk).version == 3


def test_a_refund_after_the_term_gives_nothing_back(advanced, included_lines, monkeypatch):
    order = pay(new(advanced), advanced)
    ends_on = CaseGrant.objects.get().ends_on
    monkeypatch.setattr(resolver, 'today', lambda now=None: ends_on + timedelta(days=1))
    with transaction.atomic():
        included.refund(fresh(order))
    assert uses(order) == ['consumed']


# --- None left ---------------------------------------------------------------------------------

def test_no_order_is_created_without_a_case_available(advanced, included_lines):
    CaseGrant.objects.update(count=1)
    new(advanced)
    assert left(advanced) == (0, 1)
    before = Order.objects.count()
    with pytest.raises(payments.HookFailed):
        new(advanced)
    assert Order.objects.count() == before and CaseUse.objects.count() == 1


def test_an_order_asking_more_than_left_is_refused_whole(advanced, included_lines):
    CaseGrant.objects.update(count=2)
    with pytest.raises(payments.HookFailed):
        new(advanced, lines=3)
    assert not CaseUse.objects.exists() and left(advanced) == (2, 2)


def test_a_line_of_another_category_takes_no_tps_case(advanced, included_lines):
    with pytest.raises(payments.HookFailed):
        new(advanced, product=VIRTUA)
    assert left(advanced) == (12, 12)


# --- Repeat hooks ------------------------------------------------------------------------------

def test_the_hooks_are_idempotent(advanced, included_lines):
    order = new(advanced)
    with transaction.atomic():
        included.reserve(fresh(order))
    assert uses(order) == ['reserved'] and left(advanced) == (11, 12)
    order = pay(order, advanced)
    with transaction.atomic():
        included.consume(fresh(order))
        included.consume(fresh(order))
        included.reserve(fresh(order))
    assert uses(order) == ['consumed'] and left(advanced) == (11, 12)
    assert CaseUse.objects.get(order_id=order.pk).version == 2


def test_a_line_paid_without_a_use_is_reserved_at_payment(advanced, included_lines):
    order = new(advanced)
    CaseUse.objects.filter(order_id=order.pk).delete()  # an order made before this release
    pay(order, advanced)
    assert uses(order) == ['consumed'] and left(advanced) == (11, 12)


# --- Catalogue edit ----------------------------------------------------------------------------

def test_a_catalogue_edit_never_changes_a_grant(advanced, admin):
    assert pull(admin, 2, section(advanced=20)) == ('applied', 2)
    assert resolver.included_cases(resolver.subscription_by_id(ADVANCED), 'tps_branded') == 20
    assert CaseGrant.objects.get().count == 12 and left(advanced) == (12, 12)


# --- Basic, expired, dropped -------------------------------------------------------------------

def test_a_basic_doctor_has_none(loaded, included_lines):
    user = spanish()
    assert left(user) is None and included.available(user) == 0
    assert home_line(html(browser(user).get('/en/home/'))) is None
    with pytest.raises(payments.HookFailed):
        new(user)


@pytest.mark.parametrize('fields', [
    {'status': 'expired'},
    {'tier': 'basic', 'subscription_id': None, 'billing_period': '', 'ends_on': None},
])
def test_an_expired_or_dropped_record_counts_none(advanced, fields):
    record = service.record_of(advanced)
    service.change(advanced, expected_version=record.version, actor=service.SYSTEM_ACTOR,
                   reason='test', **fields)
    assert left(advanced) is None and included.available(advanced) == 0
    assert home_line(html(browser(advanced).get('/en/home/'))) is None


def test_a_cancelled_record_counts_until_its_end(advanced):
    record = service.record_of(advanced)
    service.change(advanced, expected_version=record.version, actor=service.SYSTEM_ACTOR,
                   reason='test', status='cancelled')
    assert left(advanced, record.ends_on) == (12, 12)
    assert left(advanced, record.ends_on + timedelta(days=1)) is None


def test_a_grant_outside_its_term_counts_none(advanced):
    grant = CaseGrant.objects.get()
    assert left(advanced, grant.starts_on - timedelta(days=1)) is None


def test_only_the_current_purchase_grant_counts(advanced, capture):
    old = CaseGrant.objects.get()
    service.reset_to_basic(advanced)
    assert left(advanced) is None
    buy(advanced)
    capture(lambda: pay_on_fake(Order.objects.filter(user=advanced, kind=Order.SUBSCRIPTION,
                                                     state='checkout').get(), advanced))
    assert CaseGrant.objects.count() == 2
    new_grant = CaseGrant.objects.exclude(pk=old.pk).get()
    assert left(advanced) == (12, 12)
    assert included._eligible(advanced, resolver.today()) == [new_grant]


def test_the_tag_never_breaks_the_home(advanced, monkeypatch):
    def broken(user, day=None):
        raise RuntimeError('boom')

    monkeypatch.setattr(service, 'included_left', broken)
    assert home_line(html(browser(advanced).get('/en/home/'))) is None


# --- Backfill ----------------------------------------------------------------------------------

def run(*args):
    out = StringIO()
    call_command('grant_included_cases', *args, stdout=out)
    return out.getvalue().strip()


def test_the_backfill_grants_a_paid_record_without_one(advanced, loaded):
    CaseGrant.objects.all().delete()  # activated before this release
    other = doctor('basic@example.com')
    assert run('--dry-run') == ('grant_included_cases: 1 doctor(s) without a grant (dry run: '
                                'nothing written)')
    assert not CaseGrant.objects.exists()
    assert run() == 'grant_included_cases: 1 doctor(s) given their included cases'
    grant = CaseGrant.objects.get()
    record = service.record_of(advanced)
    assert (grant.doctor_uid, grant.count, grant.accrual, grant.starts_on, grant.ends_on) == (
        advanced.uid, 12, 'upfront', record.started_on, record.ends_on)
    assert left(advanced) == (12, 12) and left(other) is None
    assert run() == 'grant_included_cases: 0 doctor(s) given their included cases'
    assert run('--dry-run').startswith('grant_included_cases: 0 doctor(s)')


def test_a_staff_account_paid_record_is_counted_by_neither_run(advanced):
    CaseGrant.objects.all().delete()
    type(advanced).objects.filter(pk=advanced.pk).update(is_staff=True)
    assert service.records_missing_a_grant() == []
    assert run('--dry-run').startswith('grant_included_cases: 0 doctor(s)')
    assert run() == 'grant_included_cases: 0 doctor(s) given their included cases'
    assert not CaseGrant.objects.exists()


# --- Concurrency guard and grant order ---------------------------------------------------------

def test_one_use_per_order_line(advanced, included_lines):
    order = new(advanced)
    use = CaseUse.objects.get(order_id=order.pk)
    with pytest.raises(IntegrityError), transaction.atomic():
        CaseUse.objects.create(grant=use.grant, doctor_uid=use.doctor_uid,
                               order_id=use.order_id, line_id=use.line_id, state='reserved')


def test_the_soonest_ending_grant_is_used_first(advanced, included_lines, monkeypatch):
    term = CaseGrant.objects.get()
    with transaction.atomic():
        sooner, _ = included.grant_term(
            doctor_uid=advanced.uid, order_id=uuid.uuid4(), count=1, accrual='upfront',
            starts_on=term.starts_on, ends_on=term.ends_on - timedelta(days=30))
    assert left(advanced) == (12, 12)  # only the current purchase's grant counts
    # Two counting grants (later sources, e.g. renewals): the soonest-ending is used first.
    monkeypatch.setattr(included, '_term_sources',
                        lambda user, day: [term.source_id, sooner.source_id])
    assert left(advanced) == (13, 13)
    first = CaseUse.objects.get(order_id=new(advanced).pk)
    second = CaseUse.objects.get(order_id=new(advanced).pk)
    assert (first.grant_id, second.grant_id) == (sooner.pk, term.pk)
    assert left(advanced) == (11, 13)
