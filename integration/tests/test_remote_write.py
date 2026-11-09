"""Remote writes (epic-admin-interface entry 6, interface I7, AD-3, AD-15): the sample
record's POST, every row of the plan's matrix (write, retry, key reused, stale, a field
not allowed, not assigned, the log unwritable), the order (rules, key, log, apply), the
stored answer (encrypted, replayed with only the fields the actor may read), the version,
the purge of old keys and, on PostgreSQL, concurrent writes. The admin site is played by
its signing key (a fixed, non-secret test seed); no test reaches the network or a KMS."""
import io
import json
import logging
import threading
import time
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import Client
from django.utils import timezone

from integration import remote, sample
from integration.models import RemoteAccess, RemoteWrite, SampleRecord
from integration.tests.test_remote import (ADMIN, CLINICAL_FIELDS, DEV_AE, ME, NAME,
                                           SOMEONE, URL, interface_settings, scope)
from mytp_core import interface

__all__ = ['interface_settings']  # the autouse fixture, shared with the read tests

NOTE = 'Rückruf am Montag'


def post(client, body, signer=ADMIN, issuer='admindev', extra=None, **kwargs):
    raw = body if isinstance(body, bytes) else interface.json_body(body)
    token = interface.sign_request('POST', URL, raw, audience='dev', issuer=issuer,
                                   signing_key=signer,
                                   extra=scope(**kwargs) if extra is None else extra)
    return client.generic('POST', URL, raw, content_type='application/json',
                          HTTP_AUTHORIZATION=interface.authorization_header(token),
                          REMOTE_ADDR='203.0.113.9', HTTP_USER_AGENT='mytp-admindev-test')


def body(note=NOTE, expected=1, key=None, changes=None, object_id=None):
    request = {'idempotency_key': key or str(uuid.uuid4()), 'expected_version': expected,
               'changes': {'note': note} if changes is None else changes}
    if object_id is not None:
        request['object_id'] = str(object_id)
    return request


def write(client, note=NOTE, expected=1, key=None, changes=None, object_id=None,
          **kwargs):
    return post(client, body(note, expected, key, changes, object_id), **kwargs)


@pytest.fixture
def record(db):
    return SampleRecord.objects.create(patient_name=NAME, amount_value=Decimal('120.00'),
                                       currency='EUR', assignee_uuid=uuid.UUID(ME),
                                       note='Staging sample')


def rows(**filters):
    return list(RemoteAccess.objects.filter(**filters).order_by('at', 'id'))


def assert_no_store(response):
    assert 'no-store' in response['Cache-Control']


def assert_unchanged(record):
    record.refresh_from_db()
    assert (record.note, record.version) == ('Staging sample', 1)
    assert not RemoteWrite.objects.exists()


# --- Matrix -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_clinical_manager_writes_the_note(client, record):
    response = write(client, role='clinical_manager')
    assert response.status_code == 200
    assert_no_store(response)
    assert response.json() == {
        'resource': 'sample_record', 'region': 'eu', 'object_id': str(record.uuid),
        'fields': CLINICAL_FIELDS,
        'data': {'uuid': str(record.uuid), 'patient_name': NAME, 'assignee_uuid': ME,
                 'note': NOTE},
        'version': 2}
    record.refresh_from_db()
    assert (record.note, record.version) == (NOTE, 2)
    (row,) = rows()
    # `fields`: the names returned, as a read's (user, 2026-11-09); the changed names are
    # in the access reason.
    assert (row.operation, row.outcome, row.reason, row.fields, row.access_reason,
            str(row.actor_uuid), row.role, row.resource, row.object_id,
            row.source_site) == (
        'write', 'granted', '', CLINICAL_FIELDS, 'write:note', ME, 'clinical_manager',
        'sample_record', str(record.uuid), 'admindev')
    assert row.fields == response.json()['fields']
    stored = RemoteWrite.objects.get()
    assert (str(stored.actor_uuid), stored.resource, stored.object_id, stored.status) == (
        ME, 'sample_record', str(record.uuid), 200)
    assert stored.response == response.json()


@pytest.mark.django_db
def test_a_retry_gets_the_same_answer_and_is_applied_once(client, record):
    request = body()
    first = post(client, request)
    second = post(client, request)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert_no_store(second)
    record.refresh_from_db()
    assert (record.note, record.version) == (NOTE, 2)
    assert RemoteWrite.objects.count() == 1
    # The retry is answered from the key, after its own granted row (reason `replay`).
    assert [(r.operation, r.outcome, r.access_reason, r.fields) for r in rows()] == [
        ('write', 'granted', 'write:note', CLINICAL_FIELDS),
        ('write', 'granted', 'replay:note', CLINICAL_FIELDS)]


@pytest.mark.django_db
def test_a_replay_whose_log_fails_is_503_without_data(client, record, monkeypatch):
    request = body()
    assert post(client, request).status_code == 200
    monkeypatch.setattr(RemoteAccess.objects, 'create',
                        lambda **kwargs: (_ for _ in ()).throw(RuntimeError('down')))
    response = post(client, request)
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'access_log_unavailable'
    assert NAME not in response.content.decode() and NOTE not in response.content.decode()


@pytest.mark.django_db
def test_a_retry_with_the_keys_in_another_order_is_the_same_request(client, record):
    request = body()
    assert post(client, request).status_code == 200
    reordered = json.dumps({'changes': request['changes'],
                            'expected_version': request['expected_version'],
                            'idempotency_key': request['idempotency_key']}).encode()
    assert post(client, reordered).status_code == 200
    record.refresh_from_db()
    assert record.version == 2


@pytest.mark.django_db
@pytest.mark.parametrize('other', [{'note': 'Something else'}, {'expected': 2}])
def test_the_same_key_with_another_body_is_422(client, record, other):
    key = str(uuid.uuid4())
    assert write(client, key=key).status_code == 200
    response = write(client, key=key, **{'note': NOTE, **other})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'idempotency_key_reused'
    assert_no_store(response)
    record.refresh_from_db()
    assert (record.note, record.version) == (NOTE, 2)
    refused = rows(outcome='refused')
    assert [(r.operation, r.reason, r.fields) for r in refused] == [
        ('write', 'idempotency_key_reused', ['note'])]


@pytest.mark.django_db
def test_keys_are_per_admin_user(client, record):
    key = str(uuid.uuid4())
    assert write(client, key=key, sub=ME).status_code == 200
    # Another admin user with the same key is a new request: stale, not a replay.
    response = write(client, key=key, sub=SOMEONE, role='sysadmin')
    assert response.status_code == 409
    assert RemoteWrite.objects.count() == 1


@pytest.mark.django_db
def test_a_stale_version_is_409_with_the_current_version(client, record):
    assert write(client, note='first').status_code == 200
    response = write(client, note='second', expected=1)
    assert response.status_code == 409
    assert_no_store(response)
    assert response.json() == {'error': {
        'code': 'stale_version', 'message': 'The record was changed meanwhile; read it again.'},
        'current_version': 2}
    record.refresh_from_db()
    assert (record.note, record.version) == ('first', 2)
    assert RemoteWrite.objects.count() == 1
    # The second write was logged granted (allowed to try), committed before applying,
    # so it stays when the stale write rolls back; then the refused row.
    assert [(r.outcome, r.reason) for r in rows()] == [
        ('granted', ''), ('granted', ''), ('refused', 'stale_version')]
    assert NAME not in response.content.decode() and 'first' not in response.content.decode()


@pytest.mark.django_db
def test_writes_in_sequence_follow_the_version(client, record):
    for version in (1, 2, 3):
        response = write(client, note=f'note {version}', expected=version)
        assert response.json()['version'] == version + 1
    record.refresh_from_db()
    assert (record.note, record.version) == ('note 3', 4)


@pytest.mark.django_db
@pytest.mark.parametrize('role, changes', [
    ('clinical_manager', {'amount': {'amount': '1.00', 'currency': 'EUR'}}),
    ('clinical_manager', {'note': 'x', 'amount': {'amount': '1.00', 'currency': 'EUR'}}),
    ('clinical_manager', {'patient_name': 'Other'}),
    ('clinical_user', {'patient_name': 'Other'}),
    ('sysadmin', {'amount': {'amount': '1.00', 'currency': 'EUR'}}),
    ('sysadmin', {'uuid': SOMEONE}),
    ('sysadmin', {'version': 9}),
])
def test_a_field_outside_the_roles_writable_fields_is_403(client, record, role, changes):
    response = write(client, changes=changes, role=role)
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'forbidden'
    assert_no_store(response)
    assert_unchanged(record)
    (row,) = rows()
    assert (row.operation, row.outcome, row.reason, row.fields) == (
        'write', 'refused', 'field', sorted(changes))


@pytest.mark.django_db
@pytest.mark.parametrize('assignee', [SOMEONE, None])
def test_a_clinical_user_not_assigned_is_403(client, record, assignee):
    record.assignee_uuid = assignee
    record.save()
    response = write(client, role='clinical_user')
    assert response.status_code == 403
    assert NAME not in response.content.decode()
    record.refresh_from_db()
    assert record.version == 1
    (row,) = rows()
    assert (row.operation, row.outcome, row.reason, row.object_id) == (
        'write', 'refused', 'assigned', str(record.uuid))


@pytest.mark.django_db
def test_an_assigned_clinical_user_writes(client, record):
    response = write(client, role='clinical_user')
    assert response.status_code == 200
    assert response.json()['fields'] == CLINICAL_FIELDS


@pytest.mark.django_db
@pytest.mark.parametrize('role', ['financial_manager', 'commercial_manager',
                                  'commercial_regional_manager'])
def test_roles_without_the_patients_area_are_refused(client, record, role):
    response = write(client, role=role)
    assert response.status_code == 403
    assert_unchanged(record)
    (row,) = rows()
    assert (row.operation, row.reason) == ('write', 'role')


@pytest.mark.django_db
def test_a_region_not_permitted_is_refused(client, record):
    response = write(client, role='sysadmin', regions=('ae',))
    assert response.status_code == 403
    assert rows()[0].reason == 'region'


@pytest.mark.django_db
def test_a_token_without_a_valid_admin_user_is_refused(client, record):
    response = write(client, extra={'sub': ME, 'role': 'root', 'regions': ['eu']})
    assert response.status_code == 403
    (row,) = rows()
    assert (row.operation, row.reason, row.actor_uuid) == ('write', 'actor', None)


@pytest.mark.django_db
def test_log_unwritable_means_503_and_nothing_applied(client, record, monkeypatch, caplog):
    def fail(**kwargs):
        raise RuntimeError('permission denied for table integration_remoteaccess')
    monkeypatch.setattr(RemoteAccess.objects, 'create', fail)
    monkeypatch.setattr(sample, 'update_sample_record',
                        lambda *a: pytest.fail('nothing may be applied'))
    response = write(client)
    assert response.status_code == 503
    assert response.json() == {'error': {
        'code': 'access_log_unavailable',
        'message': 'The access could not be logged, so nothing was changed.'}}
    assert_no_store(response)
    assert_unchanged(record)
    assert NOTE not in caplog.text and 'permission denied' not in caplog.text


@pytest.mark.django_db(transaction=True)
def test_the_write_is_logged_and_committed_before_it_is_applied(record, monkeypatch):
    seen = []
    original = sample.update_sample_record

    def update(*args):
        # Inside the write's transaction; the granted row was committed before it.
        assert connection.in_atomic_block
        (row,) = RemoteAccess.objects.all()
        seen.append((row.operation, row.outcome, row.fields))
        return original(*args)
    monkeypatch.setattr(sample, 'update_sample_record', update)
    assert write(Client()).status_code == 200
    assert seen == [('write', 'granted', CLINICAL_FIELDS)]


@pytest.mark.django_db
@pytest.mark.parametrize('raw', [
    b'', b'not json', b'[]', b'{"idempotency_key": NaN}',
    json.dumps({**body(), 'extra': 1}).encode(),
    json.dumps({**body(), 'expected_version': 0}).encode(),
    json.dumps({**body(), 'expected_version': '1'}).encode(),
    json.dumps({**body(), 'idempotency_key': 'not-a-uuid'}).encode(),
    json.dumps({**body(), 'changes': {}}).encode(),
    json.dumps(body(note='x' * 501)).encode(),
    json.dumps(body(changes={'note': None})).encode(),
    json.dumps(body(changes={'note': 5})).encode(),
    json.dumps(body(note='x' * 20000)).encode(),
])
def test_a_body_that_is_not_a_write_request_is_400(client, record, raw):
    response = post(client, raw)
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'invalid_body'
    assert_unchanged(record)
    (row,) = rows()
    assert (row.operation, row.outcome, row.reason) == ('write', 'refused', 'invalid_body')


@pytest.mark.django_db
def test_a_note_of_500_characters_is_accepted(client, record):
    assert write(client, note='é' * 500).status_code == 200
    record.refresh_from_db()
    assert record.note == 'é' * 500


@pytest.mark.django_db
def test_no_record_is_404(client):
    response = write(client)
    assert response.status_code == 404
    (row,) = rows()
    assert (row.operation, row.reason) == ('write', 'not_found')


@pytest.mark.django_db
def test_the_endpoint_is_404_without_the_setting(client, record, settings):
    settings.INTERFACE_SAMPLE_RECORDS = False
    assert write(client).status_code == 404
    assert_unchanged(record)
    assert not RemoteAccess.objects.exists()


@pytest.mark.django_db
def test_another_portal_cannot_write(client, record):
    response = write(client, signer=DEV_AE, issuer='dev-ae', role='sysadmin')
    assert response.status_code == 401
    assert_unchanged(record)
    assert not RemoteAccess.objects.exists()


@pytest.mark.django_db
def test_a_token_signed_for_another_body_is_refused(client, record):
    token = interface.sign_request('POST', URL, interface.json_body(body(note='a')),
                                   audience='dev', issuer='admindev', signing_key=ADMIN,
                                   extra=scope())
    response = client.generic('POST', URL, interface.json_body(body(note='b')),
                              content_type='application/json',
                              HTTP_AUTHORIZATION=interface.authorization_header(token))
    assert response.status_code == 401
    assert_unchanged(record)


@pytest.mark.django_db
def test_a_replay_answers_with_only_the_fields_the_actor_reads_now(client, record):
    request = body()
    first = post(client, request, role='sysadmin')
    assert 'amount' in first.json()['data']
    # The same admin user, now a Clinical Manager: the stored answer without the amount.
    again = post(client, request, role='clinical_manager')
    assert again.status_code == 200
    assert again.json()['fields'] == CLINICAL_FIELDS
    assert 'amount' not in again.json()['data']
    assert again.json()['version'] == 2


@pytest.mark.django_db
def test_the_read_after_a_write_has_the_new_version(client, record):
    from integration.tests.test_remote import read
    assert write(client).status_code == 200
    answer = read(client).json()
    assert (answer['version'], answer['data']['note']) == (2, NOTE)


@pytest.mark.django_db
def test_nothing_of_the_record_is_logged(client, record, caplog):
    caplog.set_level(logging.DEBUG)
    request = body()
    assert post(client, request, role='sysadmin').status_code == 200
    assert post(client, request, role='sysadmin').status_code == 200
    assert write(client, note='stale').status_code == 409
    assert NAME not in caplog.text and NOTE not in caplog.text
    assert '120.00' not in caplog.text and 'stale' not in caplog.text.replace(
        'stale_version', '')


@pytest.mark.django_db
def test_the_stored_answer_is_encrypted(client, record):
    assert write(client).status_code == 200
    with connection.cursor() as cursor:
        cursor.execute('SELECT response FROM integration_remotewrite')
        (stored,) = cursor.fetchone()
    stored = stored if isinstance(stored, str) else json.dumps(stored)
    assert NAME not in stored and NOTE not in stored and 'enc:' in stored


@pytest.mark.django_db
def test_seed_sample_record_starts_at_version_1(client, record):
    assert write(client).status_code == 200
    call_command('seed_sample_record', stdout=io.StringIO())
    assert SampleRecord.objects.get().version == 1


# --- Purge ------------------------------------------------------------------------------

@pytest.mark.django_db
def test_purge_outbox_deletes_keys_older_than_7_days(client, record):
    assert write(client).status_code == 200
    assert write(client, expected=2).status_code == 200
    old, recent = RemoteWrite.objects.order_by('id')
    RemoteWrite.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(days=7, minutes=1))
    RemoteWrite.objects.filter(pk=recent.pk).update(
        created_at=timezone.now() - timedelta(days=6, hours=23))
    out = io.StringIO()
    call_command('purge_outbox', stdout=out)
    assert out.getvalue() == ('purge_outbox: 0 events deleted, 1 remote write keys '
                              'deleted\n')
    assert list(RemoteWrite.objects.values_list('pk', flat=True)) == [recent.pk]
    assert remote.purge_writes() == 0


# --- Concurrency (PostgreSQL) -----------------------------------------------------------

def _in_threads(count, target):
    """Run `target(index)` in `count` threads started together; their results."""
    from django.db import connections
    barrier = threading.Barrier(count)
    results = [None] * count

    def run(index):
        try:
            barrier.wait()
            results[index] = target(index)
        except Exception as exc:  # noqa: BLE001
            results[index] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=run, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def _slow_updates(monkeypatch):
    original = sample.update_sample_record

    def update(*args):
        time.sleep(0.2)  # both requests are in flight together
        return original(*args)
    monkeypatch.setattr(sample, 'update_sample_record', update)


@pytest.mark.django_db(transaction=True)
def test_two_admin_users_writing_from_version_1_one_wins(record, monkeypatch):
    if connection.vendor != 'postgresql':
        pytest.skip('row locks need PostgreSQL')
    _slow_updates(monkeypatch)
    users = [(ME, 'clinical_manager'), (SOMEONE, 'sysadmin')]

    def send(index):
        sub, role = users[index]
        response = write(Client(), note=f'by {index}', sub=sub, role=role)
        return response.status_code, response.json()

    results = _in_threads(2, send)
    assert sorted(status for status, _ in results) == [200, 409]
    stale = next(answer for status, answer in results if status == 409)
    assert stale['current_version'] == 2
    record.refresh_from_db()
    assert record.version == 2
    assert RemoteWrite.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_a_retry_arriving_while_the_first_is_in_flight_is_applied_once(record, monkeypatch):
    if connection.vendor != 'postgresql':
        pytest.skip('row locks need PostgreSQL')
    _slow_updates(monkeypatch)
    request = body()

    def send(index):
        response = post(Client(), request)
        return response.status_code, response.json()

    first, second = _in_threads(2, send)
    assert first == second
    assert first[0] == 200 and first[1]['version'] == 2
    record.refresh_from_db()
    assert (record.note, record.version) == (NOTE, 2)
    assert RemoteWrite.objects.count() == 1
    # Both passed the key lookup before either was applied: two granted rows, no refusal.
    assert [(r.operation, r.outcome, r.access_reason, r.fields) for r in rows()] == [
        ('write', 'granted', 'write:note', CLINICAL_FIELDS)] * 2


@pytest.mark.django_db(transaction=True)
def test_the_same_key_in_flight_with_another_body_is_422(record, monkeypatch):
    if connection.vendor != 'postgresql':
        pytest.skip('row locks need PostgreSQL')
    _slow_updates(monkeypatch)
    key = str(uuid.uuid4())

    def send(index):
        response = write(Client(), note=f'note {index}', key=key)
        return response.status_code

    assert sorted(_in_threads(2, send)) == [200, 422]
    record.refresh_from_db()
    assert record.version == 2 and RemoteWrite.objects.count() == 1


# --- The record named in the request (object_id) ----------------------------------------

@pytest.mark.django_db
def test_a_write_naming_its_record_changes_that_record(client, record):
    response = write(client, object_id=record.uuid)
    assert response.status_code == 200 and response.json()['object_id'] == str(record.uuid)
    record.refresh_from_db()
    assert (record.note, record.version) == (NOTE, 2)


@pytest.mark.django_db
def test_a_write_for_a_record_reseeded_meanwhile_is_404_and_changes_nothing(client, record):
    call_command('seed_sample_record', stdout=io.StringIO())
    new = SampleRecord.objects.get()
    assert new.pk != record.pk and new.version == 1
    # Based on the old record's version 1: the new record (also at 1) is never written.
    response = write(client, object_id=record.uuid)
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'not_found'
    new.refresh_from_db()
    assert (new.version, new.note) == (1, 'Staging sample record (entry 5).')
    assert not RemoteWrite.objects.exists()
    (row,) = rows()
    assert (row.operation, row.reason, row.object_id) == (
        'write', 'not_found', str(record.uuid))


@pytest.mark.django_db
def test_the_same_key_for_another_record_is_422(client, record):
    key = str(uuid.uuid4())
    assert write(client, key=key, object_id=record.uuid).status_code == 200
    response = write(client, key=key)  # the same body without object_id
    assert response.status_code == 422


@pytest.mark.django_db
def test_a_failure_after_the_claim_rolls_the_key_back(client, record, monkeypatch):
    request = body()
    original = sample.build
    monkeypatch.setattr(sample, 'build', lambda r: (_ for _ in ()).throw(RuntimeError('x')))
    with pytest.raises(RuntimeError):
        post(client, request)
    record.refresh_from_db()
    assert record.version == 1 and not RemoteWrite.objects.exists()
    monkeypatch.setattr(sample, 'build', original)
    response = post(client, request)
    assert response.status_code == 200 and response.json()['version'] == 2


@pytest.mark.django_db
def test_a_failure_in_update_rolls_the_key_back(client, record, monkeypatch):
    request = body()
    original = sample.update_sample_record
    monkeypatch.setattr(sample, 'update_sample_record',
                        lambda *a: (_ for _ in ()).throw(RuntimeError('x')))
    with pytest.raises(RuntimeError):
        post(client, request)
    assert not RemoteWrite.objects.exists()
    monkeypatch.setattr(sample, 'update_sample_record', original)
    assert post(client, request).json()['version'] == 2


@pytest.mark.django_db
def test_the_previous_release_still_inserts_without_the_new_columns():
    """A rollback runs the code before entry 6 on the migrated tables: its inserts name
    neither `operation` nor `version`, so the database defaults fill them."""
    new_id = uuid.uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            'INSERT INTO integration_samplerecord (uuid, patient_name, amount_value, '
            'currency, note, created_at) VALUES (%s, %s, %s, %s, %s, %s)',
            [new_id.hex if connection.vendor == 'sqlite' else str(new_id), 'enc:x',
             Decimal('1.00'), 'EUR', '', timezone.now()])
        cursor.execute(
            'INSERT INTO integration_remoteaccess (at, actor_type, role, source_site, '
            'source_ip, user_agent, resource, object_id, purpose, access_reason, fields, '
            "outcome, reason) VALUES (%s, 'admin', '', '', '', '', 'sample_record', '', "
            "'', '', '[]', 'granted', '')", [timezone.now()])
        cursor.execute('SELECT version FROM integration_samplerecord')
        assert cursor.fetchone()[0] == 1
        cursor.execute('SELECT operation FROM integration_remoteaccess')
        assert cursor.fetchone()[0] == 'read'


@pytest.mark.django_db
def test_a_sysadmin_write_row_lists_every_field_returned(client, record):
    response = write(client, role='sysadmin', sub=SOMEONE)
    assert response.status_code == 200
    (row,) = rows()
    assert row.fields == response.json()['fields'] == [
        'amount', 'assignee_uuid', 'note', 'patient_name', 'uuid']
    assert row.access_reason == 'write:note'


@pytest.mark.django_db
def test_a_replay_row_lists_what_the_actor_reads_now(client, record):
    request = body()
    post(client, request, role='sysadmin')
    again = post(client, request, role='clinical_manager')
    replay = rows()[-1]
    assert replay.fields == again.json()['fields'] == CLINICAL_FIELDS
    assert replay.access_reason == 'replay:note'


def test_the_access_reason_keeps_the_changed_names_that_fit():
    assert remote.access_reason_for('write', ('note',)) == 'write:note'
    assert remote.access_reason_for('replay', ('a', 'b')) == 'replay:a,b'
    long = tuple(f'field_{i:02d}' for i in range(10))
    reason = remote.access_reason_for('write', long)
    assert len(reason) <= 50 and reason.startswith('write:field_00,')
    shown = reason.split(':', 1)[1].split(',')
    assert shown[-1] == f'+{10 - (len(shown) - 1)}'
    assert remote.access_reason_for('write', ('x' * 64,)) == 'write:+1'
