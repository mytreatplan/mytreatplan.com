"""Background task worker (AD-8, epic-platform entry 9): the django-tasks-db backend on
Django 6.1, run by its own worker (`db_worker --batch`), with the mytp-core helpers
(enqueue after commit, idempotent effects, recovery of interrupted tasks) and the worker
heartbeat. One test per row of the story's I/O matrix that can run in CI; the server
rows (systemd restarts, the release, Sentry alerting) are checked on staging."""
import io
import logging
import re
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from django.tasks import TaskResultStatus, task_backends
from django.utils import timezone
from django_tasks_db.backend import DatabaseBackend
from django_tasks_db.models import DBTaskResult

from mytp_core import jobs
from mytp_core import tasks as core_tasks
from mytp_core.models import TaskEffect
from mytp_publicsite.models import ContactMessage
from mytp_publicsite.tasks import SAMPLE_EFFECT, sample_task

REPO = Path(settings.BASE_DIR)
EFFECT_LINE = 'sample task effect for {}'


def run_worker():
    """What mytp-<site>-worker does (`manage.py db_worker`), until the queue is empty."""
    call_command('db_worker', '--batch', '--no-startup-delay', '--no-reload', verbosity=0)


@pytest.fixture
def effect_log(caplog):
    caplog.set_level(logging.INFO, logger='mytp_publicsite.tasks')

    def count(record):
        return sum(r.getMessage() == EFFECT_LINE.format(record) for r in caplog.records)
    return count


def save_message():
    return ContactMessage.objects.create(first_name='Ana', surname='Ruiz', country='Spain',
                                         phone='+34600000000', email='ana@example.com')


def test_the_site_runs_django_tasks_db():
    assert isinstance(task_backends['default'], DatabaseBackend)
    assert 'django_tasks_db' in settings.INSTALLED_APPS


# --- Enqueue after commit -------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_a_task_enqueued_with_the_saved_record_runs_once_after_commit(effect_log):
    with transaction.atomic():
        message = save_message()
        core_tasks.enqueue_on_commit(sample_task, str(message.uid))
        assert not DBTaskResult.objects.exists()

    queued = DBTaskResult.objects.get()
    assert queued.task_path == 'mytp_publicsite.tasks.sample_task'
    # The record's UUID only, never personal values.
    assert queued.args_kwargs == {'args': [str(message.uid)], 'kwargs': {}}

    run_worker()

    queued.refresh_from_db()
    assert queued.status == TaskResultStatus.SUCCESSFUL
    assert queued.return_value is True
    assert effect_log(message.uid) == 1


@pytest.mark.django_db(transaction=True)
def test_nothing_runs_when_the_saving_transaction_rolls_back(effect_log):
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            message = save_message()
            core_tasks.enqueue_on_commit(sample_task, str(message.uid))
            raise RuntimeError('the view failed')

    run_worker()

    assert not ContactMessage.objects.exists()
    assert not DBTaskResult.objects.exists()
    assert effect_log(message.uid) == 0


@pytest.mark.django_db(transaction=True)
def test_enqueue_sample_task_command(effect_log):
    record = uuid.uuid4()
    out = io.StringIO()

    call_command('enqueue_sample_task', '--record', str(record), stdout=out)
    call_command('enqueue_sample_task', '--record', str(record), stdout=out)
    run_worker()

    assert out.getvalue().count(f'sample task enqueued for {record}') == 2
    assert sorted(r.return_value for r in DBTaskResult.objects.all()) == [False, True]
    assert effect_log(record) == 1


# --- Rerun and periodic effects ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_rerun_after_the_worker_was_killed_past_the_effect_has_one_effect(effect_log):
    """The worker ran the effect (committed), then was killed before it marked the task
    finished. The next start (ExecStartPre) requeues the task; the rerun skips the effect."""
    record = str(uuid.uuid4())
    result = sample_task.enqueue(record)
    assert sample_task.call(record) is True
    DBTaskResult.objects.filter(id=result.id).update(
        status=TaskResultStatus.RUNNING, started_at=timezone.now(), worker_ids=['killed'])

    call_command('requeue_interrupted_tasks', stdout=io.StringIO())
    run_worker()

    row = DBTaskResult.objects.get(id=result.id)
    assert row.status == TaskResultStatus.SUCCESSFUL
    assert row.return_value is False
    assert effect_log(record) == 1
    assert TaskEffect.objects.filter(effect=SAMPLE_EFFECT, record=record).count() == 1


@pytest.mark.django_db(transaction=True)
def test_a_task_interrupted_before_its_effect_runs_after_the_restart(effect_log):
    record = str(uuid.uuid4())
    result = sample_task.enqueue(record)
    DBTaskResult.objects.filter(id=result.id).update(status=TaskResultStatus.RUNNING)

    run_worker()  # the backend never picks up a RUNNING task by itself
    assert effect_log(record) == 0

    call_command('requeue_interrupted_tasks', stdout=io.StringIO())
    run_worker()

    assert DBTaskResult.objects.get(id=result.id).return_value is True
    assert effect_log(record) == 1


@pytest.mark.django_db
def test_periodic_effect_once_per_record_and_period():
    record = uuid.uuid4()
    done = []

    for period in ('2026-11', '2026-11', '2026-12'):
        core_tasks.run_once('renewal.test', record, lambda p=period: done.append(p),
                            period=period)

    assert done == ['2026-11', '2026-12']
    assert core_tasks.effect_done('renewal.test', record, period='2026-11')
    assert not core_tasks.effect_done('renewal.test', record, period='2027-01')


# --- Heartbeat and pruning (jobs.conf) ----------------------------------------------------------

def test_jobs_conf_enqueues_the_heartbeat_every_5_minutes_and_prunes_daily():
    declared = jobs.load_jobs(jobs.release_jobs_file())

    heartbeat = declared['workerheartbeat']
    assert (heartbeat.command, heartbeat.args) == ('enqueue_worker_heartbeat', ())
    assert heartbeat.crontab == '*/5 * * * *'

    prune = declared['prunetasks']
    assert prune.command == 'prune_db_task_results'
    assert prune.args == ('--queue-name', '*', '--min-age-days', '14',
                          '--failed-min-age-days', '30')
    assert re.fullmatch(r'\d+ \d+ \* \* \*', prune.crontab)


@pytest.mark.django_db(transaction=True)
def test_heartbeat_job_makes_the_worker_check_in_on_its_monitor(monkeypatch, settings):
    settings.SITE = 'dev'
    checkins = []

    class Sentry:
        @staticmethod
        def get_client():
            return Sentry

        @staticmethod
        def is_active():
            return True

        @staticmethod
        def flush(timeout=None):
            pass

    monkeypatch.setattr(core_tasks, 'sentry_sdk', Sentry)
    monkeypatch.setattr(core_tasks, 'capture_checkin', lambda **kw: checkins.append(kw))

    call_command('enqueue_worker_heartbeat', stdout=io.StringIO())
    assert checkins == []  # the job only enqueues; the worker checks in
    run_worker()

    assert [c['monitor_slug'] for c in checkins] == ['mytp-dev-worker']
    assert checkins[0]['status'] == 'ok'
    # The monitor expects a check-in as often as the job enqueues one.
    assert checkins[0]['monitor_config']['schedule'] == {'type': 'crontab',
                                                         'value': '*/5 * * * *'}


@pytest.mark.django_db
def test_prune_job_deletes_old_finished_results_only():
    old_done = sample_task.enqueue(str(uuid.uuid4()))
    old_failed = sample_task.enqueue(str(uuid.uuid4()))
    recent_done = sample_task.enqueue(str(uuid.uuid4()))
    waiting = sample_task.enqueue(str(uuid.uuid4()))
    now = timezone.now()
    DBTaskResult.objects.filter(id=old_done.id).update(
        status=TaskResultStatus.SUCCESSFUL, finished_at=now - timedelta(days=15))
    DBTaskResult.objects.filter(id=old_failed.id).update(
        status=TaskResultStatus.FAILED, finished_at=now - timedelta(days=15))
    DBTaskResult.objects.filter(id=recent_done.id).update(
        status=TaskResultStatus.SUCCESSFUL, finished_at=now - timedelta(days=1))

    job = jobs.load_jobs(jobs.release_jobs_file())['prunetasks']
    call_command(job.command, *job.args, stdout=io.StringIO())

    left = {str(pk) for pk in DBTaskResult.objects.values_list('id', flat=True)}
    # Failed results stay 30 days, for the investigation.
    assert left == {old_failed.id, recent_done.id, waiting.id}


# --- AD-8: task code never imports the backend -------------------------------------------------

def test_task_modules_import_only_django_tasks_and_the_helpers():
    modules = [p for p in REPO.glob('*/tasks.py') if 'node_modules' not in p.parts]
    modules.append(Path(core_tasks.__file__))
    assert REPO / 'mytp_publicsite' / 'tasks.py' in modules
    for path in modules:
        text = path.read_text()
        assert 'django_tasks_db' not in text, path
        assert 'celery' not in text.lower() and 'redis' not in text.lower(), path


def test_task_states_and_failures_reach_the_journal_with_debug_off():
    """`django.tasks` has its own console handler without the debug-only filter, so with
    DEBUG off the worker's journal still shows each task's state and a failure's
    traceback (db_worker adds no handler once an ancestor has one)."""
    tasks_logger = logging.getLogger('django.tasks')
    assert tasks_logger.propagate is False
    handlers = [h for h in tasks_logger.handlers if isinstance(h, logging.StreamHandler)]
    assert handlers and not any(h.filters for h in handlers)
    assert settings.LOGGING['loggers']['django.tasks']['level'] == 'INFO'
