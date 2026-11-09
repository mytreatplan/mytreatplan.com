"""Remote reads (epic-admin-interface entry 5, AD-4, AD-17, AD-23): the sample record's
endpoint, the access log written and committed before any data, field omission by role,
no-store, the refusals, the staging-only setting (integration.E006), `seed_sample_record`,
the log's append-only rows and their retention. The admin site is played by its signing key
(a fixed, non-secret test seed); no test reaches the network or a KMS."""
import base64
import io
import logging
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from integration import checks, sample
from integration.models import AppendOnly, InterfaceCall, RemoteAccess, SampleRecord
from mytp_core import interface

ADMIN_SEED = b'test-only-interface-seed-admin!!'
PORTAL_SEED = b'test-only-interface-seed-portal!'
OTHER_SEED = b'test-only-interface-seed-other!!'
ADMIN = interface.SigningKey('a1', ADMIN_SEED)
PORTAL = interface.SigningKey('p1', PORTAL_SEED)
DEV_AE = interface.SigningKey('e1', OTHER_SEED)
URL = '/api/v1/remote/sample-record'
ME = '6f1c2b8a-3d4e-4f5a-9b6c-7d8e9f0a1b2c'
SOMEONE = '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d'
NAME = 'Zoë Sample-Patient'
ALL_FIELDS = ['amount', 'assignee_uuid', 'note', 'patient_name', 'uuid']
CLINICAL_FIELDS = ['assignee_uuid', 'note', 'patient_name', 'uuid']


@pytest.fixture(autouse=True)
def interface_settings(settings):
    """dev (region eu), with its own plain test key, admindev's public key and dev-ae's
    (another portal, known but not the admin site); the sample record on."""
    settings.SITE = 'dev'
    settings.REGION = 'eu'
    settings.ROBOTS_NOINDEX = True
    settings.INTERFACE_SAMPLE_RECORDS = True
    settings.INTERFACE_SIGNING_KEY = f'p1:{base64.b64encode(PORTAL_SEED).decode()}'
    settings.INTERFACE_PEER_KEYS = (f'admindev:a1:{ADMIN.public_text},'
                                    f'dev-ae:e1:{DEV_AE.public_text}')
    settings.INTERFACE_PEERS = 'admindev=https://admindev.example.test'
    settings.INTERFACE_ADMIN_SITE = 'admindev'
    interface.reset_signing_key()
    yield settings
    interface.reset_signing_key()


def scope(role='clinical_manager', sub=ME, regions=('ae', 'eu')):
    return {'sub': sub, 'role': role, 'regions': list(regions)}


def read(client, extra=None, key=ADMIN, issuer='admindev', **kwargs):
    token = interface.sign_request('GET', URL, b'', audience='dev', issuer=issuer,
                                   signing_key=key,
                                   extra=scope(**kwargs) if extra is None else extra)
    return client.get(URL, HTTP_AUTHORIZATION=interface.authorization_header(token),
                      REMOTE_ADDR='203.0.113.9', HTTP_USER_AGENT='mytp-admindev-test')


@pytest.fixture
def record(db):
    return SampleRecord.objects.create(patient_name=NAME, amount_value=Decimal('120.00'),
                                       currency='EUR', assignee_uuid=uuid.UUID(ME),
                                       note='Staging sample')


def only_access():
    rows = list(RemoteAccess.objects.all())
    assert len(rows) == 1, rows
    return rows[0]


def assert_no_store(response):
    assert 'no-store' in response['Cache-Control']


# --- Matrix -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_clinical_manager_sees_the_name_but_not_the_amount(client, record):
    response = read(client, role='clinical_manager')
    assert response.status_code == 200
    assert_no_store(response)
    body = response.json()
    assert body == {
        'resource': 'sample_record', 'region': 'eu', 'object_id': str(record.uuid),
        'fields': CLINICAL_FIELDS,
        'data': {'uuid': str(record.uuid), 'patient_name': NAME, 'assignee_uuid': ME,
                 'note': 'Staging sample'},
        'version': 1}
    row = only_access()
    assert (row.actor_type, str(row.actor_uuid), row.role, row.source_site, row.source_ip,
            row.resource, row.object_id, row.fields, row.outcome, row.reason) == (
        'admin', ME, 'clinical_manager', 'admindev', '203.0.113.9', 'sample_record',
        str(record.uuid), CLINICAL_FIELDS, 'granted', '')
    assert (row.user_agent, row.purpose, row.access_reason) == ('mytp-admindev-test', '', '')
    assert row.operation == 'read'


@pytest.mark.django_db
def test_sysadmin_sees_every_field(client, record):
    response = read(client, role='sysadmin', sub=SOMEONE)
    assert response.status_code == 200
    body = response.json()
    assert body['fields'] == ALL_FIELDS
    assert body['data']['amount'] == {'amount': '120.00', 'currency': 'EUR'}
    assert only_access().fields == ALL_FIELDS


@pytest.mark.django_db
def test_clinical_user_reads_a_record_assigned_to_them(client, record):
    response = read(client, role='clinical_user')
    assert response.status_code == 200
    assert response.json()['fields'] == CLINICAL_FIELDS


@pytest.mark.django_db
@pytest.mark.parametrize('assignee', [SOMEONE, None])
def test_clinical_user_is_refused_a_record_not_assigned_to_them(client, record, assignee):
    record.assignee_uuid = assignee
    record.save()
    response = read(client, role='clinical_user')
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'forbidden'
    assert NAME not in response.content.decode()
    assert_no_store(response)
    row = only_access()
    assert (row.outcome, row.reason, str(row.actor_uuid), row.role, row.fields) == (
        'refused', 'assigned', ME, 'clinical_user', [])


@pytest.mark.django_db
@pytest.mark.parametrize('role', ['financial_manager', 'commercial_manager',
                                  'commercial_regional_manager'])
def test_roles_without_the_patients_area_are_refused(client, record, role):
    response = read(client, role=role)
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'forbidden'
    row = only_access()
    assert (row.outcome, row.reason, row.role) == ('refused', 'role', role)


@pytest.mark.django_db
def test_a_region_not_permitted_is_refused(client, record, settings):
    settings.REGION = 'ae'
    response = read(client, role='sysadmin', regions=('eu',))
    assert response.status_code == 403
    row = only_access()
    assert (row.outcome, row.reason) == ('refused', 'region')


@pytest.mark.django_db
@pytest.mark.parametrize('extra', [
    {}, {'sub': 'x', 'role': 'sysadmin', 'regions': ['eu']},
    {'sub': ME, 'role': 'root', 'regions': ['eu']}, {'sub': ME, 'role': 'sysadmin'},
])
def test_a_token_without_a_valid_admin_user_is_refused(client, record, extra):
    response = read(client, extra=extra)
    assert response.status_code == 403
    row = only_access()
    assert (row.outcome, row.reason, row.actor_uuid) == ('refused', 'actor', None)
    assert row.role in ('', 'sysadmin')


@pytest.mark.django_db
def test_log_unwritable_means_503_and_no_data(client, record, monkeypatch, caplog):
    def fail(**kwargs):
        raise RuntimeError('permission denied for table integration_remoteaccess')
    monkeypatch.setattr(RemoteAccess.objects, 'create', fail)
    monkeypatch.setattr(sample, 'build', lambda r: pytest.fail('no data may be built'))
    response = read(client, role='sysadmin')
    assert response.status_code == 503
    assert response.json() == {'error': {
        'code': 'access_log_unavailable',
        'message': 'The access could not be logged, so no data was returned.'}}
    assert_no_store(response)
    assert NAME not in caplog.text and 'permission denied' not in caplog.text


@pytest.mark.django_db
def test_a_refusal_whose_log_fails_is_still_a_403(client, record, monkeypatch):
    monkeypatch.setattr(RemoteAccess.objects, 'create',
                        lambda **kwargs: (_ for _ in ()).throw(RuntimeError('down')))
    assert read(client, role='financial_manager').status_code == 403


@pytest.mark.django_db
def test_another_portal_is_refused_as_an_unknown_key(client, record):
    response = read(client, key=DEV_AE, issuer='dev-ae', role='sysadmin')
    assert response.status_code == 401
    assert response.json()['error']['code'] == 'unknown_key'
    assert not RemoteAccess.objects.exists()
    call = InterfaceCall.objects.get()
    assert (call.accepted, call.reason, call.iss) == (False, 'unknown_key', 'dev-ae')


@pytest.mark.django_db
def test_an_unsigned_read_is_refused(client, record):
    response = client.get(URL)
    assert response.status_code == 401
    assert not RemoteAccess.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_the_access_is_committed_before_the_data_is_built(client, record, monkeypatch):
    built = []
    original = sample.build

    def build(rec):
        # Committed: no transaction is open, and the row lists only the fields returned.
        assert not connection.in_atomic_block
        row = RemoteAccess.objects.get()
        assert (row.outcome, row.fields) == ('granted', CLINICAL_FIELDS)
        built.append(rec.pk)
        return original(rec)
    monkeypatch.setattr(sample, 'build', build)
    response = read(client, role='clinical_manager')
    assert response.status_code == 200 and built == [record.pk]
    assert response.json()['fields'] == only_access().fields


@pytest.mark.django_db
def test_no_record_is_404_and_logged(client):
    response = read(client, role='sysadmin')
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'not_found'
    row = only_access()
    assert (row.outcome, row.reason, row.object_id) == ('refused', 'not_found', '')


@pytest.mark.django_db
def test_the_endpoint_is_404_without_the_setting(client, record, settings):
    settings.INTERFACE_SAMPLE_RECORDS = False
    response = read(client, role='sysadmin')
    assert response.status_code == 404
    assert not RemoteAccess.objects.exists()


@pytest.mark.django_db
def test_another_portal_is_refused_even_with_the_setting_off(client, record, settings):
    settings.INTERFACE_SAMPLE_RECORDS = False
    response = read(client, key=DEV_AE, issuer='dev-ae', role='sysadmin')
    assert response.status_code == 401
    assert response.json()['error']['code'] == 'unknown_key'
    assert not RemoteAccess.objects.exists()
    call = InterfaceCall.objects.get()
    assert (call.accepted, call.reason, call.iss, call.status) == (
        False, 'unknown_key', 'dev-ae', 401)


@pytest.mark.django_db
def test_the_route_is_get_and_post_only(client, record):
    # POST is the remote write (entry 6, test_remote_write); unsigned, it is refused.
    assert client.put(URL).status_code == 405
    assert client.delete(URL).status_code == 405
    assert client.post(URL).status_code == 401


@pytest.mark.django_db
def test_nothing_of_the_record_is_logged(client, record, caplog):
    caplog.set_level(logging.DEBUG)
    assert read(client, role='sysadmin').status_code == 200
    assert NAME not in caplog.text and '120.00' not in caplog.text


@pytest.mark.django_db
def test_the_patient_name_is_stored_encrypted(record):
    with connection.cursor() as cursor:
        cursor.execute('SELECT patient_name FROM integration_samplerecord')
        (stored,) = cursor.fetchone()
    assert NAME not in stored and stored.startswith('enc:')


# --- The access log ---------------------------------------------------------------------

@pytest.mark.django_db
def test_access_rows_are_append_only(client, record):
    read(client, role='sysadmin')
    row = only_access()
    row.reason = 'x'
    with pytest.raises(AppendOnly):
        row.save()
    with pytest.raises(AppendOnly):
        row.delete()
    with pytest.raises(AppendOnly):
        RemoteAccess.objects.filter(pk=row.pk).update(reason='x')


@pytest.mark.django_db
def test_access_rows_are_kept_5_years_by_the_retention_job():
    from eventlog import retention
    now = timezone.now()

    def row(age):
        return RemoteAccess.objects.create(
            at=now - age, actor_type='admin', resource='sample_record', outcome='granted')
    old = row(timedelta(days=5 * 366 + 2))
    kept = row(timedelta(days=5 * 365 - 30))
    assert retention.delete_old_access(now=now) == 1
    assert list(RemoteAccess.objects.values_list('pk', flat=True)) == [kept.pk]
    assert not RemoteAccess.objects.filter(pk=old.pk).exists()
    out = io.StringIO()
    call_command('eventlog_retention', stdout=out)
    assert '0 access log rows deleted' in out.getvalue()


# --- Setting and command ----------------------------------------------------------------

def test_e006_refuses_the_sample_on_a_public_site(settings):
    settings.ROBOTS_NOINDEX = False
    ids = [error.id for error in checks.check_interface_settings()]
    assert checks.SAMPLE_ON_PUBLIC_SITE in ids
    settings.ROBOTS_NOINDEX = True
    assert checks.SAMPLE_ON_PUBLIC_SITE not in [e.id for e in
                                                checks.check_interface_settings()]
    settings.ROBOTS_NOINDEX = False
    settings.INTERFACE_SAMPLE_RECORDS = False
    assert checks.SAMPLE_ON_PUBLIC_SITE not in [e.id for e in
                                                checks.check_interface_settings()]


@pytest.mark.django_db
def test_seed_sample_record_keeps_one_and_prints_no_name():
    out = io.StringIO()
    call_command('seed_sample_record', '--patient-name', NAME, stdout=out)
    call_command('seed_sample_record', '--assignee', ME, '--amount', '75.5', stdout=out)
    record = SampleRecord.objects.get()
    assert (str(record.assignee_uuid), record.amount_value, record.currency) == (
        ME, Decimal('75.50'), 'EUR')
    assert NAME not in out.getvalue() and str(record.uuid) in out.getvalue()


@pytest.mark.django_db
@pytest.mark.parametrize('args', [['--assignee', 'x'], ['--amount', 'ten'],
                                  ['--patient-name', ' ']])
def test_seed_sample_record_refuses_bad_values(args):
    with pytest.raises(CommandError):
        call_command('seed_sample_record', *args, stdout=io.StringIO())
    assert not SampleRecord.objects.exists()


@pytest.mark.django_db
@override_settings(INTERFACE_SAMPLE_RECORDS=False)
def test_seed_sample_record_needs_the_setting():
    with pytest.raises(CommandError, match='INTERFACE_SAMPLE_RECORDS'):
        call_command('seed_sample_record', stdout=io.StringIO())
