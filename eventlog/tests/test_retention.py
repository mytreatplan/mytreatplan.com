"""Signup entry 10: the event log's retention (anonymize 30 days after an attempt ends,
delete 2 years after) and its daily job. Every event log row of the plan's I/O matrix.

Epic-login entry 8: activity records (login and page) are never anonymized and are
deleted 5 calendar years after they end, by the same daily job. Every row of its matrix
but the erased account (accounts/tests/test_erasure.py)."""
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from dateutil.relativedelta import relativedelta
from django.core.management import call_command
from django.utils import timezone

from conftest import _runjob_with
from eventlog import retention, service
from eventlog.models import Attempt, Event
from mytp_publicsite import jobs
from mytp_publicsite.tests.test_monitoring import _FakeSentry

pytestmark = pytest.mark.django_db

EMAIL = 'log.retention@example.com'
IP = '203.0.113.77'
AGENT = 'Mozilla/5.0 (RetentionBrowser)'
FIVE_YEARS = relativedelta(years=5)
DAY = timedelta(days=1)


def _attempt(ended_ago=None, kind=Attempt.SIGNUP, events=2):
    """An attempt with `events` events holding personal data, ended `ended_ago` ago
    (None: still open)."""
    attempt = service.start_attempt(kind)
    for index in range(events):
        service.record(attempt, 'field_error', step='account', field='email',
                       reason='duplicate', data={'email': EMAIL, 'ip': IP, 'n': index})
    if ended_ago is not None:
        Attempt.objects.filter(pk=attempt.pk).update(
            ended_at=timezone.now() - ended_ago, outcome='timeout',
            started_at=timezone.now() - ended_ago - timedelta(hours=1))
    attempt.refresh_from_db()
    return attempt


def _activity(kind, ended_at, events=1):
    """An activity attempt of `kind` (login or page) tied to an account, ended at
    `ended_at` (None: still open), whose events hold the IP and User-Agent."""
    event = 'login' if kind == Attempt.LOGIN else 'page_view'
    attempt = service.start_attempt(kind, account_id=4242)
    for index in range(events):
        service.record(attempt, event, data={'ip': IP, 'user_agent': AGENT, 'n': index})
    if ended_at is not None:
        Attempt.objects.filter(pk=attempt.pk).update(
            ended_at=ended_at, outcome=event, started_at=ended_at - timedelta(minutes=1))
    attempt.refresh_from_db()
    return attempt


def _intact(attempt):
    """The attempt still exists, every event with its IP and User-Agent."""
    assert Attempt.objects.filter(pk=attempt.pk).exists()
    return all(event.data['ip'] == IP and event.data['user_agent'] == AGENT
               for event in attempt.events.all()) and attempt.events.exists()


def _columns(attempt):
    return list(attempt.events.values_list('created_at', 'step', 'event', 'field', 'reason'))


# --- Anonymize -------------------------------------------------------------------------

def test_attempt_ended_30_days_ago_is_anonymized():
    attempt = _attempt(timedelta(days=30, minutes=1))
    columns = _columns(attempt)

    assert retention.anonymize() == 2

    assert all(event.data is None for event in attempt.events.all())
    assert _columns(attempt) == columns  # the rest of each event is intact
    attempt.refresh_from_db()
    assert attempt.outcome == 'timeout' and attempt.ended_at is not None


def test_attempt_ended_29_days_ago_keeps_its_data():
    attempt = _attempt(timedelta(days=29))

    assert retention.anonymize() == 0
    assert all(event.data['email'] == EMAIL for event in attempt.events.all())


def test_every_kind_but_the_activity_kinds_is_anonymized():
    """Login and page records ended 31 days ago keep their data (epic-login entry 8)."""
    other = _attempt(timedelta(days=31), kind='other', events=1)
    login = _activity(Attempt.LOGIN, timezone.now() - timedelta(days=31))
    page = _activity(Attempt.PAGE, timezone.now() - timedelta(days=31))

    assert retention.anonymize() == 1
    assert other.events.get().data is None
    assert _intact(login) and _intact(page)


def test_now_argument_time_travels():
    attempt = _attempt(timedelta(days=1))

    assert retention.anonymize(now=timezone.now() + timedelta(days=29, minutes=1)) == 2
    assert not attempt.events.filter(data__isnull=False).exists()


def test_anonymize_in_batches():
    _attempt(timedelta(days=40), events=5)
    _attempt(timedelta(days=40), events=2)

    assert retention.anonymize(batch_size=2) == 7
    assert not Event.objects.filter(data__isnull=False).exists()


# --- Delete ----------------------------------------------------------------------------

def test_attempt_ended_two_years_and_a_day_ago_is_deleted():
    old = _attempt(timedelta(days=731))
    kept = _attempt(timedelta(days=729))

    assert retention.delete_old() == (1, 2)

    assert not Attempt.objects.filter(pk=old.pk).exists()
    assert not Event.objects.filter(attempt_id=old.pk).exists()
    assert Attempt.objects.filter(pk=kept.pk).exists()
    assert kept.events.count() == 2


def test_delete_in_batches():
    for _ in range(3):
        _attempt(timedelta(days=800), events=1)

    assert retention.delete_old(batch_size=2) == (3, 3)
    assert not Attempt.objects.exists()


def test_signup_rules_leave_activity_records_of_3_years_alone():
    """Activity records follow their own 5-year rule (epic-login entry 8); signup
    attempts as before."""
    three_years = timezone.now() - relativedelta(years=3)
    login = _activity(Attempt.LOGIN, three_years)
    page = _activity(Attempt.PAGE, three_years)
    signup = _attempt(timedelta(days=800), events=1)

    assert retention.anonymize() == 1  # the signup attempt's one event
    assert retention.delete_old() == (1, 1)
    assert retention.delete_old_activity() == (0, 0)

    assert _intact(login) and _intact(page)
    assert not Attempt.objects.filter(pk=signup.pk).exists()


# --- Activity records: 5 calendar years (epic-login entry 8) ---------------------------

def test_activity_kinds_and_period():
    assert retention.ACTIVITY_KINDS == (Attempt.LOGIN, Attempt.PAGE)
    assert retention.ACTIVITY_RETENTION == relativedelta(years=5)


@pytest.mark.parametrize('kind', [Attempt.LOGIN, Attempt.PAGE])
def test_activity_record_5_years_and_a_day_old_is_deleted(kind):
    now = timezone.now()
    old = _activity(kind, now - FIVE_YEARS - DAY, events=2)
    young = _activity(kind, now - FIVE_YEARS + DAY, events=2)

    assert retention.delete_old_activity(now=now) == (1, 2)

    assert not Attempt.objects.filter(pk=old.pk).exists()
    assert not Event.objects.filter(attempt_id=old.pk).exists()
    assert _intact(young)
    assert young.events.count() == 2


def test_activity_record_ended_exactly_5_years_ago_is_deleted():
    now = timezone.now()
    attempt = _activity(Attempt.PAGE, now - FIVE_YEARS)

    assert retention.delete_old_activity(now=now) == (1, 1)
    assert not Attempt.objects.filter(pk=attempt.pk).exists()


@pytest.mark.parametrize('run_on, deleted', [
    (datetime(2033, 2, 28, 3, 45, tzinfo=dt_timezone.utc), False),
    (datetime(2033, 3, 1, 3, 45, tzinfo=dt_timezone.utc), True),
])
def test_leap_day_counts_in_calendar_years(run_on, deleted):
    attempt = _activity(Attempt.LOGIN, datetime(2028, 2, 29, 12, 0, tzinfo=dt_timezone.utc))

    assert retention.delete_old_activity(now=run_on) == ((1, 1) if deleted else (0, 0))
    assert Attempt.objects.filter(pk=attempt.pk).exists() is not deleted


def test_delete_old_activity_leaves_other_kinds_alone():
    """Signup attempts are deleted by `delete_old` only (2 years)."""
    signup = _attempt(timedelta(days=365 * 6), events=1)

    assert retention.delete_old_activity() == (0, 0)
    assert Attempt.objects.filter(pk=signup.pk).exists()


def test_delete_old_activity_in_batches():
    for kind in (Attempt.LOGIN, Attempt.PAGE, Attempt.LOGIN):
        _activity(kind, timezone.now() - relativedelta(years=6))

    assert retention.delete_old_activity(batch_size=2) == (3, 3)
    assert not Attempt.objects.exists()


# --- Open attempts ---------------------------------------------------------------------

def test_open_old_attempt_is_never_anonymized_or_deleted():
    attempt = _attempt(None)
    Attempt.objects.filter(pk=attempt.pk).update(started_at=timezone.now() - timedelta(days=1000))
    Event.objects.filter(attempt=attempt).update(created_at=timezone.now() - timedelta(days=1000))

    assert retention.anonymize() == 0
    assert retention.delete_old() == (0, 0)

    assert all(event.data['email'] == EMAIL for event in attempt.events.all())


@pytest.mark.parametrize('kind', [Attempt.LOGIN, Attempt.PAGE])
def test_open_old_activity_attempt_is_never_deleted(kind):
    attempt = _activity(kind, None)
    Attempt.objects.filter(pk=attempt.pk).update(
        started_at=timezone.now() - relativedelta(years=10))

    assert retention.delete_old_activity() == (0, 0)
    assert retention.delete_old_activity(now=timezone.now() + relativedelta(years=20)) == (0, 0)
    assert _intact(attempt)


# --- Idempotence and the job -----------------------------------------------------------

def test_second_run_changes_nothing():
    _attempt(timedelta(days=31))
    _attempt(timedelta(days=731))

    assert retention.anonymize() == 4
    assert retention.delete_old() == (1, 2)

    assert retention.anonymize() == 0
    assert retention.delete_old() == (0, 0)
    assert Event.objects.count() == 2


def test_second_activity_run_changes_nothing():
    _activity(Attempt.LOGIN, timezone.now() - FIVE_YEARS - DAY)
    young = _activity(Attempt.PAGE, timezone.now() - FIVE_YEARS + DAY)

    assert retention.delete_old_activity() == (1, 1)
    assert retention.delete_old_activity() == (0, 0)
    assert _intact(young)


def test_job_runs_the_command_and_prints_counts_only(release_jobs, capsys):
    _attempt(timedelta(days=31))
    _attempt(timedelta(days=731), events=1)

    call_command('runjob', 'eventlogretention')

    out = capsys.readouterr().out
    assert 'eventlog_retention: 3 events anonymized, 1 attempts deleted (1 events)' in out
    assert 'job eventlogretention: ok' in out
    assert EMAIL not in out and IP not in out
    assert not Event.objects.filter(data__isnull=False).exists()


def test_job_applies_the_activity_rule_and_prints_counts_only(release_jobs, capsys):
    _attempt(timedelta(days=31), events=1)
    old_login = _activity(Attempt.LOGIN, timezone.now() - FIVE_YEARS - DAY, events=2)
    old_page = _activity(Attempt.PAGE, timezone.now() - FIVE_YEARS - DAY)
    young = _activity(Attempt.PAGE, timezone.now() - FIVE_YEARS + DAY)

    call_command('runjob', 'eventlogretention')

    out = capsys.readouterr().out
    assert ('eventlog_retention: 1 events anonymized, 0 attempts deleted (0 events), '
            '2 activity attempts deleted (3 events)') in out
    assert 'job eventlogretention: ok' in out
    for secret in (EMAIL, IP, AGENT):
        assert secret not in out
    assert not Attempt.objects.filter(pk__in=[old_login.pk, old_page.pk]).exists()
    assert _intact(young)
    [event] = Event.objects.filter(attempt_id=young.pk)
    assert (event.data['ip'], event.data['user_agent']) == (IP, AGENT)
    assert set(Attempt.objects.values_list('kind', flat=True)) == {Attempt.SIGNUP, Attempt.PAGE}


def test_job_checks_in_to_its_existing_monitor(monkeypatch, settings):
    settings.SENTRY_ENVIRONMENT = 'staging'
    fake = _FakeSentry(active=True)
    checkins = _runjob_with(monkeypatch, fake)
    _activity(Attempt.LOGIN, timezone.now() - FIVE_YEARS - DAY)

    call_command('runjob', 'eventlogretention')

    assert [c['status'] for c in checkins] == ['in_progress', 'ok']
    assert {c['monitor_slug'] for c in checkins} == {'mytp-staging-eventlogretention'}
    assert not Attempt.objects.exists()


def test_job_failure_checks_in_error(monkeypatch, settings):
    settings.SENTRY_ENVIRONMENT = 'staging'
    fake = _FakeSentry(active=True)
    checkins = _runjob_with(monkeypatch, fake)

    def fail(*args, **kwargs):
        raise RuntimeError('activity retention failed')
    monkeypatch.setattr(retention, 'delete_old_activity', fail)

    with pytest.raises(RuntimeError):
        call_command('runjob', 'eventlogretention')

    assert [c['status'] for c in checkins] == ['in_progress', 'error']
    assert fake.exceptions == 1


def test_jobs_conf_schedules_retention_daily():
    job = jobs.load_jobs(jobs.JOBS_FILE)['eventlogretention']
    assert (job.command, job.args) == ('eventlog_retention', ())
    assert job.crontab == '45 3 * * *'
