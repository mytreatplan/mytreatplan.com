"""The backfill (migration subscriptions 0002 and `assign_basic`) and the read-only command
`doctor_subscription` (epic-subscriptions entry 1)."""
import io
from datetime import date, datetime, timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from subscriptions import service
from subscriptions.models import DoctorSubscription, SubscriptionChange

from .helpers import ADVANCED, DAY, MANUAL, PASSWORD, doctor, load

User = get_user_model()
BEFORE = [('subscriptions', '0001_initial')]
ACCOUNTS = [('accounts', '0014_sync_version')]  # the users' table as 0002 reads it
# 23:30 UTC on 1 March is 2 March in Madrid.
JOINED = datetime(2026, 3, 1, 23, 30, tzinfo=dt_timezone.utc)


def run(*args):
    out = io.StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


# --- The migration and assign_basic -------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_the_migration_gives_existing_doctors_basic_and_the_command_then_none():
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    try:
        OldUser = executor.loader.project_state(ACCOUNTS).apps.get_model('accounts', 'User')
        for index in range(3):
            OldUser.objects.create(email=f'old{index}@example.com', email_index=f'old{index}',
                                   password='!', date_joined=JOINED)
        OldUser.objects.create(email='staff@example.com', email_index='staff', password='!',
                               is_staff=True)
        OldUser.objects.create(email='root@example.com', email_index='root', password='!',
                               is_staff=True, is_superuser=True)
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    doctors = User.objects.filter(email_index__startswith='old')
    records = DoctorSubscription.objects.filter(doctor__in=doctors)
    assert records.count() == 3 and DoctorSubscription.objects.count() == 3
    assert {(r.tier, r.status, r.subscription_id, r.manual, r.auto_renew, r.billing_period,
             r.version, r.actor_type, r.actor_uuid, r.started_on) for r in records} == {
        ('basic', 'active', None, False, False, '', 1, 'system', None, date(2026, 3, 2))}
    rows = SubscriptionChange.objects.all()
    assert rows.count() == 3
    assert {(row.reason, row.actor_type, row.actor_uuid, row.version) for row in rows} == {
        ('backfill', 'system', None, 1)}
    assert all(row.before == {} and row.after == {
        'tier': 'basic', 'subscription_id': None, 'started_on': '2026-03-02',
        'ends_on': None, 'billing_period': '', 'auto_renew': False, 'status': 'active',
        'manual': False} for row in rows)
    assert run('assign_basic') == 'assign_basic: 0 doctor(s) given Basic\n'


@pytest.mark.django_db
def test_assign_basic_gives_a_doctor_of_the_previous_release_a_record_once():
    User.objects.create_superuser('root@example.net', PASSWORD)
    kept = doctor()
    old = User.objects.create_user('old@example.com', PASSWORD, date_joined=JOINED)
    assert run('assign_basic', '--dry-run') == (
        'assign_basic: 1 doctor(s) without a record (dry run: nothing written)\n')
    assert not DoctorSubscription.objects.filter(doctor=old).exists()
    assert run('assign_basic') == 'assign_basic: 1 doctor(s) given Basic\n'
    found = DoctorSubscription.objects.get(doctor=old)
    assert (found.tier, found.started_on, found.actor_type) == (
        'basic', date(2026, 3, 2), 'system')
    assert list(found.changes.values_list('reason', flat=True)) == ['backfill']
    assert run('assign_basic') == 'assign_basic: 0 doctor(s) given Basic\n'
    assert DoctorSubscription.objects.get(doctor=kept).version == 1
    assert DoctorSubscription.objects.count() == 2


# --- doctor_subscription ------------------------------------------------------------------

@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr('catalogue.resolver.today', lambda now=None: DAY)


@pytest.mark.django_db
def test_doctor_subscription_prints_the_record_and_effective_tier(admin, today):
    load(admin)
    user = doctor()
    out = run('doctor_subscription', str(user.uid))
    assert f'doctor {user.uid}: country ES, on 2026-11-14' in out
    assert ('record: basic, active, subscription -, started 2026-11-14, ends -, period -, '
            'auto-renew off, manual no, version 1, last change by doctor') in out
    assert 'effective: basic, subscription BAS-0, manual no, can switch yes' in out
    assert 'bea@example.com' not in out
    # By email too.
    assert run('doctor_subscription', 'BEA@example.com') == out


@pytest.mark.django_db
def test_doctor_subscription_of_a_manual_record_and_of_one_without(admin, today):
    load(admin)
    user = doctor()
    service.assign_manual(user, service.resolver.subscription_by_id(MANUAL),
                          expected_version=1, actor=service.SYSTEM_ACTOR)
    out = run('doctor_subscription', str(user.uid))
    assert 'effective: premium, subscription ORMCO (held), manual yes, can switch no' in out
    old = User.objects.create_user('old@example.com', PASSWORD)
    out = run('doctor_subscription', str(old.uid))
    assert 'record: none (counts as Basic; assign_basic gives one)' in out
    assert 'effective: basic, subscription none (offered nothing)' in out


@pytest.mark.django_db
def test_doctor_subscription_offers_and_order(admin, today):
    load(admin)
    user = doctor()
    service.change(user, expected_version=1, actor=service.SYSTEM_ACTOR, reason='test',
                   tier='advanced', subscription_id=ADVANCED)
    out = run('doctor_subscription', str(user.uid), '--offers')
    assert 'effective: advanced, subscription ADV-IB (held)' in out
    assert 'TPS-BR  TPS branded  tps_branded  94.00 EUR' in out
    out = run('doctor_subscription', str(user.uid), '--order', '--product', 'TPS-BR',
              '--quantity', '1')
    assert 'order: TPS-BR x 1, VIES not_applicable' in out
    assert 'company: ES  MyTPDSO Spain S.L.' in out
    assert 'total: 94.00 EUR' in out


@pytest.mark.django_db
def test_doctor_subscription_errors(admin, today):
    load(admin)
    user = doctor(country='SE')
    assert 'no offers' in run('doctor_subscription', str(user.uid), '--offers')
    with pytest.raises(CommandError, match=r'Refused \(product_not_offered\)'):
        run('doctor_subscription', str(user.uid), '--order', '--product', 'TPS-BR')
    with pytest.raises(CommandError, match='No such user'):
        run('doctor_subscription', 'nobody@example.com')
    with pytest.raises(CommandError, match='No product'):
        run('doctor_subscription', str(user.uid), '--order', '--product', 'NOPE')
    with pytest.raises(CommandError, match='--order goes with --product'):
        run('doctor_subscription', str(user.uid), '--order')


@pytest.mark.django_db
def test_doctor_subscription_of_a_doctor_made_staff_shows_no_record(admin, today):
    load(admin)
    user = doctor()
    user.is_staff = True
    user.save()
    out = run('doctor_subscription', str(user.uid))
    assert 'record: none (staff account: any record is ignored)' in out
    assert 'effective: basic, subscription BAS-0, manual no, can switch yes' in out
    assert DoctorSubscription.objects.filter(doctor=user).exists()
