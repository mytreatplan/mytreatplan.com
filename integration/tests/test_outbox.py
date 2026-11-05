"""The outbox (epic-admin-interface entry 3): `outbox.record`, the dispatcher (job, command
and nudge task), the purge, the staging sample and its system check. The admin site is a
fake at mytp_core.interface._open that checks the signature (portal key, fixed non-secret
test seed) and answers as the real ingest does (dedupe on event_id, versions per
aggregate, the region check). No test reaches the network or a KMS."""
import base64
import io
import json
import logging
import threading
import urllib.error
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.conf import settings as django_settings
from django.core.management import CommandError, call_command
from django.db import connection, transaction
from django.utils import timezone

from integration import outbox
from integration.models import OutboxEvent
from mytp_core import interface
from mytp_core.contracts import (ContractViolation, EventEnvelope, InvalidPayload,
                                 UnknownEventType, check_central_payload)

DEPLOY = Path(django_settings.BASE_DIR) / 'deploy'
PORTAL_SEED = b'test-only-interface-seed-portal!'
PORTAL = interface.SigningKey('p1', PORTAL_SEED)
SAMPLE = 'sample.recorded'
EVENTS_URL = 'https://admindev.example.test/api/v1/events'
SECRET_LABEL = 'label-that-must-not-leak'


@pytest.fixture(autouse=True)
def interface_settings(settings):
    settings.SITE = 'dev'
    settings.REGION = 'eu'
    settings.INTERFACE_SIGNING_KEY = f'p1:{base64.b64encode(PORTAL_SEED).decode()}'
    settings.INTERFACE_PEERS = 'admindev=https://admindev.example.test'
    settings.INTERFACE_ADMIN_SITE = ''
    interface.reset_signing_key()
    yield settings
    interface.reset_signing_key()


class FakeAdmin:
    """The admin's POST /api/v1/events: verifies the signature over the body, then
    region check, dedupe on event_id, version per aggregate. `down` makes it unreachable;
    `fail_for` maps an event id to an HTTP status it answers instead."""

    def __init__(self):
        self.ring = interface.Keyring.from_entries([('dev', 'p1', PORTAL.public_text)])
        self.regions = {'dev': 'eu', 'dev-ae': 'ae'}
        self.seen = set()
        self.versions = {}
        self.applied = []
        self.sent = []
        self.down = False
        self.fail_for = {}
        self.lock = threading.Lock()

    def answer(self, data, status=200):
        response = io.BytesIO(json.dumps(data).encode())
        response.status = status
        return response

    def __call__(self, request, timeout):
        if self.down:
            raise urllib.error.URLError(ConnectionRefusedError(61, 'refused'))
        parts = urlsplit(request.full_url)
        assert request.full_url == EVENTS_URL and request.method == 'POST'
        claims = interface.verify_request(
            request.get_header('Authorization'), 'POST',
            interface.request_target(parts.path, parts.query), request.data,
            keyring=self.ring, audience='admindev')
        envelope = EventEnvelope.model_validate_json(request.data)
        check_central_payload(envelope.payload)
        event_id = str(envelope.event_id)
        with self.lock:
            self.sent.append(event_id)
            status = self.fail_for.get(event_id)
            if status:
                raise urllib.error.HTTPError(request.full_url, status, 'x', {},
                                             io.BytesIO(b'{}'))
            if self.regions.get(claims['iss']) != envelope.region:
                self.seen.add(event_id)
                return self.answer({'result': 'rejected', 'reason': 'region_mismatch'})
            if event_id in self.seen:
                return self.answer({'result': 'duplicate'})
            self.seen.add(event_id)
            key = (envelope.type, str(envelope.aggregate_id))
            if envelope.aggregate_version <= self.versions.get(key, 0):
                return self.answer({'result': 'stale'})
            self.versions[key] = envelope.aggregate_version
            self.applied.append(event_id)
            return self.answer({'result': 'accepted'})


@pytest.fixture
def admin(monkeypatch):
    fake = FakeAdmin()
    monkeypatch.setattr(interface, '_open', fake)
    return fake


def emit(aggregate=None, version=1, label='hello', **kwargs):
    with transaction.atomic():
        return outbox.record(SAMPLE, aggregate or uuid.uuid4(), version, {'label': label},
                             **kwargs)


def make_due():
    OutboxEvent.objects.update(next_attempt_at=timezone.now() - timedelta(seconds=1))


def state(event):
    event.refresh_from_db()
    return event.state


# --- Writing ----------------------------------------------------------------------------

@pytest.mark.django_db
def test_record_writes_a_pending_row_with_sequences_per_aggregate():
    a, b = uuid.uuid4(), uuid.uuid4()
    first = emit(a, 1)
    second = emit(a, 2)
    other = emit(b, 7)
    assert (first.sequence, second.sequence, other.sequence) == (1, 2, 1)
    row = OutboxEvent.objects.get(pk=first.pk)
    assert (row.type, row.version, row.aggregate_id, row.aggregate_version, row.region,
            row.payload, row.state, row.attempts, row.last_error) == \
        (SAMPLE, 1, a, 1, 'eu', {'label': 'hello'}, 'pending', 0, '')
    assert row.event_id.version == 4 and row.delivered_at is None


@pytest.mark.django_db
@pytest.mark.parametrize('payload, error', [
    ({'label': 'x', 'extra': 1}, InvalidPayload),
    ({'label': 'x', 'patient_name': 'Ana Ruiz'}, InvalidPayload),
    ({}, InvalidPayload),
])
def test_invalid_payload_raises_and_rolls_back_the_callers_transaction(payload, error):
    from integration.models import InterfaceCall
    with pytest.raises(error) as raised:
        with transaction.atomic():
            InterfaceCall.objects.create(method='GET', path='/x', status=200)
            outbox.record(SAMPLE, uuid.uuid4(), 1, payload)
    assert 'Ana Ruiz' not in str(raised.value)
    assert not OutboxEvent.objects.exists()
    assert not InterfaceCall.objects.exists()


@pytest.mark.django_db
def test_an_unregistered_type_cannot_be_recorded():
    with pytest.raises(UnknownEventType):
        with transaction.atomic():
            outbox.record('x.future', uuid.uuid4(), 1, {})
    assert not OutboxEvent.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize('kwargs', [
    {'aggregate_id': 'not-a-uuid'}, {'aggregate_version': 0}, {'region': 'EU'},
])
def test_malformed_envelope_fields_are_refused_before_writing(kwargs):
    args = {'event_type': SAMPLE, 'aggregate_id': uuid.uuid4(), 'aggregate_version': 1,
            'payload': {'label': 'x'}}
    region = kwargs.pop('region', None)
    args.update(kwargs)
    with pytest.raises((outbox.OutboxError, ValueError)):
        with transaction.atomic():
            outbox.record(**args, region=region)
    assert not OutboxEvent.objects.exists()


@pytest.mark.django_db
def test_an_oversized_event_is_refused(monkeypatch):
    monkeypatch.setattr(outbox, 'MAX_EVENT_BYTES', 200)
    with pytest.raises(outbox.OutboxError, match='larger than'):
        emit(label='x' * 100)
    assert not OutboxEvent.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_record_outside_a_transaction_is_refused():
    with pytest.raises(outbox.OutboxError, match='transaction.atomic'):
        outbox.record(SAMPLE, uuid.uuid4(), 1, {'label': 'x'})
    assert not OutboxEvent.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_the_nudge_is_enqueued_on_commit_only_and_delivers(admin):
    from django_tasks_db.models import DBTaskResult
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            outbox.record(SAMPLE, uuid.uuid4(), 1, {'label': 'x'})
            raise RuntimeError('the caller failed')
    assert not DBTaskResult.objects.exists() and not OutboxEvent.objects.exists()

    event = emit()
    task = DBTaskResult.objects.get()
    assert task.task_path == 'integration.tasks.dispatch_outbox_task'
    assert task.args_kwargs == {'args': [], 'kwargs': {}}
    call_command('db_worker', '--batch', '--no-startup-delay', '--no-reload', verbosity=0)
    assert state(event) == 'delivered'
    assert admin.applied == [str(event.event_id)]


@pytest.mark.django_db(transaction=True)
def test_one_nudge_per_transaction_and_the_log_line_after_commit(caplog):
    from django_tasks_db.models import DBTaskResult
    caplog.set_level(logging.INFO, logger='integration.outbox')
    aggregate = uuid.uuid4()
    with transaction.atomic():
        for version in (1, 2, 3):
            outbox.record(SAMPLE, aggregate, version, {'label': 'x'})
        # A savepoint rolled back keeps the transaction's one nudge.
        try:
            with transaction.atomic():
                outbox.record(SAMPLE, aggregate, 4, {'label': 'x'})
                raise RuntimeError
        except RuntimeError:
            pass
        assert 'outbox event recorded' not in caplog.text
    assert DBTaskResult.objects.count() == 1
    assert caplog.text.count('outbox event recorded') == 3
    # A rolled-back savepoint that held the only nudge: the next record adds it again.
    with transaction.atomic():
        try:
            with transaction.atomic():
                outbox.record(SAMPLE, aggregate, 5, {'label': 'x'})
                raise RuntimeError
        except RuntimeError:
            pass
        outbox.record(SAMPLE, aggregate, 6, {'label': 'x'})
    assert DBTaskResult.objects.count() == 2


def test_the_nudge_sweep_is_short(monkeypatch):
    from integration import tasks
    seen = {}
    monkeypatch.setattr(outbox, 'dispatch', lambda **kwargs: seen.update(kwargs) or {})
    tasks.dispatch_outbox_task.call()
    assert seen == {'max_seconds': 10}


# --- Matrix -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_happy_path_delivered_once(admin):
    event = emit()
    out = io.StringIO()
    call_command('dispatch_outbox', stdout=out)
    assert out.getvalue() == 'dispatch_outbox: 1 delivered, 0 rejected, 0 to retry\n'
    event.refresh_from_db()
    assert event.state == 'delivered' and event.delivered_at is not None
    assert admin.applied == [str(event.event_id)]
    assert admin.versions == {(SAMPLE, str(event.aggregate_id)): 1}
    call_command('dispatch_outbox', stdout=io.StringIO())
    assert admin.sent == [str(event.event_id)]


@pytest.mark.django_db
def test_a_run_killed_after_the_admin_stored_the_event_resends_it_as_duplicate(
        admin, monkeypatch):
    event = emit()
    real = outbox._after_send

    def killed(*args, **kwargs):
        raise KeyboardInterrupt('killed')
    monkeypatch.setattr(outbox, '_after_send', killed)
    with pytest.raises(KeyboardInterrupt):
        outbox.dispatch()
    assert state(event) == 'pending'
    monkeypatch.setattr(outbox, '_after_send', real)
    outbox.dispatch()
    assert state(event) == 'delivered'
    assert admin.sent == [str(event.event_id)] * 2
    assert admin.applied == [str(event.event_id)]


@pytest.mark.django_db
def test_admin_down_keeps_rows_pending_with_backoff_then_delivers(admin):
    admin.down = True
    event = emit()
    for attempt in (1, 2, 3):
        make_due()
        before = timezone.now()
        assert outbox.dispatch() == {'delivered': 0, 'rejected': 0, 'retry': 1}
        event.refresh_from_db()
        assert event.state == 'pending' and event.attempts == attempt
        assert event.last_error == 'network: no answer: connection refused'
        wait = timedelta(minutes=2 ** (attempt - 1))
        assert before + wait <= event.next_attempt_at <= timezone.now() + wait
    # Not due yet: nothing is sent.
    admin.down = False
    assert outbox.dispatch() == {'delivered': 0, 'rejected': 0, 'retry': 0}
    make_due()
    assert outbox.dispatch()['delivered'] == 1
    assert state(event) == 'delivered'


def test_backoff_doubles_up_to_an_hour():
    assert [outbox.backoff(n).total_seconds() / 60 for n in (1, 2, 3, 6, 7, 8, 50)] == \
        [1, 2, 4, 32, 60, 60, 60]


@pytest.mark.django_db
@pytest.mark.parametrize('status', [500, 503, 401, 404])
def test_http_errors_are_retried(admin, status):
    event = emit()
    admin.fail_for[str(event.event_id)] = status
    outbox.dispatch()
    event.refresh_from_db()
    assert (event.state, event.attempts) == ('pending', 1)
    assert event.last_error.startswith(f'http_{status}: HTTP {status}')


@pytest.mark.django_db
def test_order_within_an_aggregate_and_other_aggregates_go_on(admin):
    a, b = uuid.uuid4(), uuid.uuid4()
    a1 = emit(a, 1)
    a2 = emit(a, 2)
    b1 = emit(b, 1)
    admin.fail_for[str(a1.event_id)] = 503
    outbox.dispatch()
    assert (state(a1), state(a2), state(b1)) == ('pending', 'pending', 'delivered')
    assert str(a2.event_id) not in admin.sent
    make_due()
    outbox.dispatch()
    assert str(a2.event_id) not in admin.sent
    del admin.fail_for[str(a1.event_id)]
    make_due()
    outbox.dispatch()
    assert (state(a1), state(a2)) == ('delivered', 'delivered')
    assert admin.applied == [str(b1.event_id), str(a1.event_id), str(a2.event_id)]


@pytest.mark.django_db
def test_an_older_version_is_stale_and_delivered(admin):
    aggregate = uuid.uuid4()
    emit(aggregate, 2)
    outbox.dispatch()
    old = emit(aggregate, 1)
    outbox.dispatch()
    assert state(old) == 'delivered'
    assert admin.versions[(SAMPLE, str(aggregate))] == 2


@pytest.mark.django_db
def test_a_wrong_region_is_rejected_for_good(admin):
    event = emit(region='ae')
    assert outbox.dispatch() == {'delivered': 0, 'rejected': 1, 'retry': 0}
    event.refresh_from_db()
    assert (event.state, event.last_error) == ('rejected', 'rejected: region_mismatch')
    make_due()
    outbox.dispatch()
    assert admin.sent == [str(event.event_id)]


@pytest.mark.django_db
def test_a_rejected_head_lets_the_aggregate_go_on(admin):
    aggregate = uuid.uuid4()
    bad = emit(aggregate, 1, region='ae')
    good = emit(aggregate, 2)
    outbox.dispatch()
    assert (state(bad), state(good)) == ('rejected', 'delivered')


@pytest.mark.django_db
def test_an_answer_without_a_result_is_retried(admin, monkeypatch):
    event = emit()
    monkeypatch.setattr(interface, '_open', lambda request, timeout: admin.answer({'x': 1}))
    outbox.dispatch()
    event.refresh_from_db()
    assert (event.state, event.last_error) == ('pending', 'bad_answer: HTTP 200 without a '
                                                          'result')


@pytest.mark.django_db
def test_a_run_stops_after_three_failures_in_a_row(admin):
    admin.down = True
    for _ in range(5):
        emit()
    assert outbox.dispatch()['retry'] == 3
    assert OutboxEvent.objects.filter(attempts=0).count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize('status', [400, 404, 413])
def test_failures_of_one_event_do_not_stop_the_run(admin, status):
    """Five events the admin refuses one by one (4xx), then a healthy aggregate: the run
    goes on and delivers it."""
    bad = [emit() for _ in range(5)]
    for event in bad:
        admin.fail_for[str(event.event_id)] = status
    good = emit()
    assert outbox.dispatch() == {'delivered': 1, 'rejected': 0, 'retry': 5}
    assert state(good) == 'delivered'
    assert all(state(event) == 'pending' for event in bad)


@pytest.mark.django_db
def test_answers_without_a_result_do_not_stop_the_run(admin, monkeypatch):
    bad = [emit() for _ in range(4)]
    good = emit()
    real = admin.__call__

    def answer(request, timeout):
        if json.loads(request.data)['event_id'] == str(good.event_id):
            return real(request, timeout)
        return admin.answer({'x': 1})
    monkeypatch.setattr(interface, '_open', answer)
    assert outbox.dispatch() == {'delivered': 1, 'rejected': 0, 'retry': 4}
    assert all(state(event) == 'pending' for event in bad)


@pytest.mark.django_db
def test_a_body_over_the_limit_is_rejected_here_for_good(admin, monkeypatch):
    event = emit(label='x' * 100)
    monkeypatch.setattr(outbox, 'MAX_EVENT_BYTES', 200)
    assert outbox.dispatch() == {'delivered': 0, 'rejected': 1, 'retry': 0}
    event.refresh_from_db()
    assert event.state == 'rejected' and event.attempts == 0
    assert event.last_error == 'body_too_large: the body is larger than 200 bytes'
    assert admin.sent == []


@pytest.mark.django_db
def test_a_stored_event_that_no_longer_passes_is_rejected_here(admin):
    event = emit()
    OutboxEvent.objects.filter(pk=event.pk).update(payload={'label': 'x', 'price': 1.5})
    assert outbox.dispatch()['rejected'] == 1
    event.refresh_from_db()
    assert event.last_error.startswith('invalid_envelope: ')
    assert admin.sent == []


@pytest.mark.django_db
@pytest.mark.parametrize('reason', ['body_too_large', 'invalid_envelope'])
def test_a_local_rejection_is_reported_once_without_the_payload(
        admin, monkeypatch, sentry_capture, caplog, django_capture_on_commit_callbacks,
        reason):
    event = emit(label=SECRET_LABEL)
    if reason == 'body_too_large':
        monkeypatch.setattr(outbox, 'MAX_EVENT_BYTES', 120)
    else:
        OutboxEvent.objects.filter(pk=event.pk).update(
            payload={'label': SECRET_LABEL, 'price': 1.5})
    with django_capture_on_commit_callbacks(execute=True):
        assert outbox.dispatch()['rejected'] == 1
    with django_capture_on_commit_callbacks(execute=True):
        outbox.dispatch()  # nothing left to send: no second warning
    assert len(sentry_capture.events) == 1
    sent = sentry_capture.events[0]
    assert sent['level'] == 'warning'
    assert sent['tags']['outbox.event_id'] == str(event.event_id)
    assert sent['tags']['outbox.type'] == SAMPLE
    assert sent['tags']['outbox.error'] == reason
    assert 'rejected before sending' in json.dumps(sent, default=str)
    warnings = [r.getMessage() for r in caplog.records
                if r.name == 'integration.outbox' and r.levelno == logging.WARNING]
    assert any(str(event.event_id) in line and reason in line for line in warnings)
    assert SECRET_LABEL not in json.dumps(sent, default=str)
    assert SECRET_LABEL not in caplog.text


@pytest.mark.django_db
def test_a_rejection_by_the_admin_sends_no_sentry_warning(
        admin, sentry_capture, django_capture_on_commit_callbacks):
    emit(region='ae')
    with django_capture_on_commit_callbacks(execute=True):
        assert outbox.dispatch()['rejected'] == 1
    assert sentry_capture.events == []


@pytest.mark.django_db
def test_only_a_missing_answer_is_labelled_network(admin, settings, monkeypatch):
    event = emit()
    settings.DEBUG = False
    monkeypatch.setattr(outbox, 'admin_site',
                        lambda: ('admindev', 'http://admindev.example.test/api/v1/events'))
    outbox.dispatch()
    event.refresh_from_db()
    assert event.last_error.startswith('peer_url: ')


@pytest.mark.django_db
def test_one_sentry_warning_after_24_hours_without_the_payload(
        admin, sentry_capture, caplog, django_capture_on_commit_callbacks):
    caplog.set_level(logging.INFO)
    admin.down = True
    event = emit(label=SECRET_LABEL)
    with django_capture_on_commit_callbacks(execute=True):
        outbox.dispatch()
    assert sentry_capture.events == []
    OutboxEvent.objects.update(failing_since=timezone.now() - timedelta(hours=25))
    for _ in range(2):
        make_due()
        with django_capture_on_commit_callbacks(execute=True):
            outbox.dispatch()
    assert len(sentry_capture.events) == 1
    sent = sentry_capture.events[0]
    assert sent['level'] == 'warning'
    assert sent['tags']['outbox.event_id'] == str(event.event_id)
    assert sent['tags']['outbox.type'] == SAMPLE
    assert sent['tags']['outbox.error'] == 'network'
    assert SECRET_LABEL not in json.dumps(sent, default=str)
    assert SECRET_LABEL not in caplog.text
    event.refresh_from_db()
    assert event.warned_at is not None and event.attempts == 3


@pytest.mark.django_db
def test_the_warning_waits_for_the_commit_that_marks_the_row(
        admin, sentry_capture, monkeypatch, django_capture_on_commit_callbacks):
    """A run that fails after marking the row warned sends nothing (rolled back)."""
    admin.down = True
    event = emit()
    OutboxEvent.objects.update(failing_since=timezone.now() - timedelta(hours=25))
    real_save = OutboxEvent.save

    def failing_save(self, *args, **kwargs):
        real_save(self, *args, **kwargs)
        raise RuntimeError('killed after the save')
    monkeypatch.setattr(OutboxEvent, 'save', failing_save)
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            outbox.dispatch()
    assert sentry_capture.events == []
    monkeypatch.setattr(OutboxEvent, 'save', real_save)
    event.refresh_from_db()
    assert event.warned_at is None


@pytest.mark.django_db
def test_without_pending_events_no_configuration_is_needed(settings):
    settings.INTERFACE_PEERS = ''
    settings.INTERFACE_SIGNING_KEY = ''
    out = io.StringIO()
    call_command('dispatch_outbox', stdout=out)
    assert '0 delivered' in out.getvalue()


@pytest.mark.django_db
@pytest.mark.parametrize('name, value, message', [
    ('INTERFACE_PEERS', '', 'INTERFACE_ADMIN_SITE'),
    ('INTERFACE_ADMIN_SITE', 'admin', "'admin' is not in INTERFACE_PEERS"),
    ('INTERFACE_SIGNING_KEY', '', 'No signing key'),
])
def test_pending_events_without_configuration_fail_the_job(admin, settings, name, value,
                                                           message):
    event = emit()
    setattr(settings, name, value)
    interface.reset_signing_key()
    with pytest.raises(CommandError, match=message):
        call_command('dispatch_outbox', stdout=io.StringIO())
    event.refresh_from_db()
    assert (event.state, event.attempts) == ('pending', 0)
    assert admin.sent == []


@pytest.mark.django_db
def test_admin_site_is_the_only_peer_or_the_named_one(settings):
    assert outbox.admin_site() == ('admindev', EVENTS_URL)
    settings.INTERFACE_PEERS = 'admindev=https://a.test,admin=https://b.test'
    with pytest.raises(interface.InterfaceConfigurationError):
        outbox.admin_site()
    settings.INTERFACE_ADMIN_SITE = 'admin'
    assert outbox.admin_site() == ('admin', 'https://b.test/api/v1/events')


@pytest.mark.django_db
def test_the_nudge_task_logs_a_missing_configuration(settings, caplog):
    from integration.tasks import dispatch_outbox_task
    emit()
    settings.INTERFACE_PEERS = ''
    assert dispatch_outbox_task.call() is None
    assert 'outbox nudge: not sent' in caplog.text


# --- Purge ------------------------------------------------------------------------------

@pytest.mark.django_db
def test_purge_deletes_old_delivered_rows_and_keeps_rejected_ones():
    now = timezone.now()
    aggregate, other = uuid.uuid4(), uuid.uuid4()
    old_delivered = emit(aggregate, 1)
    old_rejected = emit(aggregate, 2)
    recent = emit(aggregate, 3)
    last_old = emit(other, 1)
    OutboxEvent.objects.filter(pk__in=[old_delivered.pk, last_old.pk]).update(
        state='delivered', delivered_at=now - timedelta(days=31))
    OutboxEvent.objects.filter(pk=old_rejected.pk).update(state='rejected')
    OutboxEvent.objects.filter(pk=recent.pk).update(
        state='delivered', delivered_at=now - timedelta(days=29))
    out = io.StringIO()
    call_command('purge_outbox', stdout=out)
    assert out.getvalue() == 'purge_outbox: 2 events deleted\n'
    assert set(OutboxEvent.objects.values_list('pk', flat=True)) == {
        old_rejected.pk, recent.pk}
    # The counter row keeps the sequence going once every event of `other` is gone.
    assert emit(other, 2).sequence == 2
    assert emit(aggregate, 4).sequence == 4


# --- Sample -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_emit_sample_event_is_refused_unless_enabled(settings):
    settings.INTERFACE_SAMPLE_EVENTS = False
    with pytest.raises(CommandError, match='INTERFACE_SAMPLE_EVENTS is off'):
        call_command('emit_sample_event', str(uuid.uuid4()), '1', stdout=io.StringIO())
    assert not OutboxEvent.objects.exists()


@pytest.mark.django_db
def test_emit_sample_event_records_and_is_delivered(settings, admin):
    settings.INTERFACE_SAMPLE_EVENTS = True
    aggregate = str(uuid.uuid4())
    out = io.StringIO()
    call_command('emit_sample_event', aggregate, '3', '--label', 'staging check', stdout=out)
    event = OutboxEvent.objects.get()
    assert out.getvalue() == (f'emit_sample_event: {event.event_id} recorded (aggregate '
                              f'{aggregate} v3 #1)\n')
    assert event.payload == {'label': 'staging check'}
    call_command('emit_sample_event', aggregate, '1', '--region', 'ae', stdout=out)
    with pytest.raises(CommandError, match='aggregate_id is not a UUID'):
        call_command('emit_sample_event', 'nope', '1', stdout=out)
    outbox.dispatch()
    assert list(OutboxEvent.objects.values_list('state', flat=True)) == ['delivered',
                                                                         'rejected']


def test_sample_events_are_refused_on_a_public_site(settings):
    from integration.checks import check_interface_settings
    settings.INTERFACE_SAMPLE_EVENTS = True
    settings.ROBOTS_NOINDEX = True
    assert check_interface_settings() == []
    settings.ROBOTS_NOINDEX = False
    assert [e.id for e in check_interface_settings()] == ['integration.E004']


def test_admin_site_must_be_a_peer(settings):
    from integration.checks import check_interface_settings
    settings.INTERFACE_ADMIN_SITE = 'admin'
    assert [e.id for e in check_interface_settings()] == ['integration.E005']
    settings.INTERFACE_ADMIN_SITE = ''
    settings.INTERFACE_PEERS = 'admindev=https://a.test,admin=https://b.test'
    assert [e.id for e in check_interface_settings()] == ['integration.E005']
    settings.INTERFACE_ADMIN_SITE = 'admin'
    assert check_interface_settings() == []
    settings.INTERFACE_ADMIN_SITE = ''
    settings.INTERFACE_PEERS = ''
    assert check_interface_settings() == []


def test_outbox_jobs_run_every_minute_and_daily():
    from mytp_core import jobs
    declared = jobs.load_jobs(DEPLOY / 'jobs.conf')
    dispatch = declared['dispatchoutbox']
    assert (dispatch.command, dispatch.args) == ('dispatch_outbox', ())
    assert dispatch.schedule == '*-*-* *:00/1:00 UTC'
    assert jobs.oncalendar_to_crontab(dispatch.schedule) == '*/1 * * * *'
    purge = declared['purgeoutbox']
    assert (purge.command, purge.schedule) == ('purge_outbox', '*-*-* 04:25:00 UTC')


# --- Concurrency (PostgreSQL only: SQLite has no row locks) -----------------------------

@pytest.mark.django_db(transaction=True)
def test_two_concurrent_runs_send_each_event_once(admin):
    if connection.vendor != 'postgresql':
        pytest.skip('select_for_update(skip_locked=True) needs PostgreSQL')
    events = [emit() for _ in range(20)]
    real = admin.__call__
    barrier = threading.Barrier(2)

    def slow(request, timeout):
        import time
        time.sleep(0.02)
        return real(request, timeout)

    import mytp_core.interface as core_interface
    core_interface._open = slow
    errors = []

    def run():
        from django.db import connections
        try:
            barrier.wait()
            outbox.dispatch()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connections.close_all()

    try:
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        core_interface._open = admin
    assert errors == []
    assert sorted(admin.sent) == sorted(str(e.event_id) for e in events)
    assert len(admin.sent) == len(set(admin.sent)) == 20
    assert set(OutboxEvent.objects.values_list('state', flat=True)) == {'delivered'}


def _in_threads(count, target):
    """Run `target(index)` in `count` threads started together; their exceptions."""
    from django.db import connections
    barrier = threading.Barrier(count)
    errors = []

    def run(index):
        try:
            barrier.wait()
            target(index)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=run, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


@pytest.mark.django_db(transaction=True)
def test_concurrent_records_on_one_new_aggregate_all_succeed_in_sequence():
    if connection.vendor != 'postgresql':
        pytest.skip('row locks need PostgreSQL')
    aggregate = uuid.uuid4()

    def write(index):
        import time
        with transaction.atomic():
            outbox.record(SAMPLE, aggregate, index + 1, {'label': 'x'})
            time.sleep(0.02)  # hold the counter's lock a little

    assert _in_threads(8, write) == []
    assert sorted(OutboxEvent.objects.filter(aggregate_id=aggregate)
                  .values_list('sequence', flat=True)) == list(range(1, 9))
    from integration.models import OutboxAggregate
    assert OutboxAggregate.objects.get(pk=aggregate).last_sequence == 8


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_runs_deliver_one_aggregate_in_sequence_order(admin):
    if connection.vendor != 'postgresql':
        pytest.skip('select_for_update(skip_locked=True) needs PostgreSQL')
    aggregate = uuid.uuid4()
    events = [emit(aggregate, version) for version in range(1, 6)]
    real = admin.__call__

    def slow(request, timeout):
        import time
        time.sleep(0.03)
        return real(request, timeout)

    import mytp_core.interface as core_interface
    core_interface._open = slow
    try:
        assert _in_threads(2, lambda index: outbox.dispatch()) == []
    finally:
        core_interface._open = admin
    assert admin.sent == [str(event.event_id) for event in events]
    assert admin.applied == admin.sent
    assert set(OutboxEvent.objects.values_list('state', flat=True)) == {'delivered'}
