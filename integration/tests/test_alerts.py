"""The interface health check (epic-admin-interface entry 10, I4 and I5):
`integration.alerts.check`, the `check_interface` command and its job, the threshold
settings' checks. Outbox rows and the pull's state are written directly: the check only
reads them. No test reaches the network."""
import io
import json
import logging
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
import sentry_sdk
from django.conf import settings as django_settings
from django.core.management import call_command
from django.utils import timezone

from integration import alerts
from integration.checks import check_interface_settings
from integration.models import ConfigState, InterfaceAlert, OutboxEvent

DEPLOY = Path(django_settings.BASE_DIR) / 'deploy'
SECRET = 'payload-value-that-must-not-leak'
AGGREGATE = uuid.UUID('6c1f0a3e-2b4d-4e5f-8a9b-0c1d2e3f4a5b')


@pytest.fixture(autouse=True)
def thresholds(settings):
    settings.OUTBOX_ALERT_MINUTES = 15
    settings.CONFIG_ALERT_MINUTES = 60
    settings.INTERFACE_PEERS = 'admindev=https://admindev.example.test'
    return settings


def event(minutes_ago, state=OutboxEvent.PENDING, sequence=None):
    now = timezone.now()
    sequence = sequence or OutboxEvent.objects.count() + 1
    return OutboxEvent.objects.create(
        type='client.updated', aggregate_id=AGGREGATE, aggregate_version=sequence,
        sequence=sequence, region='eu', payload={'email': SECRET}, state=state,
        attempts=3, last_error='network: no answer from https://admindev.example.test',
        created_at=now - timedelta(minutes=minutes_ago),
        occurred_at=now - timedelta(minutes=minutes_ago))


def pulled(minutes_ago, *, never=False):
    now = timezone.now()
    ConfigState.objects.update_or_create(pk=ConfigState.SINGLETON, defaults={
        'version': None if never else 3, 'last_error': 'network',
        'last_success_at': None if never else now - timedelta(minutes=minutes_ago),
        'created_at': now - timedelta(minutes=minutes_ago)})


def outcomes(callbacks):
    with callbacks(execute=True):
        return {f.kind: f.outcome for f in alerts.check()}


def snapshot_of_events():
    return list(OutboxEvent.objects.order_by('id').values_list(
        'state', 'attempts', 'next_attempt_at', 'last_error', 'failing_since', 'warned_at',
        'delivered_at'))


@pytest.mark.django_db
def test_a_stuck_outbox_warns_once_until_it_delivers(sentry_capture, caplog,
                                                     django_capture_on_commit_callbacks):
    caplog.set_level(logging.INFO)
    pulled(1)
    first = event(14)
    event(3)
    assert outcomes(django_capture_on_commit_callbacks)[alerts.OUTBOX] == alerts.OK
    assert sentry_capture.events == []
    OutboxEvent.objects.filter(pk=first.pk).update(
        created_at=timezone.now() - timedelta(minutes=20))
    before = snapshot_of_events()
    assert outcomes(django_capture_on_commit_callbacks)[alerts.OUTBOX] == alerts.WARNED
    for _ in range(3):
        assert (outcomes(django_capture_on_commit_callbacks)[alerts.OUTBOX]
                == alerts.STILL_FAILING)
    assert len(sentry_capture.events) == 1
    sent = sentry_capture.events[0]
    assert sent['level'] == 'warning'
    assert sent['tags']['interface.alert'] == 'outbox'
    assert sent['fingerprint'] == ['interface-alert', 'outbox']
    message = sent.get('message') or sent['logentry']['message']
    assert '2 event(s) pending, the oldest for 20 minutes' in message
    # Counts and ages only: no payload, ids, URLs or error text.
    dumped = json.dumps(sent, default=str)
    for leak in (SECRET, str(AGGREGATE), str(first.event_id), 'https://', 'admindev',
                 'network'):
        assert leak not in dumped
    for leak in (SECRET, str(AGGREGATE), str(first.event_id), 'https://'):
        assert leak not in caplog.text
    # The check never sends or changes an event.
    assert snapshot_of_events() == before
    # Delivered: reset; a later outage warns again.
    OutboxEvent.objects.update(state=OutboxEvent.DELIVERED, delivered_at=timezone.now())
    assert outcomes(django_capture_on_commit_callbacks)[alerts.OUTBOX] == alerts.RECOVERED
    assert InterfaceAlert.objects.get(kind='outbox').warned_at is None
    assert 'interface alert cleared: outbox' in caplog.text
    assert outcomes(django_capture_on_commit_callbacks)[alerts.OUTBOX] == alerts.OK
    event(16)
    assert outcomes(django_capture_on_commit_callbacks)[alerts.OUTBOX] == alerts.WARNED
    assert len(sentry_capture.events) == 2


@pytest.mark.django_db
def test_rejected_and_delivered_events_do_not_count(sentry_capture,
                                                    django_capture_on_commit_callbacks):
    pulled(1)
    event(500, state=OutboxEvent.REJECTED)
    event(500, state=OutboxEvent.DELIVERED)
    assert outcomes(django_capture_on_commit_callbacks) == {'outbox': 'ok', 'config': 'ok'}
    assert sentry_capture.events == []
    # A healthy run writes no state row.
    assert not InterfaceAlert.objects.exists()


@pytest.mark.django_db
def test_a_stale_config_warns_once_until_a_good_pull(sentry_capture,
                                                     django_capture_on_commit_callbacks):
    pulled(59)
    assert outcomes(django_capture_on_commit_callbacks)[alerts.CONFIG] == alerts.OK
    pulled(61)
    assert outcomes(django_capture_on_commit_callbacks)[alerts.CONFIG] == alerts.WARNED
    assert (outcomes(django_capture_on_commit_callbacks)[alerts.CONFIG]
            == alerts.STILL_FAILING)
    assert len(sentry_capture.events) == 1
    sent = sentry_capture.events[0]
    assert sent['tags']['interface.alert'] == 'config'
    message = sent.get('message') or sent['logentry']['message']
    assert 'last good pull 61 minutes ago' in message
    assert 'network' not in json.dumps(sent, default=str)
    # The check never pulls or changes the pull's state.
    row = ConfigState.objects.get()
    assert row.warned_at is None and row.last_error == 'network'
    pulled(2)
    assert outcomes(django_capture_on_commit_callbacks)[alerts.CONFIG] == alerts.RECOVERED
    pulled(90)
    assert outcomes(django_capture_on_commit_callbacks)[alerts.CONFIG] == alerts.WARNED
    assert len(sentry_capture.events) == 2


@pytest.mark.django_db
def test_a_site_that_never_pulled_well_warns_after_its_first_attempt(
        sentry_capture, django_capture_on_commit_callbacks):
    pulled(30, never=True)
    assert outcomes(django_capture_on_commit_callbacks)[alerts.CONFIG] == alerts.OK
    pulled(61, never=True)
    assert outcomes(django_capture_on_commit_callbacks)[alerts.CONFIG] == alerts.WARNED
    message = sentry_capture.events[0].get('message') or \
        sentry_capture.events[0]['logentry']['message']
    assert 'no good pull yet, first attempt 61 minutes ago' in message


@pytest.mark.django_db
def test_no_pull_tried_warns_once_after_the_threshold_from_the_first_check(
        sentry_capture, django_capture_on_commit_callbacks):
    assert outcomes(django_capture_on_commit_callbacks)[alerts.CONFIG] == alerts.NOT_CHECKED
    row = InterfaceAlert.objects.get(kind='config')
    first_seen = row.since
    assert first_seen is not None and row.warned_at is None
    assert outcomes(django_capture_on_commit_callbacks)[alerts.CONFIG] == alerts.NOT_CHECKED
    assert InterfaceAlert.objects.get(kind='config').since == first_seen  # kept, not moved
    InterfaceAlert.objects.filter(kind='config').update(
        since=timezone.now() - timedelta(minutes=61))
    assert outcomes(django_capture_on_commit_callbacks)[alerts.CONFIG] == alerts.WARNED
    assert (outcomes(django_capture_on_commit_callbacks)[alerts.CONFIG]
            == alerts.STILL_FAILING)
    assert len(sentry_capture.events) == 1
    sent = sentry_capture.events[0]
    message = sent.get('message') or sent['logentry']['message']
    assert 'no pull tried yet, first seen 61 minutes ago' in message
    # The pull's state is never written by the check.
    assert not ConfigState.objects.exists()
    # A good pull: cleared, `since` too.
    pulled(1)
    assert outcomes(django_capture_on_commit_callbacks)[alerts.CONFIG] == alerts.RECOVERED
    row = InterfaceAlert.objects.get(kind='config')
    assert row.warned_at is None and row.since is None


@pytest.mark.django_db
def test_no_second_config_warning_when_the_pull_already_warned(
        sentry_capture, django_capture_on_commit_callbacks):
    pulled(90)
    ConfigState.objects.update(warned_at=timezone.now() - timedelta(minutes=30))
    before = list(ConfigState.objects.values())
    for _ in range(2):
        assert (outcomes(django_capture_on_commit_callbacks)[alerts.CONFIG]
                == alerts.STILL_FAILING)
    assert sentry_capture.events == []
    assert list(ConfigState.objects.values()) == before  # read only
    # A good pull clears both; the next outage the pull has not warned for warns here.
    pulled(1)
    ConfigState.objects.update(warned_at=None)
    assert outcomes(django_capture_on_commit_callbacks)[alerts.CONFIG] == alerts.RECOVERED
    pulled(61)
    assert outcomes(django_capture_on_commit_callbacks)[alerts.CONFIG] == alerts.WARNED
    assert len(sentry_capture.events) == 1


@pytest.mark.django_db
def test_a_row_created_by_another_run_meanwhile_is_locked_and_used(
        monkeypatch, sentry_capture, django_capture_on_commit_callbacks):
    """`_locked_alert`'s IntegrityError branch: the row did not exist when looked up, but
    another run created it before this one could; that row is used."""
    pulled(1)
    event(30)
    InterfaceAlert.objects.create(kind='outbox')
    real = alerts._find_locked
    calls = []

    def first_miss(kind):
        calls.append(kind)
        return None if len(calls) == 1 else real(kind)

    monkeypatch.setattr(alerts, '_find_locked', first_miss)
    assert outcomes(django_capture_on_commit_callbacks)[alerts.OUTBOX] == alerts.WARNED
    assert InterfaceAlert.objects.filter(kind='outbox').count() == 1
    assert InterfaceAlert.objects.get(kind='outbox').warned_at is not None
    assert len(sentry_capture.events) == 1


@pytest.mark.django_db
def test_the_command_flushes_sentry(monkeypatch):
    flushed = []
    monkeypatch.setattr(sentry_sdk, 'flush', lambda timeout=None: flushed.append(timeout))
    call_command('check_interface', stdout=io.StringIO())
    assert flushed == [5]


@pytest.mark.django_db
def test_the_two_kinds_warn_independently(sentry_capture, django_capture_on_commit_callbacks):
    pulled(120)
    event(30)
    assert outcomes(django_capture_on_commit_callbacks) == {'outbox': 'warned',
                                                            'config': 'warned'}
    pulled(1)
    assert outcomes(django_capture_on_commit_callbacks) == {'outbox': 'still_failing',
                                                            'config': 'recovered'}
    assert sorted(e['tags']['interface.alert'] for e in sentry_capture.events) == [
        'config', 'outbox']


@pytest.mark.django_db
def test_thresholds_come_from_the_settings(settings, sentry_capture,
                                           django_capture_on_commit_callbacks):
    settings.OUTBOX_ALERT_MINUTES = 2
    settings.CONFIG_ALERT_MINUTES = 5
    pulled(6)
    event(3)
    assert outcomes(django_capture_on_commit_callbacks) == {'outbox': 'warned',
                                                            'config': 'warned'}
    messages = sorted(e.get('message') or e['logentry']['message']
                      for e in sentry_capture.events)
    assert messages[0].startswith('Configuration snapshot not pulled for over 5 minutes')
    assert messages[1].startswith('Interface outbox not delivering for over 2 minutes')


@pytest.mark.django_db
def test_the_warning_is_sent_only_after_the_commit(sentry_capture,
                                                   django_capture_on_commit_callbacks):
    pulled(1)
    event(30)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        alerts.check()
    assert sentry_capture.events == [] and len(callbacks) == 1
    assert InterfaceAlert.objects.get(kind='outbox').warned_at is not None


@pytest.mark.django_db
def test_the_command_prints_counts_and_ages_only(sentry_capture):
    pulled(3)
    stuck = event(40)
    out = io.StringIO()
    call_command('check_interface', stdout=out)
    lines = out.getvalue().splitlines()
    assert lines == [
        'check_interface: outbox warned (1 event(s) pending, the oldest for 40 minutes; '
        'threshold 15 minutes)',
        'check_interface: config ok (last good pull 3 minutes ago; threshold 60 minutes)',
    ]
    assert str(stuck.event_id) not in out.getvalue()


@pytest.mark.django_db
def test_the_job_runs_every_5_minutes_off_the_pull():
    from mytp_core import jobs
    job = jobs.load_jobs(DEPLOY / 'jobs.conf')['checkinterface']
    assert (job.command, job.args) == ('check_interface', ())
    assert job.schedule == '*-*-* *:02/5:00 UTC'
    assert jobs.oncalendar_to_crontab(job.schedule) == '2-59/5 * * * *'


@pytest.mark.parametrize('name, value, ok', [
    ('OUTBOX_ALERT_MINUTES', 15, True), ('OUTBOX_ALERT_MINUTES', 1, True),
    ('OUTBOX_ALERT_MINUTES', 0, False), ('OUTBOX_ALERT_MINUTES', -5, False),
    ('OUTBOX_ALERT_MINUTES', 2.5, False), ('OUTBOX_ALERT_MINUTES', True, False),
    ('OUTBOX_ALERT_MINUTES', '15', False),
    ('CONFIG_ALERT_MINUTES', 60, True), ('CONFIG_ALERT_MINUTES', 0, False),
    ('CONFIG_ALERT_MINUTES', None, False),
])
def test_the_thresholds_are_checked(settings, name, value, ok):
    setattr(settings, name, value)
    check_id = {'OUTBOX_ALERT_MINUTES': 'integration.E008',
                'CONFIG_ALERT_MINUTES': 'integration.E009'}[name]
    errors = [e for e in check_interface_settings() if e.id in ('integration.E008',
                                                                'integration.E009')]
    assert [e.id for e in errors] == ([] if ok else [check_id])
    if not ok:
        assert name in errors[0].msg
