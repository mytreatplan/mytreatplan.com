"""The account archive (epic-ordering 9.12): the document, the write and its checks, the
purge's order of work and every row of the plan's I/O matrix. The archive bucket is an
in-memory fake (conftest `archive_bucket`) and `age` is a stand-in, except in the one test
that runs the real binary."""
import base64
import hashlib
import shutil
import subprocess
import uuid
from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command
from django.utils import timezone

from accounts.erasure import erase_user, purge_erased
from accounts.models import FiscalEntity, MakerLogin, Profile, ShippingAddress
from archive import service
from archive.models import AccountArchive
from conftest import read_fake_archive
from invoices.models import Invoice, InvoiceLine
from legal.models import LegalChoice, LegalDocument
from patients.models import Patient, WorkOrder
from payments.models import Order, OrderLine
from storage.models import DeletionTrace

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = 'violet-orbit-canyon-42'
EMAIL = 'ana.garcia@example.com'
PATIENT_NAME = 'Zacarias'
PATIENT_SURNAME = 'Quintanilla'
MAKER_USER = 'maker-login-name-77'
MAKER_NAME = 'Acme Aligners'
NOW = timezone.now()


@pytest.fixture(autouse=True)
def retention(settings):
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 30


@pytest.fixture
def staff():
    return User.objects.create_superuser('staff@example.net', PASSWORD)


def make_doctor(email=EMAIL, phone='+34622918374'):
    return User.objects.create_user(email, PASSWORD, title='dr', first_name='Anabel',
                                    last_name='Garcia', country='ES', phone=phone)


def make_account(email=EMAIL, phone='+34622918374', code='EU-AAAAAAAA'):
    """A doctor with everything the archive holds, an order with its invoice and a credit
    note, and a maker login (which it must not hold)."""
    doctor = make_doctor(email, phone)
    Profile.objects.create(user=doctor, city='Madrid', landline='+34910000000',
                           time_zone='Europe/Madrid', profession='prosthodontist',
                           language='es')
    document = LegalDocument.objects.create(
        kind='terms', title_en='x', title_es='x', body_en='x', body_es='x',
        published_at=NOW - timedelta(days=3))
    LegalChoice.objects.create(user=doctor, document=document, accepted=True,
                               decided_at=NOW - timedelta(days=2))
    FiscalEntity.objects.create(user=doctor, kind='person', first_name='Anabel',
                                last_name='Garcia', tax_id='12345678Z',
                                address_line1='Calle Mayor 1', city='Madrid', country='ES')
    ShippingAddress.objects.create(user=doctor, recipient_name='Clinica Sol',
                                   contact_phone='+34622918374', delivery_notes='Ring twice',
                                   address_line1='Calle Luna 2', city='Madrid', country='ES')
    patient = Patient.objects.create(doctor=doctor, code=code, first_name=PATIENT_NAME,
                                     surname=PATIENT_SURNAME, reference='REF-9',
                                     comments='Allergic to latex', actor_type='doctor')
    WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(),
                             stage='pending_assignment', code='WO-1', actor_type='system')
    MakerLogin.objects.create(user=doctor, maker='other', other_name=MAKER_NAME,
                              username=MAKER_USER, password='s3cret-pass', saved_at=NOW)
    order = Order.objects.create(
        user=doctor, reference=f'KM-{uuid.uuid4().int % 10**6:06d}', state='paid',
        kind='service', currency='EUR', company_code='ES', company_legal_name='MyTP SL',
        net_total=Decimal('100.00'), discount_total=0, ice_total=0, voucher_taken=0,
        vat_total=Decimal('21.00'), total=Decimal('121.00'), actor_type='system',
        paid_at=NOW)
    OrderLine.objects.create(order=order, position=1, code='ALIGN', name='Case',
                             quantity=1, list_amount=100, discount_percent=0,
                             discount_amount=0, ice=0, voucher_share=0, net=100,
                             vat_percent=21, patient_code=code)
    invoice = Invoice.objects.create(
        number=f'ES-EU-2026-{code[-8:]}', kind='order', order=order, company_code='ES',
        instance='EU', year=2026, seller_legal_name='MyTP SL', currency='EUR',
        net_total=100, discount_total=0, ice_total=0, voucher_taken=0, vat_total=21,
        total=121, issue_date=NOW.date(), time_zone='Europe/Madrid', actor_type='system')
    InvoiceLine.objects.create(invoice=invoice, position=1, code='ALIGN', name='Case',
                               quantity=1, list_amount=100, discount_amount=0, ice=0,
                               voucher_share=0, net=100, vat_percent=21, tax_category='S',
                               patient_code=code)
    DeletionTrace.objects.create(object_id=uuid.uuid4(), patient_code=code,
                                 actor_type='user', actor_uuid=doctor.uid)
    return doctor, patient, order, invoice


def decoded(archive_bucket, user_uid):
    return read_fake_archive(archive_bucket.objects[service.object_key(user_uid)]['Body'])


def due(user):
    return User.objects.get(pk=user.pk).purge_after + timedelta(seconds=1)


# --- The document --------------------------------------------------------------------------

def test_the_archive_holds_every_item_of_the_decisions_and_no_maker_data(staff):
    doctor, patient, order, invoice = make_account()
    erase_user(doctor, by=staff)

    raw = service.dumps(service.build(User.objects.get(pk=doctor.pk)))
    document = service.build(User.objects.get(pk=doctor.pk))

    assert document['schema'] == 1
    account = document['account']
    assert account['email'] == EMAIL and account['uid'] == doctor.uid
    assert account['profile']['city'] == 'Madrid'
    assert [c['kind'] for c in document['consents']].count('terms') >= 1
    [fiscal] = document['fiscal_entities']
    assert fiscal['tax_id'] == '12345678Z' and fiscal['country'] == 'ES'
    [shipping] = document['shipping_addresses']
    assert shipping['delivery_notes'] == 'Ring twice'
    [archived_patient] = document['patients']
    assert (archived_patient['first_name'], archived_patient['surname'],
            archived_patient['reference'], archived_patient['comments'],
            archived_patient['status'], archived_patient['code']) == (
        PATIENT_NAME, PATIENT_SURNAME, 'REF-9', 'Allergic to latex', 'erased', patient.code)
    [work_order] = archived_patient['work_orders']
    assert work_order['stage'] == 'pending_assignment'
    [archived_order] = document['orders']
    assert archived_order['reference'] == order.reference
    assert archived_order['lines'][0]['patient_code'] == patient.code
    [summary] = archived_order['invoices']
    assert summary['number'] == invoice.number and summary['patient_codes'] == [patient.code]
    assert [t['patient_code'] for t in document['deletion_traces']] == [patient.code]
    # Never the maker login or name.
    for text in (MAKER_USER, MAKER_NAME, 's3cret-pass'):
        assert text.encode() not in raw


def test_the_document_is_deterministic_json_with_decimals_as_text():
    doctor, *_ = make_account()
    raw = service.dumps(service.build(doctor))
    assert b'"total":"121.00"' in raw


# --- write ---------------------------------------------------------------------------------

def test_write_puts_a_locked_encrypted_object_and_records_it(archive_bucket, settings):
    doctor, *_ = make_account()
    now = timezone.now()

    row = service.write(doctor, now=now)

    key = f'accounts/{doctor.uid}.json.age'
    put = archive_bucket.objects[key]
    assert put['Bucket'] == 'mytp-dev-archive'
    assert put['ObjectLockMode'] == 'COMPLIANCE'
    assert abs((put['ObjectLockRetainUntilDate'] - (now + timedelta(days=9132))).days) <= 1
    assert put['StorageClass'] == 'STANDARD_IA'
    assert put['Body'].startswith(b'AGE-STUB:') and b'Quintanilla' not in put['Body'][:9]
    digest = hashlib.sha256(put['Body']).digest()
    assert put['ChecksumSHA256'] == base64.b64encode(digest).decode()
    assert (row.user_uid, row.object_key, row.sha256, row.size) == (
        doctor.uid, key, digest.hex(), len(put['Body']))
    assert row.retain_until == put['ObjectLockRetainUntilDate']
    # No name, email or file name in the key.
    for text in (EMAIL, 'Anabel', PATIENT_NAME):
        assert text not in key


def test_the_storage_class_comes_from_the_setting(archive_bucket, settings):
    settings.ARCHIVE_STORAGE_CLASS = 'STANDARD'
    doctor, *_ = make_account()
    service.write(doctor)
    assert archive_bucket.objects[service.object_key(doctor.uid)]['StorageClass'] == 'STANDARD'


@pytest.mark.parametrize('field,value,problem', [
    ('ObjectLockMode', 'GOVERNANCE', 'lock mode'),
    ('ObjectLockRetainUntilDate', timezone.now() + timedelta(days=1), 'retain-until'),
    ('ChecksumSHA256', 'AAAA', 'checksum'),
])
def test_a_head_that_differs_is_an_error_and_records_nothing(archive_bucket, monkeypatch,
                                                              field, value, problem):
    doctor, *_ = make_account()
    real = archive_bucket.head_object

    def head(**kwargs):
        result = real(**kwargs)
        return {**result, field: value}
    monkeypatch.setattr(archive_bucket, 'head_object', head)

    with pytest.raises(service.ArchiveError, match=problem):
        service.write(doctor)
    assert not AccountArchive.objects.exists()


def test_a_failed_put_or_head_is_an_error_without_personal_data(archive_bucket):
    doctor, *_ = make_account()
    error = ClientError({'Error': {'Code': 'ServiceUnavailable', 'Message': 'k ' + EMAIL},
                         'ResponseMetadata': {'HTTPStatusCode': 503}}, 'PutObject')
    archive_bucket.put_error = error
    with pytest.raises(service.ArchiveError) as caught:
        service.write(doctor)
    assert EMAIL not in str(caught.value) and 'ServiceUnavailable' in str(caught.value)
    archive_bucket.put_error = None
    archive_bucket.head_error = error
    with pytest.raises(service.ArchiveError):
        service.write(doctor)
    assert not AccountArchive.objects.exists()


def test_an_existing_row_is_verified_and_no_second_object_is_put(archive_bucket):
    doctor, *_ = make_account()
    first = service.write(doctor)
    assert archive_bucket.puts == 1

    again = service.write(doctor)

    assert again == first and archive_bucket.puts == 1
    archive_bucket.head_error = ClientError({'Error': {'Code': '404'}}, 'HeadObject')
    with pytest.raises(service.ArchiveError):
        service.write(doctor)


def test_the_put_sends_the_md5_and_both_head_shapes_verify(archive_bucket):
    first, *_ = make_account()
    second, *_ = make_account('bea@example.org', '+34622918375', 'EU-BBBBBBBB')
    archive_bucket.return_checksum = True
    service.write(first)
    archive_bucket.return_checksum = False  # the store returns only the ETag
    service.write(second)
    for user in (first, second):
        put = archive_bucket.objects[service.object_key(user.uid)]
        assert put['ContentMD5'] == base64.b64encode(hashlib.md5(put['Body']).digest()).decode()
    assert AccountArchive.objects.count() == 2


def test_an_etag_that_differs_is_a_checksum_error(archive_bucket, monkeypatch):
    doctor, *_ = make_account()
    archive_bucket.return_checksum = False
    real = archive_bucket.head_object
    monkeypatch.setattr(archive_bucket, 'head_object',
                        lambda **kw: {**real(**kw), 'ETag': '"deadbeef"'})
    with pytest.raises(service.ArchiveError, match='checksum'):
        service.write(doctor)
    assert not AccountArchive.objects.exists()


def test_put_ok_head_failed_then_the_next_run_records_the_object_without_a_second_put(
        archive_bucket):
    doctor, *_ = make_account()
    real_put = archive_bucket.put_object

    def put(**kwargs):  # the put succeeds, then the store's head fails
        real_put(**kwargs)
        archive_bucket.head_error = ClientError({'Error': {'Code': '500'}}, 'HeadObject')
    archive_bucket.put_object = put
    with pytest.raises(service.ArchiveError):
        service.write(doctor)
    assert archive_bucket.puts == 1 and not AccountArchive.objects.exists()
    archive_bucket.head_error = None

    row = service.write(doctor)

    assert archive_bucket.puts == 1
    put = archive_bucket.objects[service.object_key(doctor.uid)]
    assert (row.size, row.sha256) == (len(put['Body']), hashlib.sha256(put['Body']).hexdigest())
    assert row.retain_until == put['ObjectLockRetainUntilDate']


def test_an_object_already_there_with_an_ended_or_wrong_lock_is_not_adopted(archive_bucket):
    doctor, *_ = make_account()
    service.write(doctor)
    AccountArchive.objects.all().delete()
    item = archive_bucket.objects[service.object_key(doctor.uid)]
    item['ObjectLockRetainUntilDate'] = timezone.now() - timedelta(days=1)
    with pytest.raises(service.ArchiveError, match='retain-until'):
        service.write(doctor)
    item['ObjectLockRetainUntilDate'] = timezone.now() + timedelta(days=5)
    item['ObjectLockMode'] = 'GOVERNANCE'
    with pytest.raises(service.ArchiveError, match='lock mode'):
        service.write(doctor)
    assert archive_bucket.puts == 1 and not AccountArchive.objects.exists()


def test_an_existing_row_is_checked_against_its_retain_until(archive_bucket):
    doctor, *_ = make_account()
    service.write(doctor)
    archive_bucket.objects[service.object_key(doctor.uid)]['ObjectLockRetainUntilDate'] = (
        timezone.now() + timedelta(days=1))
    with pytest.raises(service.ArchiveError, match='retain-until'):
        service.write(doctor)


def test_the_archive_error_text_reaches_the_report_and_the_command_output(
        staff, archive_bucket, sentry_capture):
    import json

    import sentry_sdk
    doctor, *_ = make_account()
    erase_user(doctor, by=staff)
    User.objects.filter(pk=doctor.pk).update(purge_after=timezone.now() - timedelta(days=1))
    archive_bucket.put_error = ClientError(
        {'Error': {'Code': 'ServiceUnavailable'}, 'ResponseMetadata': {'HTTPStatusCode': 503}},
        'PutObject')
    out = StringIO()
    call_command('purge_erased_accounts', stdout=out)
    sentry_sdk.flush()
    assert 'put failed (ServiceUnavailable (HTTP 503))' in out.getvalue()
    assert 'put failed (ServiceUnavailable (HTTP 503))' in json.dumps(sentry_capture.events)
    for secret in (EMAIL, PATIENT_NAME):
        assert secret not in out.getvalue() and secret not in json.dumps(sentry_capture.events)


def test_age_missing_or_failing_is_an_error(monkeypatch, settings):
    from archive import service as svc
    monkeypatch.setattr(svc, 'encrypt', svc.encrypt.real)  # the real one, not the stand-in
    monkeypatch.setattr(svc.shutil, 'which', lambda name: None)
    with pytest.raises(svc.ArchiveError, match='not installed'):
        svc.encrypt(b'x')
    monkeypatch.setattr(svc.shutil, 'which', lambda name: '/bin/false')
    with pytest.raises(svc.ArchiveError, match='age failed'):
        svc.encrypt(b'x')


@pytest.mark.skipif(not shutil.which('age') or not shutil.which('age-keygen'),
                    reason='needs the age binaries')
def test_the_real_age_round_trip(settings, tmp_path):
    from archive import service as svc
    identity = tmp_path / 'id.txt'
    keygen = subprocess.run(['age-keygen', '-o', str(identity)], capture_output=True, text=True)
    recipient = next(line.split()[-1] for line in keygen.stderr.splitlines()
                     if line.startswith('Public key'))
    settings.ARCHIVE_AGE_RECIPIENT = recipient
    cipher = svc.encrypt.real(b'{"hello":"patient"}')
    assert b'patient' not in cipher
    plain = subprocess.run(['age', '-d', '-i', str(identity)], input=cipher,
                           capture_output=True, check=True).stdout
    assert plain == b'{"hello":"patient"}'


# --- Erase hides -----------------------------------------------------------------------------

def test_erase_hides_the_patients_and_keeps_the_rows_for_the_archive(staff):
    doctor, patient, *_ = make_account()

    assert erase_user(doctor, by=staff)

    patient.refresh_from_db()
    assert patient.status == Patient.ERASED and patient.display_name == ''
    assert FiscalEntity.objects.filter(user=doctor).exists()
    assert ShippingAddress.objects.filter(user=doctor).exists()
    assert Profile.objects.get(user=doctor).city == 'Madrid'
    # Neither maker logins nor the archive are kept for them.
    assert not MakerLogin.objects.filter(user=doctor).exists()
    from patients import service as patients_service
    assert patients_service.patient_of(doctor, patient.code) is None
    assert patients_service.names_for_invoice(doctor, [patient.code]) == {}
    assert User.objects.get(pk=doctor.pk).is_active is False


# --- Purge: the matrix ---------------------------------------------------------------------

def test_purge_archives_verifies_then_deletes_the_live_rows(staff, archive_bucket):
    doctor, patient, order, invoice = make_account()
    other, *_ = make_account('bea@example.org', '+34622918375', 'EU-BBBBBBBB')
    # Make the other account's invoice number unique.
    erase_user(doctor, by=staff)
    uid = doctor.uid

    stats = {}
    assert purge_erased(now=due(doctor), stats=stats) == 1

    assert stats == {'failed': 0, 'errors': [], 'expired': 0}
    assert AccountArchive.objects.filter(user_uid=uid).count() == 1
    assert list(archive_bucket.objects) == [f'accounts/{uid}.json.age']
    assert not User.objects.filter(pk=doctor.pk).exists()
    assert not Patient.objects.filter(pk=patient.pk).exists()
    assert not WorkOrder.objects.filter(patient_id=patient.pk).exists()
    assert not FiscalEntity.objects.filter(user_id=doctor.pk).exists()
    assert not ShippingAddress.objects.filter(user_id=doctor.pk).exists()
    assert not Profile.objects.filter(user_id=doctor.pk).exists()
    # The legal record is untouched: the order keeps no user, the invoice is the same.
    order.refresh_from_db()
    assert order.user_id is None
    assert Invoice.objects.get(pk=invoice.pk).number == invoice.number
    assert InvoiceLine.objects.get(invoice=invoice).patient_code == patient.code
    # The archive holds the account, with the patient's name.
    document = decoded(archive_bucket, uid)
    assert document['patients'][0]['first_name'] == PATIENT_NAME
    # The live account is untouched.
    assert User.objects.filter(pk=other.pk).exists()


def test_an_account_without_patients_is_archived_too(staff, archive_bucket):
    doctor = make_doctor()
    erase_user(doctor, by=staff)
    assert purge_erased(now=due(doctor)) == 1
    assert AccountArchive.objects.filter(user_uid=doctor.uid).exists()


def test_not_due_accounts_are_left_alone(staff, archive_bucket):
    doctor = make_doctor()
    erase_user(doctor, by=staff)
    assert purge_erased(now=timezone.now()) == 0
    assert not archive_bucket.objects and User.objects.filter(pk=doctor.pk).exists()


def test_bucket_down_deletes_nothing_reports_without_data_and_is_retried(
        staff, archive_bucket, sentry_capture, capsys):
    import json

    import sentry_sdk
    doctor, patient, *_ = make_account()
    erase_user(doctor, by=staff)
    archive_bucket.put_error = ClientError(
        {'Error': {'Code': 'ServiceUnavailable'}, 'ResponseMetadata': {'HTTPStatusCode': 503}},
        'PutObject')

    out = StringIO()
    call_command('purge_erased_accounts', stdout=out)
    # Not due yet at "now": move the account's date back to make it due.
    User.objects.filter(pk=doctor.pk).update(purge_after=timezone.now() - timedelta(days=1))
    out = StringIO()
    call_command('purge_erased_accounts', stdout=out)
    sentry_sdk.flush()

    assert out.getvalue().splitlines()[0] == '0'
    assert '1 account(s) kept' in out.getvalue()
    assert User.objects.filter(pk=doctor.pk).exists()
    assert Patient.objects.filter(pk=patient.pk).exists()
    assert FiscalEntity.objects.filter(user_id=doctor.pk).exists()
    assert not AccountArchive.objects.exists()
    messages = [e.get('logentry', {}).get('message') or e.get('message')
                for e in sentry_capture.events]
    assert any(m and m.startswith('Account archive error: archive (') for m in messages)
    text = json.dumps(sentry_capture.events)
    for secret in (EMAIL, PATIENT_NAME, PATIENT_SURNAME):
        assert secret not in text and secret not in out.getvalue()

    archive_bucket.put_error = None
    assert purge_erased() == 1
    assert not User.objects.filter(pk=doctor.pk).exists()


def test_a_head_failure_keeps_everything_too(staff, archive_bucket):
    doctor, patient, *_ = make_account()
    erase_user(doctor, by=staff)
    archive_bucket.head_error = ClientError({'Error': {'Code': '500'}}, 'HeadObject')
    stats = {}
    assert purge_erased(now=due(doctor), stats=stats) == 0
    assert stats['failed'] == 1 and Patient.objects.filter(pk=patient.pk).exists()


def test_a_failed_account_does_not_stop_the_others(staff, archive_bucket):
    bad, *_ = make_account()
    good, *_ = make_account('bea@example.org', '+34622918375', 'EU-BBBBBBBB')
    for user in (bad, good):
        erase_user(user, by=staff)
    real = archive_bucket.put_object

    def put(**kwargs):
        if str(bad.uid) in kwargs['Key']:
            raise ClientError({'Error': {'Code': '500'}}, 'PutObject')
        return real(**kwargs)
    archive_bucket.put_object = put

    assert purge_erased(now=timezone.now() + timedelta(days=31), batch_size=5) == 1
    assert User.objects.filter(pk=bad.pk).exists()
    assert not User.objects.filter(pk=good.pk).exists()


@pytest.mark.parametrize('name', ['ARCHIVE_BUCKET', 'ARCHIVE_ACCESS_KEY', 'ARCHIVE_SECRET_KEY',
                                  'ARCHIVE_AGE_RECIPIENT', 'ARCHIVE_RETENTION_YEARS'])
def test_an_unset_archive_setting_refuses_and_deletes_nothing(staff, settings, archive_bucket,
                                                              name):
    doctor, patient, *_ = make_account()
    erase_user(doctor, by=staff)
    setattr(settings, name, None if name == 'ARCHIVE_RETENTION_YEARS' else '')

    with pytest.raises(ImproperlyConfigured, match=name):
        purge_erased(now=due(doctor))
    with pytest.raises(CommandError, match='refused'):
        call_command('purge_erased_accounts', stdout=StringIO())

    assert User.objects.filter(pk=doctor.pk).exists()
    assert Patient.objects.filter(pk=patient.pk).exists()
    assert not archive_bucket.objects


def test_an_existing_row_from_a_failed_run_is_verified_not_rewritten(staff, archive_bucket,
                                                                      monkeypatch):
    doctor, patient, *_ = make_account()
    erase_user(doctor, by=staff)
    # The first run writes the archive, then fails before deleting the live rows.
    from accounts import erasure
    real_delete = erasure._delete_live_rows
    monkeypatch.setattr(erasure, '_delete_live_rows',
                        lambda ids: (_ for _ in ()).throw(RuntimeError('boom')))
    with pytest.raises(RuntimeError):
        purge_erased(now=due(doctor))
    assert AccountArchive.objects.filter(user_uid=doctor.uid).exists()
    assert archive_bucket.puts == 1 and Patient.objects.filter(pk=patient.pk).exists()
    monkeypatch.setattr('accounts.erasure._delete_live_rows', real_delete)

    assert purge_erased(now=due(doctor)) == 1

    assert archive_bucket.puts == 1
    assert not User.objects.filter(pk=doctor.pk).exists()


def test_an_expired_row_is_deleted_by_the_purge(archive_bucket):
    AccountArchive.objects.create(user_uid=uuid.uuid4(), object_key='accounts/x.json.age',
                                  sha256='0' * 64, size=1,
                                  retain_until=timezone.now() - timedelta(days=1))
    kept = AccountArchive.objects.create(user_uid=uuid.uuid4(), object_key='accounts/y.json.age',
                                         sha256='0' * 64, size=1,
                                         retain_until=timezone.now() + timedelta(days=1))
    stats = {}
    assert purge_erased(stats=stats) == 0
    assert stats['expired'] == 1
    assert list(AccountArchive.objects.all()) == [kept]


def test_the_purge_command_prints_only_the_count(staff, archive_bucket):
    doctor, *_ = make_account()
    erase_user(doctor, by=staff)
    User.objects.filter(pk=doctor.pk).update(purge_after=timezone.now() - timedelta(days=1))
    out = StringIO()
    call_command('purge_erased_accounts', stdout=out)
    assert out.getvalue() == '1\n'


# --- Settings and the bucket command ---------------------------------------------------------

def test_missing_archive_setting_names_the_first_empty_one(settings):
    from storage import s3
    assert s3.missing_archive_setting() is None
    settings.ARCHIVE_SECRET_KEY = ''
    assert s3.missing_archive_setting() == 'ARCHIVE_SECRET_KEY'


def test_the_archive_client_uses_its_own_keys(settings):
    import storage.s3 as s3
    settings.STORAGE_ACCESS_KEY = 'STORAGEKEYAAAAAAAAAAA'
    client = s3.make_archive_client.real()
    assert client._request_signer._credentials.access_key == 'ARCHIVEACCESSKEYTEST1'


def test_the_expiration_is_the_retention_plus_a_day(settings):
    settings.ARCHIVE_RETENTION_YEARS = 25
    assert service.expiration_days() == 9133  # ceil(25 * 365.25) + 1
    settings.ARCHIVE_RETENTION_YEARS = 2
    assert service.expiration_days() == 732


def _stubbed_archive_client(monkeypatch):
    from botocore.stub import Stubber

    import storage.s3 as s3
    client = s3.make_archive_client.real()
    monkeypatch.setattr(s3, 'make_archive_client', lambda **kwargs: client)
    return Stubber(client)


def test_storage_bucket_archive_apply_sets_expiration_and_encryption(monkeypatch):
    stub = _stubbed_archive_client(monkeypatch)
    bucket = {'Bucket': 'mytp-dev-archive'}
    lifecycle = {'Rules': [
        {'ID': 'expire-account-archives', 'Filter': {'Prefix': ''}, 'Status': 'Enabled',
         'Expiration': {'Days': 9133}, 'NoncurrentVersionExpiration': {'NoncurrentDays': 1}},
        {'ID': 'remove-expired-delete-markers', 'Filter': {'Prefix': ''}, 'Status': 'Enabled',
         'Expiration': {'ExpiredObjectDeleteMarker': True}}]}
    sse = {'Rules': [{'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]}
    stub.add_response('put_bucket_lifecycle_configuration', {},
                      {**bucket, 'LifecycleConfiguration': lifecycle})
    stub.add_response('put_bucket_encryption', {},
                      {**bucket, 'ServerSideEncryptionConfiguration': sse})
    stub.add_response('get_bucket_lifecycle_configuration', lifecycle, bucket)
    stub.add_response('get_bucket_encryption', {'ServerSideEncryptionConfiguration': sse}, bucket)
    stub.add_response('get_object_lock_configuration', {'ObjectLockConfiguration': {
        'ObjectLockEnabled': 'Enabled'}}, bucket)
    out = StringIO()
    with stub:
        call_command('storage_bucket', '--archive', '--apply', stdout=out)
    text = out.getvalue()
    assert 'set lifecycle: ok' in text and 'set encryption: ok' in text
    assert 'expiration 9133 day(s) noncurrent 1 day(s)' in text and 'delete-markers' in text
    assert 'object lock: Enabled' in text
    assert 'ARCHIVEACCESSKEYTEST1' not in text


def test_storage_bucket_archive_needs_the_archive_settings(settings):
    settings.ARCHIVE_BUCKET = ''
    with pytest.raises(CommandError, match='ARCHIVE_BUCKET'):
        call_command('storage_bucket', '--archive', '--inspect', stdout=StringIO())
