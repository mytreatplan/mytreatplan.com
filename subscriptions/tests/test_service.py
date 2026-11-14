"""A doctor's subscription (epic-subscriptions entry 1): Basic at signup and from the
Django admin, the effective-tier rule, the doctor's offers and order prices, and the
versioned change (`subscriptions.service`). One test (or more) per row of the plan's
matrix. The admin site is the fake of integration/tests/snapshot_rows.py (no network)."""
import logging
import threading
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import Client

from catalogue import resolver
from catalogue.models import Product
from integration.tests.snapshot_rows import ADA, EU, SPAIN, SUBSCRIPTIONS, pull
from mytp_core.money import Money
from signup.tests.helpers import NORMALIZED, _complete
from subscriptions import service
from subscriptions.models import DoctorSubscription, SubscriptionChange

from .helpers import (ADVANCED, BASIC_IB, DAY, MANUAL, NEW_BASIC, NORDICS, PASSWORD, TPS,
                      catalogue, doctor, load, priced)

pytestmark = pytest.mark.django_db
User = get_user_model()
SYSTEM = service.SYSTEM_ACTOR
ADMIN = service.admin_actor(ADA)


def record(user):
    return DoctorSubscription.objects.get(doctor=user)


def changes(user):
    return list(SubscriptionChange.objects.filter(record__doctor=user)
                .order_by('version').values_list('reason', 'actor_type', 'version'))


def advanced(user, **fields):
    """The doctor on Iberia's Advanced (by the system), with `fields`."""
    return service.change(user, expected_version=record(user).version, actor=SYSTEM,
                          reason='test', tier='advanced', subscription_id=ADVANCED,
                          billing_period='monthly', **fields)


def held(user, day=DAY):
    found = service.effective(user, day)
    code = found.subscription.code if found.subscription else None
    return found.tier, code, found.held, found.manual, found.can_self_switch


# --- Signup --------------------------------------------------------------------------------

@pytest.fixture
def signup(client, capture_codes, clear_cache, portal_urls):
    def run(landing='/en/welcome/', **fields):
        response = _complete(client, **fields)
        assert response['Location'] == landing
        return User.objects.by_email(NORMALIZED).get()
    return run


def test_signup_of_an_es_doctor_gives_basic_priced_under_iberias(admin, signup):
    load(admin)
    user = signup(landing='/en/upgrade/')  # the group sells Advanced and Premium
    found = record(user)
    assert (found.tier, found.subscription_id, found.ends_on, found.billing_period,
            found.auto_renew, found.status, found.manual, found.version) == (
        'basic', None, None, '', False, 'active', False, 1)
    assert found.started_on == resolver.today()
    assert (found.actor_type, found.actor_uuid) == ('doctor', user.uid)
    change = SubscriptionChange.objects.get(record=found)
    assert (change.reason, change.actor_type, change.actor_uuid, change.before,
            change.version) == ('signup', 'doctor', user.uid, {}, 1)
    assert change.after == {'tier': 'basic', 'subscription_id': None,
                            'started_on': found.started_on.isoformat(), 'ends_on': None,
                            'billing_period': '', 'auto_renew': False, 'status': 'active',
                            'manual': False}
    assert held(user) == ('basic', 'BAS-0', False, False, True)
    order = priced(user)
    assert order.company.pk == uuid.UUID(SPAIN)
    assert order.total == Money(Decimal('100.00'), 'EUR')


def test_signup_without_a_snapshot_succeeds_on_basic_offered_nothing(signup):
    user = signup()
    assert record(user).tier == 'basic' and changes(user) == [('signup', 'doctor', 1)]
    assert held(user) == ('basic', None, False, False, True)
    assert service.offers_for(user, DAY) == []
    # No product row either: an unsaved one stands for what a caller would ask.
    product = Product(id=uuid.UUID(TPS), name='TPS', category='tps_branded',
                      net_amount=Decimal('100.00'), currency='EUR', active=True)
    with pytest.raises(resolver.OrderRefused) as refused:
        service.price_order_for(user, [resolver.OrderLine(product)],
                                vies=resolver.VIES_NOT_APPLICABLE)
    assert refused.value.reason == 'country_not_served'
    with pytest.raises(resolver.OrderRefused) as refused:
        service.price_order_for(user, [], vies=resolver.VIES_NOT_APPLICABLE)
    assert refused.value.reason == 'no_lines'


@pytest.mark.parametrize('country', ['AE', '', 'SE'],
                         ids=['other region', 'blank', 'group without a basic'])
def test_a_doctor_with_no_basic_here_is_on_basic_offered_nothing(admin, country):
    load(admin)
    user = doctor(country=country)
    assert held(user) == ('basic', None, False, False, True)
    assert service.offers_for(user, DAY) == []
    with pytest.raises(resolver.OrderRefused) as refused:
        priced(user)
    assert refused.value.reason == ('product_not_offered' if country == 'SE'
                                    else 'country_not_served')


def test_a_doctor_signed_up_before_the_first_snapshot_is_priced_once_it_arrives(
        signup, admin):
    user = signup()
    load(admin)  # the TPS row exists; the doctor's country has a group now
    assert priced(user).company.pk == uuid.UUID(SPAIN)


def test_the_groups_basic_replaced_on_the_admin_site_reprices_the_doctor(admin):
    load(admin)
    user = doctor()
    load(admin, 2, basic_ib=NEW_BASIC)
    found = service.effective(user, DAY)
    assert found.subscription.pk == uuid.UUID(NEW_BASIC) and not found.held
    assert record(user).version == 1 and record(user).subscription_id is None


def test_a_change_of_country_moves_basic_to_the_new_group(admin):
    load(admin)
    user = doctor()
    user.country = 'DE'
    user.save()
    assert service.effective(user, DAY).subscription.pk == uuid.UUID(SUBSCRIPTIONS[EU])
    assert priced(user).company.code == 'IE'


# --- Staff, the Django admin ---------------------------------------------------------------

def test_staff_and_superusers_get_no_record():
    superuser = User.objects.create_superuser('root@example.net', PASSWORD)
    staff = User.objects.create_user('staff@example.net', PASSWORD, is_staff=True)
    with transaction.atomic():
        assert service.ensure_basic(superuser, SYSTEM) is None
        assert service.ensure_basic(staff, SYSTEM) is None
    assert not DoctorSubscription.objects.exists()


def test_a_doctor_added_in_the_django_admin_gets_basic_by_the_system():
    staff = User.objects.create_superuser('root@example.net', PASSWORD)
    client = Client()
    client.force_login(staff)
    response = client.post('/admin/accounts/user/add/', {
        'email': 'new@example.com', 'usable_password': 'true',
        'password1': PASSWORD, 'password2': PASSWORD})
    assert response.status_code == 302
    user = User.objects.by_email('new@example.com').get()
    assert (record(user).tier, record(user).actor_type) == ('basic', 'system')
    assert changes(user) == [('signup', 'system', 1)]
    assert held(user) == ('basic', None, False, False, True)


def test_ensure_basic_twice_keeps_the_first_record(admin):
    user = doctor()
    with transaction.atomic():
        assert service.ensure_basic(user, SYSTEM) is None
    assert changes(user) == [('signup', 'doctor', 1)]


@pytest.mark.django_db(transaction=True)
def test_ensure_basic_refuses_to_run_outside_a_transaction():
    user = User.objects.create_user('bea@example.com', PASSWORD)
    with pytest.raises(service.ServiceError):
        service.ensure_basic(user, SYSTEM)
    assert not DoctorSubscription.objects.exists()


def test_a_doctor_without_a_record_counts_as_basic(admin):
    load(admin)
    user = User.objects.create_user('old@example.com', PASSWORD, country='ES')
    assert held(user) == ('basic', 'BAS-0', False, False, True)
    assert service.effective(user, DAY).record is None
    with pytest.raises(service.RecordGone):
        service.change(user, expected_version=1, actor=SYSTEM, reason='test',
                       status='expired')


# --- The effective-tier rule ---------------------------------------------------------------

def test_paid_and_active_is_the_records_tier_and_row(admin):
    load(admin)
    user = doctor()
    advanced(user)
    assert held(user) == ('advanced', 'ADV-IB', True, False, True)
    assert priced(user).lines[0].discount_percent == Decimal('6.00')


def test_past_due_keeps_the_tier(admin):
    load(admin)
    user = doctor()
    advanced(user, status='past_due')
    assert held(user)[:3] == ('advanced', 'ADV-IB', True)


def test_cancelled_counts_up_to_and_including_its_end_date(admin):
    load(admin)
    user = doctor()
    advanced(user, status='cancelled', ends_on=DAY)
    assert held(user, DAY)[:3] == ('advanced', 'ADV-IB', True)
    assert held(user, DAY - timedelta(days=3))[:3] == ('advanced', 'ADV-IB', True)
    assert held(user, DAY + timedelta(days=1))[:3] == ('basic', 'BAS-0', False)


def test_expired_is_basic_with_the_groups_basic(admin):
    load(admin)
    user = doctor()
    advanced(user, status='expired', ends_on=DAY)
    assert held(user, DAY)[:3] == ('basic', 'BAS-0', False)


def test_an_active_record_past_its_end_date_still_counts_as_its_tier(admin):
    load(admin)
    user = doctor()
    advanced(user, ends_on=DAY)
    assert held(user, DAY + timedelta(days=30))[:3] == ('advanced', 'ADV-IB', True)


def test_a_deactivated_held_row_still_offers_and_prices_as_before(admin):
    load(admin)
    user = doctor()
    advanced(user)
    before = service.offers_for(user, DAY)
    assert before
    load(admin, 2, advanced_active=False)
    row = resolver.subscription_by_id(ADVANCED)
    assert row.active is False
    assert held(user)[:3] == ('advanced', 'ADV-IB', True)
    assert service.offers_for(user, DAY) == before
    assert priced(user).lines[0].discount_percent == Decimal('6.00')
    # The resolver's own answers are unchanged for today's calls.
    assert resolver.offers_under(row, DAY) == []
    assert resolver.subscription('ES', 'advanced') is None
    lines = [resolver.OrderLine(resolver.product(TPS))]
    with pytest.raises(resolver.OrderRefused) as refused:
        resolver.price_order('ES', row, lines, vies=resolver.VIES_NOT_APPLICABLE, day=DAY)
    assert refused.value.reason == 'product_not_offered'
    assert resolver.price_order('ES', row, lines, vies=resolver.VIES_NOT_APPLICABLE,
                                day=DAY, held=True).total == Money(Decimal('94.00'), 'EUR')
    with pytest.raises(resolver.OrderRefused) as refused:  # a purchase: not sold
        resolver.price_subscription('ES', row, 'monthly', vies=resolver.VIES_NOT_APPLICABLE,
                                    day=DAY)
    assert refused.value.reason == 'subscription_not_offered'


def test_a_held_row_missing_from_the_snapshot_is_priced_with_basic_and_logged(admin, caplog):
    load(admin)
    user = doctor()
    advanced(user)
    load(admin, 2, advanced=False)
    with caplog.at_level(logging.ERROR, logger='subscriptions.service'):
        assert held(user)[:3] == ('advanced', 'BAS-0', False)
    assert str(ADVANCED) in caplog.text and 'not in the catalogue' in caplog.text
    assert priced(user).lines[0].discount_percent == Decimal('0.00')


# --- Manual ---------------------------------------------------------------------------------

def test_assigning_a_manual_subscription(admin):
    load(admin)
    user = doctor()
    ends = DAY + timedelta(days=365)
    found = service.assign_manual(user, resolver.subscription_by_id(MANUAL),
                                  expected_version=1, actor=ADMIN, ends_on=ends)
    assert (found.tier, found.subscription_id, found.manual, found.auto_renew, found.status,
            found.ends_on, found.version) == ('premium', uuid.UUID(MANUAL), True, False,
                                             'active', ends, 2)
    assert (found.actor_type, found.actor_uuid) == ('admin', uuid.UUID(ADA))
    assert held(user) == ('premium', 'ORMCO', True, True, False)
    change = SubscriptionChange.objects.get(record=found, version=2)
    assert change.reason == 'manual_assigned'
    assert change.before['tier'] == 'basic' and change.after['tier'] == 'premium'
    assert change.after['subscription_id'] == MANUAL


def test_a_manual_record_refuses_a_doctors_change_and_takes_an_admins(admin):
    load(admin)
    user = doctor()
    service.assign_manual(user, resolver.subscription_by_id(MANUAL), expected_version=1,
                          actor=SYSTEM)
    with pytest.raises(service.ManualLocked):
        service.change(user, expected_version=2, actor=service.doctor_actor(user),
                       reason='switch', tier='basic', subscription_id=None, manual=False)
    with pytest.raises(service.ManualLocked):
        service.change(user, expected_version=2, actor=service.doctor_actor(user),
                       reason='renewal', auto_renew=True)
    assert record(user).version == 2 and record(user).manual
    service.change(user, expected_version=2, actor=ADMIN, reason='admin', status='past_due')
    service.change(user, expected_version=3, actor=SYSTEM, reason='system', status='active')
    assert record(user).version == 4
    assert [reason for reason, *_ in changes(user)] == [
        'signup', 'manual_assigned', 'admin', 'system']


def test_assign_manual_is_refused_for_a_doctor_or_a_standard_row(admin):
    load(admin)
    user = doctor()
    with pytest.raises(service.ChangeRefused):
        service.assign_manual(user, resolver.subscription_by_id(MANUAL), expected_version=1,
                              actor=service.doctor_actor(user))
    with pytest.raises(service.ChangeRefused):
        service.assign_manual(user, resolver.subscription_by_id(ADVANCED),
                              expected_version=1, actor=ADMIN)
    assert record(user).version == 1 and changes(user) == [('signup', 'doctor', 1)]


# --- Versions and checks ---------------------------------------------------------------------

def test_a_stale_version_changes_nothing(admin):
    load(admin)
    user = doctor()
    advanced(user)
    service.change(user, expected_version=2, actor=SYSTEM, reason='test', auto_renew=True)
    with pytest.raises(service.StaleVersion) as stale:
        service.change(user, expected_version=2, actor=SYSTEM, reason='test',
                       status='cancelled', ends_on=DAY)
    assert stale.value.current_version == 3
    found = record(user)
    assert (found.version, found.status) == (3, 'active')
    assert SubscriptionChange.objects.filter(record=found).count() == 3


def test_stale_version_is_the_remote_writes():
    from integration import remote
    assert service.StaleVersion is remote.StaleVersion


@pytest.mark.parametrize('fields', [
    {'colour': 'blue'},
    {'tier': 'gold'},
    {'status': 'paused'},
    {'billing_period': 'weekly'},
    {'auto_renew': 'yes'},
    {'tier': 'advanced'},  # without its subscription
    {'subscription_id': BASIC_IB},  # a standard Basic holds the level only
    {'tier': 'premium', 'subscription_id': ADVANCED},  # another tier's row
    {'tier': 'advanced', 'subscription_id': str(uuid.uuid4())},  # not in the catalogue
    {'tier': 'premium', 'subscription_id': MANUAL},  # a manual row on a standard record
    {'ends_on': date(2000, 1, 1)},  # before the start
    {'started_on': None},
    {'ends_on': '2027-01-01'},
    {'status': 'cancelled'},  # without an end date
], ids=lambda fields: ','.join(fields))
def test_a_change_with_wrong_values_is_refused(admin, fields):
    load(admin)
    user = doctor()
    with pytest.raises(service.ChangeRefused):
        service.change(user, expected_version=1, actor=SYSTEM, reason='test', **fields)
    assert record(user).version == 1


def test_a_doctor_cannot_make_their_record_manual(admin):
    load(admin)
    user = doctor()
    with pytest.raises(service.ChangeRefused):
        service.change(user, expected_version=1, actor=service.doctor_actor(user),
                       reason='test', manual=True, tier='premium', subscription_id=MANUAL)


def test_a_doctor_may_change_their_own_standard_record(admin):
    load(admin)
    user = doctor()
    found = service.change(user, expected_version=1, actor=service.doctor_actor(user),
                           reason='switch', tier='advanced', subscription_id=ADVANCED,
                           billing_period='monthly', auto_renew=True)
    assert (found.version, found.actor_type, found.actor_uuid) == (2, 'doctor', user.uid)


@pytest.mark.parametrize('actor', [None, service.Actor('robot'),
                                   service.Actor('system', uuid.uuid4()),
                                   service.Actor('admin')])
def test_an_unknown_actor_is_refused(admin, actor):
    user = doctor()
    with pytest.raises(service.ChangeRefused):
        service.change(user, expected_version=1, actor=actor, reason='test',
                       auto_renew=False)


# --- Two changes at once (PostgreSQL) ------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_two_changes_from_the_same_version_one_wins(monkeypatch):
    if connection.vendor != 'postgresql':
        pytest.skip('row locks need PostgreSQL')
    with transaction.atomic():
        user = User.objects.create_user('race@example.com', PASSWORD, country='ES')
        service.ensure_basic(user, SYSTEM)
    original = service._check

    def slow_check(*args):
        import time
        time.sleep(0.2)  # both are in flight together
        return original(*args)
    monkeypatch.setattr(service, '_check', slow_check)
    barrier = threading.Barrier(2)
    results = [None, None]

    def run(index):
        from django.db import connections
        try:
            barrier.wait()
            results[index] = service.change(user, expected_version=1, actor=SYSTEM,
                                            reason=f'race{index}', auto_renew=bool(index))
        except Exception as error:  # noqa: BLE001
            results[index] = error
        finally:
            connections.close_all()

    threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stale = [result for result in results if isinstance(result, service.StaleVersion)]
    assert len(stale) == 1 and stale[0].current_version == 2
    assert record(user).version == 2
    assert SubscriptionChange.objects.filter(record__doctor=user).count() == 2


# --- Review patches (2026-11-14) -------------------------------------------------------------

def _edited(edit):
    """The catalogue, without Iberia's Premium, with `edit(section, row)` applied to
    Iberia's Advanced row."""
    section = catalogue(premium=False)
    row = next(found for found in section['subscriptions'] if found['id'] == ADVANCED)
    edit(section, row)
    return section


def _to_manual(section, row):
    section['subscriptions'].remove(row)
    section['manual_subscriptions'].append(row)


@pytest.mark.parametrize('edit', [
    lambda section, row: row.update(tier='premium'),
    lambda section, row: row.update(tier='basic', active=False),
    _to_manual,
], ids=['higher tier', 'lower tier', 'made manual'])
def test_a_held_row_edited_under_the_same_id_is_priced_with_basic(admin, caplog, edit):
    load(admin)
    user = doctor()
    advanced(user)
    assert pull(admin, 2, _edited(edit)) == ('applied', 2)
    with caplog.at_level(logging.ERROR, logger='subscriptions.service'):
        assert held(user)[:3] == ('advanced', 'BAS-0', False)
    assert 'is now' in caplog.text and 'not in the catalogue' not in caplog.text
    order = priced(user)
    assert order.lines[0].discount_percent == Decimal('0.00')
    assert order.total == Money(Decimal('100.00'), 'EUR')
    assert service.offers_for(user, DAY) == resolver.offers_under(
        resolver.subscription('ES', 'basic'), DAY, country='ES')


def test_a_manual_row_made_standard_is_priced_with_basic(admin, caplog):
    load(admin)
    user = doctor()
    service.assign_manual(user, resolver.subscription_by_id(MANUAL), expected_version=1,
                          actor=ADMIN)
    section = catalogue(premium=False)
    row = section.pop('manual_subscriptions')[0]
    section['subscriptions'].append(row)
    assert pull(admin, 2, section) == ('applied', 2)
    with caplog.at_level(logging.ERROR, logger='subscriptions.service'):
        assert held(user) == ('premium', 'BAS-0', False, True, False)
    assert 'is now standard premium' in caplog.text


# Staff accounts

def test_a_doctor_added_in_the_django_admin_then_made_staff_has_no_record(admin):
    load(admin)
    staff = User.objects.create_superuser('root@example.net', PASSWORD)
    client = Client()
    client.force_login(staff)
    assert client.post('/admin/accounts/user/add/', {
        'email': 'new@example.com', 'usable_password': 'true', 'password1': PASSWORD,
        'password2': PASSWORD}).status_code == 302
    user = User.objects.by_email('new@example.com').get()
    assert service.record_of(user) is not None
    user.is_staff = True  # the "staff" box ticked
    user.save()
    _ignored(user)


def test_a_doctor_promoted_to_superuser_keeps_an_ignored_row(admin):
    load(admin)
    user = doctor()
    advanced(user)
    user.is_superuser = True
    user.save()
    _ignored(user)
    with pytest.raises(service.RecordGone):
        service.assign_manual(user, resolver.subscription_by_id(MANUAL),
                              expected_version=2, actor=ADMIN)


def _ignored(user):
    found = service.effective(user, DAY)
    assert (found.record, found.tier, found.held, found.manual) == (None, 'basic', False,
                                                                   False)
    assert service.record_of(user) is None
    assert user.pk not in service.doctors_without_a_record()
    version = DoctorSubscription.objects.get(doctor=user).version
    with pytest.raises(service.RecordGone):
        service.change(user, expected_version=version, actor=SYSTEM, reason='test',
                       auto_renew=True)
    assert DoctorSubscription.objects.get(doctor=user).version == version  # kept


# The manual lock while the record counts

@pytest.fixture
def on_day(monkeypatch):
    def set_day(day):
        monkeypatch.setattr('catalogue.resolver.today', lambda now=None: day)
    return set_day


def _manual(user, on_day, start=DAY - timedelta(days=10)):
    on_day(start)
    service.assign_manual(user, resolver.subscription_by_id(MANUAL), expected_version=1,
                          actor=ADMIN)


def _doctor_moves_to_advanced(user):
    return service.change(user, expected_version=record(user).version,
                          actor=service.doctor_actor(user), reason='switch',
                          tier='advanced', subscription_id=ADVANCED, status='active',
                          billing_period='monthly', ends_on=None)


@pytest.mark.parametrize('status, ends_on', [('expired', None),
                                             ('cancelled', DAY - timedelta(days=5))],
                         ids=['expired', 'cancelled past its end'])
def test_a_manual_record_that_no_longer_counts_frees_the_doctor(admin, on_day, status,
                                                                ends_on):
    load(admin)
    user = doctor()
    _manual(user, on_day)
    service.change(user, expected_version=2, actor=SYSTEM, reason='end', status=status,
                   ends_on=ends_on)
    on_day(DAY)
    assert held(user) == ('basic', 'BAS-0', False, True, True)
    found = _doctor_moves_to_advanced(user)
    assert (found.tier, found.manual, found.subscription_id, found.version) == (
        'advanced', False, uuid.UUID(ADVANCED), 4)
    assert held(user) == ('advanced', 'ADV-IB', True, False, True)
    last = SubscriptionChange.objects.get(record=found, version=4)
    assert (last.before['manual'], last.after['manual']) == (True, False)


def test_a_doctor_leaving_a_free_manual_record_for_basic_makes_it_standard(admin, on_day):
    load(admin)
    user = doctor()
    _manual(user, on_day)
    service.change(user, expected_version=2, actor=SYSTEM, reason='end', status='expired')
    on_day(DAY)
    found = service.change(user, expected_version=3, actor=service.doctor_actor(user),
                           reason='switch', tier='basic', subscription_id=None,
                           status='active')
    assert (found.tier, found.manual, found.subscription_id) == ('basic', False, None)
    with pytest.raises(service.ChangeRefused):  # never back to manual by the doctor
        service.change(user, expected_version=4, actor=service.doctor_actor(user),
                       reason='switch', tier='premium', subscription_id=MANUAL)


@pytest.mark.parametrize('status, ends_on', [
    ('active', None), ('past_due', None), ('cancelled', DAY),
    ('active', DAY - timedelta(days=1))],
    ids=['active', 'past due', 'cancelled until today', 'active past its end'])
def test_a_manual_record_that_still_counts_stays_locked(admin, on_day, status, ends_on):
    load(admin)
    user = doctor()
    _manual(user, on_day)
    service.change(user, expected_version=2, actor=SYSTEM, reason='set', status=status,
                   ends_on=ends_on)
    on_day(DAY)
    assert held(user)[3:] == (True, False)
    with pytest.raises(service.ManualLocked):
        _doctor_moves_to_advanced(user)
    assert record(user).version == 3 and record(user).manual


# ensure_basic's race and other integrity errors

def test_ensure_basic_losing_the_race_returns_none(monkeypatch):
    user = doctor()  # "another transaction" gave the record first
    monkeypatch.setattr(service, '_has_record', lambda user: False)
    with transaction.atomic():
        assert service.ensure_basic(user, SYSTEM) is None
    assert DoctorSubscription.objects.filter(doctor=user).count() == 1
    assert changes(user) == [('signup', 'doctor', 1)]


def test_ensure_basic_lets_another_integrity_error_through(monkeypatch):
    from django.db import IntegrityError
    user = User.objects.create_user('bea@example.com', PASSWORD, country='ES')

    def broken(**kwargs):
        raise IntegrityError('something else')
    monkeypatch.setattr(SubscriptionChange.objects, 'create', broken)
    with transaction.atomic():
        with pytest.raises(IntegrityError, match='something else'):
            service.ensure_basic(user, SYSTEM)
        assert not DoctorSubscription.objects.filter(doctor=user).exists()


# price_order_for without a subscription, in price_order's order

def test_without_any_billing_rule_the_refusal_is_no_billing_rules(admin):
    from integration.tests.snapshot_rows import billed, group
    section = billed(rules_=False, companies=None)
    section['country_groups'].append(group(NORDICS, 'Nordics', ['SE']))
    assert pull(admin, 1, section) == ('applied', 1)
    for country in ('SE', 'ES'):  # no Basic in the group / a Basic: the same refusal
        with pytest.raises(resolver.OrderRefused) as refused:
            priced(doctor(f'{country}@example.com', country=country))
        assert refused.value.reason == 'no_billing_rules'
