"""Signup entry 10: the hourly purge of unfinished signups (pending rows, orphan
attempts, expired cache rows) and its job. Every signup row of the plan's I/O matrix."""
from datetime import timedelta

import pytest
from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from eventlog import service as eventlog
from eventlog.models import Attempt, Event
from legal.models import LegalChoice, LegalDocument
from mytp_publicsite import jobs
from signup import retention
from signup.models import ACTIVE_FOR, PendingRegistration

pytestmark = pytest.mark.django_db

EMAIL = 'retention.person@example.com'
FIRST_NAME = 'Retentia'
MOBILE = '+34600111222'


def _pending(age, attempt=True, legal=True):
    """A pending registration created `age` ago, with an open attempt holding personal
    data and (optionally) a legal choice."""
    attempt_obj = eventlog.start_attempt(Attempt.SIGNUP) if attempt else None
    if attempt_obj is not None:
        eventlog.record(attempt_obj, 'step_saved', step='account',
                        data={'email': EMAIL, 'first_name': FIRST_NAME})
    row = PendingRegistration.objects.create(
        email=EMAIL, password='hash', first_name=FIRST_NAME, phone=MOBILE, step='legal',
        attempt=attempt_obj)
    PendingRegistration.objects.filter(pk=row.pk).update(created_at=timezone.now() - age)
    if legal:
        document = LegalDocument.objects.create(
            kind='terms', title_en='Terms', title_es='Términos', body_en='x', body_es='x',
            published_at=timezone.now())
        LegalChoice.objects.create(document=document, accepted=True,
                                   decided_at=timezone.now(), pending_registration=row)
    row.refresh_from_db()
    return row


def _orphan(age):
    attempt = eventlog.start_attempt(Attempt.SIGNUP)
    Attempt.objects.filter(pk=attempt.pk).update(started_at=timezone.now() - age)
    return attempt


# --- Pending purge ---------------------------------------------------------------------

def test_stale_pending_is_purged_and_its_attempt_ends_timeout():
    row = _pending(ACTIVE_FOR + timedelta(minutes=1))
    assert LegalChoice.objects.filter(pending_registration=row).exists()

    assert retention.purge_pending() == 1

    assert not PendingRegistration.objects.filter(pk=row.pk).exists()
    assert not LegalChoice.objects.filter(pending_registration_id=row.pk).exists()
    attempt = Attempt.objects.get(pk=row.attempt_id)
    assert attempt.ended_at is not None
    assert attempt.outcome == 'timeout'
    purged = attempt.events.last()
    assert (purged.event, purged.reason, purged.data) == ('purged', 'timeout', None)
    # The log itself keeps its personal values until it is anonymized (eventlog.retention).
    assert attempt.events.first().data['email'] == EMAIL


def test_fresh_pending_is_untouched():
    row = _pending(ACTIVE_FOR - timedelta(minutes=1))

    assert retention.purge_pending() == 0

    assert PendingRegistration.objects.filter(pk=row.pk).exists()
    assert LegalChoice.objects.filter(pending_registration=row).exists()
    attempt = Attempt.objects.get(pk=row.attempt_id)
    assert attempt.ended_at is None
    assert not attempt.events.filter(event='purged').exists()


def test_exactly_at_the_edge_of_active_is_purged():
    now = timezone.now()
    row = _pending(timedelta(0))
    PendingRegistration.objects.filter(pk=row.pk).update(created_at=now - ACTIVE_FOR)

    assert not PendingRegistration.objects.active(now).filter(pk=row.pk).exists()
    assert retention.purge_pending(now=now) == 1


def test_now_argument_time_travels():
    row = _pending(timedelta(hours=1))

    assert retention.purge_pending(now=timezone.now() + ACTIVE_FOR) == 1
    assert not PendingRegistration.objects.filter(pk=row.pk).exists()


def test_resumed_row_keeping_an_old_created_at_is_purged():
    old = _pending(ACTIVE_FOR + timedelta(hours=2), legal=False)
    new_attempt = eventlog.start_attempt(Attempt.SIGNUP)
    resumed = old.copy_to_new_row(attempt=new_attempt)
    eventlog.end_attempt(old.attempt, 'abandoned')

    assert retention.purge_pending() == 2

    assert not PendingRegistration.objects.exists()
    assert Attempt.objects.get(pk=resumed.attempt_id).outcome == 'timeout'
    # An attempt already ended keeps its outcome and gets no purged event.
    old_attempt = Attempt.objects.get(pk=old.attempt_id)
    assert old_attempt.outcome == 'abandoned'
    assert not old_attempt.events.filter(event='purged').exists()


def test_pending_without_attempt_is_purged():
    _pending(ACTIVE_FOR + timedelta(minutes=1), attempt=False, legal=False)

    assert retention.purge_pending() == 1
    assert not PendingRegistration.objects.exists()


def test_purge_works_in_batches():
    for _ in range(5):
        _pending(ACTIVE_FOR + timedelta(minutes=1), legal=False)

    assert retention.purge_pending(batch_size=2) == 5
    assert not PendingRegistration.objects.exists()
    assert set(Attempt.objects.values_list('outcome', flat=True)) == {'timeout'}


def test_remove_pending_with_another_outcome():
    """Entry 8 (cancel) reuses it with `cancelled`."""
    row = _pending(timedelta(minutes=5))

    assert retention.remove_pending(row, 'cancelled') is True

    assert not PendingRegistration.objects.filter(pk=row.pk).exists()
    assert not LegalChoice.objects.exists()
    attempt = Attempt.objects.get(pk=row.attempt_id)
    assert attempt.outcome == 'cancelled'
    purged = attempt.events.last()
    assert (purged.event, purged.reason, purged.data) == ('purged', 'cancelled', None)
    # Already gone: nothing more happens.
    assert retention.remove_pending(row, 'cancelled') is False
    assert attempt.events.filter(event='purged').count() == 1


# --- Orphan attempts -------------------------------------------------------------------

def test_old_orphan_attempt_ends_timeout():
    attempt = _orphan(timedelta(hours=25))

    assert retention.end_orphan_attempts() == 1

    attempt.refresh_from_db()
    assert attempt.ended_at is not None
    assert attempt.outcome == 'timeout'


def test_recent_orphan_attempt_is_untouched():
    attempt = _orphan(timedelta(hours=2))

    assert retention.end_orphan_attempts() == 0

    attempt.refresh_from_db()
    assert attempt.ended_at is None


def test_old_attempt_of_a_live_row_is_untouched():
    """An attempt a fresh pending row still points to is not an orphan."""
    row = _pending(timedelta(hours=1), legal=False)
    Attempt.objects.filter(pk=row.attempt_id).update(
        started_at=timezone.now() - timedelta(hours=30))

    assert retention.end_orphan_attempts() == 0
    assert Attempt.objects.get(pk=row.attempt_id).ended_at is None


def test_orphan_rule_touches_open_signup_attempts_only():
    other_kind = Attempt.objects.create(kind='login',
                                        started_at=timezone.now() - timedelta(hours=30))
    ended = _orphan(timedelta(hours=30))
    eventlog.end_attempt(ended, 'abandoned')

    assert retention.end_orphan_attempts() == 0

    other_kind.refresh_from_db()
    ended.refresh_from_db()
    assert other_kind.ended_at is None
    assert ended.outcome == 'abandoned'


def test_orphans_in_batches():
    for _ in range(5):
        _orphan(timedelta(hours=25))

    assert retention.end_orphan_attempts(batch_size=2) == 5
    assert not Attempt.objects.filter(ended_at__isnull=True).exists()


# --- Cache clean-up --------------------------------------------------------------------

def _cache_keys():
    table = connection.ops.quote_name(settings.CACHES['default']['LOCATION'])
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT cache_key FROM {table}')
        return {key for (key,) in cursor.fetchall()}


def test_expired_cache_rows_deleted_live_rows_kept():
    cache.set('short', 1, timeout=60)
    cache.set('long', 1, timeout=3600)
    cache.set('forever', 1, timeout=None)
    assert len(_cache_keys()) == 3

    assert retention.clear_expired_cache(now=timezone.now() + timedelta(minutes=10)) == 1

    keys = _cache_keys()
    assert len(keys) == 2
    assert not any(key.endswith(':short') for key in keys)
    assert cache.get('long') == 1 and cache.get('forever') == 1
    assert retention.clear_expired_cache(now=timezone.now() + timedelta(minutes=10)) == 0


# --- Idempotence and the job -----------------------------------------------------------

def test_second_run_changes_nothing():
    _pending(ACTIVE_FOR + timedelta(minutes=1))
    _orphan(timedelta(hours=25))
    cache.set('short', 1, timeout=1)
    later = timezone.now() + timedelta(seconds=5)

    assert retention.purge_pending() == 1
    assert retention.end_orphan_attempts() == 1
    assert retention.clear_expired_cache(now=later) == 1
    events = Event.objects.count()
    ended = dict(Attempt.objects.values_list('pk', 'ended_at'))

    assert retention.purge_pending() == 0
    assert retention.end_orphan_attempts() == 0
    assert retention.clear_expired_cache(now=later) == 0
    assert Event.objects.count() == events
    assert dict(Attempt.objects.values_list('pk', 'ended_at')) == ended


def test_job_runs_the_command_and_prints_counts_only(release_jobs, capsys):
    _pending(ACTIVE_FOR + timedelta(minutes=1))
    _orphan(timedelta(hours=25))

    call_command('runjob', 'purgepending')

    out = capsys.readouterr().out
    assert ('purge_pending_signups: 1 pending registrations purged, 1 orphan attempts '
            'ended, 0 expired cache rows deleted') in out
    assert 'job purgepending: ok' in out
    for value in (EMAIL, FIRST_NAME, MOBILE):
        assert value not in out
    assert not PendingRegistration.objects.exists()


def test_jobs_conf_schedules_purge_hourly():
    job = jobs.load_jobs(jobs.JOBS_FILE)['purgepending']
    assert (job.command, job.args) == ('purge_pending_signups', ())
    assert job.crontab == '15 * * * *'
