"""Signup entry 10: the event log's retention (anonymize 30 days after an attempt ends,
delete 2 years after) and its daily job. Every event log row of the plan's I/O matrix."""
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from eventlog import retention, service
from eventlog.models import Attempt, Event
from mytp_publicsite import jobs

pytestmark = pytest.mark.django_db

EMAIL = 'log.retention@example.com'
IP = '203.0.113.77'


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


def test_every_kind_is_anonymized():
    login = _attempt(timedelta(days=31), kind='login', events=1)

    assert retention.anonymize() == 1
    assert login.events.get().data is None


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
        _attempt(timedelta(days=800), kind='login', events=1)

    assert retention.delete_old(batch_size=2) == (3, 3)
    assert not Attempt.objects.exists()


# --- Open attempts ---------------------------------------------------------------------

def test_open_old_attempt_is_never_anonymized_or_deleted():
    attempt = _attempt(None)
    Attempt.objects.filter(pk=attempt.pk).update(started_at=timezone.now() - timedelta(days=1000))
    Event.objects.filter(attempt=attempt).update(created_at=timezone.now() - timedelta(days=1000))

    assert retention.anonymize() == 0
    assert retention.delete_old() == (0, 0)

    assert all(event.data['email'] == EMAIL for event in attempt.events.all())


# --- Idempotence and the job -----------------------------------------------------------

def test_second_run_changes_nothing():
    _attempt(timedelta(days=31))
    _attempt(timedelta(days=731))

    assert retention.anonymize() == 4
    assert retention.delete_old() == (1, 2)

    assert retention.anonymize() == 0
    assert retention.delete_old() == (0, 0)
    assert Event.objects.count() == 2


@pytest.fixture
def release_jobs(monkeypatch, tmp_path):
    """runjob reads the release's deploy/jobs.conf (no provisioned copy)."""
    monkeypatch.setattr(jobs, 'INSTALLED_JOBS_FILE', tmp_path / 'missing.conf')


def test_job_runs_the_command_and_prints_counts_only(release_jobs, capsys):
    _attempt(timedelta(days=31))
    _attempt(timedelta(days=731), events=1)

    call_command('runjob', 'eventlogretention')

    out = capsys.readouterr().out
    assert 'eventlog_retention: 3 events anonymized, 1 attempts deleted (1 events)' in out
    assert 'job eventlogretention: ok' in out
    assert EMAIL not in out and IP not in out
    assert not Event.objects.filter(data__isnull=False).exists()


def test_jobs_conf_schedules_retention_daily():
    job = jobs.load_jobs(jobs.JOBS_FILE)['eventlogretention']
    assert (job.command, job.args) == ('eventlog_retention', ())
    assert job.crontab == '45 3 * * *'
