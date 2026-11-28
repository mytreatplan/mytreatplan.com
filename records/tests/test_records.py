"""Records upload with status (epic-case-lifecycle 10.5): one test per row of the plan's
I/O matrix, the sweep keeping an attached file, and no file name in logs. The bucket is a
botocore Stubber and clamd a fake (the root conftest's fakes)."""
import logging
import uuid
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from conftest import STORAGE_BUCKET, expect_get, expect_promotion, expect_rejection
from mytp_core import stages
from patients import slots
from patients.models import Patient, StageChange, WorkOrder
from records import service
from records.models import CaseRecords
from storage import purposes, service as storage_service, sweep, tasks
from storage.models import DeletionTrace, StoredObject

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls', 'fake_bucket')]
NAME = 'Pérez scans.zip'
ENCRYPTED = b'stream: Heuristics.Encrypted.Zip FOUND\0'
VIRUS = b'stream: Win.Test.EICAR_HDB-1 FOUND\0'


@pytest.fixture(autouse=True)
def deletions_enqueued_at_once(monkeypatch):
    monkeypatch.setattr(storage_service, 'enqueue_on_commit',
                        lambda task, *args: task.enqueue(*args))


@pytest.fixture(autouse=True)
def purpose_registered():
    purposes.unregister(service.PURPOSE)
    service.register()
    yield


def browser(user):
    client = Client()
    client.force_login(user)
    return client


def page_url(patient):
    return f'/en/patients/{patient.code}/'


def attach_url(patient):
    return f'{page_url(patient)}records/'


def complete_url(patient):
    return f'{page_url(patient)}records/complete/'


def html(response):
    return response.content.decode()


def case(patient, stage=stages.PENDING_REGISTERS_UPLOAD):
    return WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(), stage=stage,
                                    actor_type='system')


def uploaded(user, data=b'zip', purpose=service.PURPOSE):
    return StoredObject.objects.create(purpose=purpose, uploaded_by=user, size=len(data),
                                       content_type='application/zip', original_name=NAME)


def attach(client, patient, obj):
    return client.post(attach_url(patient), {'object_id': str(obj.pk)})


def complete(client, patient, wo):
    wo.refresh_from_db()
    return client.post(complete_url(patient), {'version': wo.version})


def scan(stubber, fake_clamd, obj, data, reply, capture):
    """Run the scan task on `obj` with the bucket holding `data` and clamd answering `reply`."""
    fake_clamd.reply = reply
    expect_get(stubber, obj, data)
    if reply == b'stream: OK\0':
        expect_promotion(stubber, obj)
    else:
        expect_rejection(stubber, obj)
    with capture(execute=True):
        return tasks.scan_object_task.call(str(obj.pk))


def status_of(client, patient):
    page = html(client.get(page_url(patient)))
    found = [part.split('"', 1)[0] for part in page.split('data-records-status="')[1:]]
    return found, page


def messages_of(response):
    return [m.message for m in response.wsgi_request._messages]


def clean_file(doctor, patient, stubber, fake_clamd, capture, wo=None):
    """A case waiting for records with a clean attached file: (client, work order, object)."""
    wo = wo or case(patient)
    obj = uploaded(doctor)
    client = browser(doctor)
    attach(client, patient, obj)
    assert scan(stubber, fake_clamd, obj, b'zip', b'stream: OK\0', capture) == 'clean'
    return client, wo, obj


def test_the_upload_section_shows_while_the_case_waits_for_records(doctor, patient):
    client = browser(doctor)
    wo = case(patient, stages.ASSIGNED)
    page = html(client.get(page_url(patient)))
    assert 'data-case-records' not in page and 'data-records-widget' not in page
    WorkOrder.objects.filter(pk=wo.pk).update(stage=stages.PENDING_REGISTERS_UPLOAD)
    page = html(client.get(page_url(patient)))
    assert 'data-records-widget' in page and 'data-records-complete' in page
    assert 'data-records-status' not in page and 'data-purpose="case_records"' in page
    assert 'data-single-file="true"' in page and f'data-max-size="{1024 ** 3}"' in page
    assert 'case-records.js' in page and 'data-portrait-widget' in page


def test_upload_is_linked_checked_then_waiting(
        doctor, patient, stubber, fake_clamd, django_capture_on_commit_callbacks, caplog):
    caplog.set_level(logging.DEBUG)
    wo = case(patient)
    obj = uploaded(doctor)
    client = browser(doctor)
    response = attach(client, patient, obj)
    assert response.status_code == 302 and response['Location'] == page_url(patient)
    obj.refresh_from_db()
    row = CaseRecords.objects.get()
    assert obj.owner == doctor and row.stored_object == obj and row.work_order == wo
    assert (row.actor_type, row.actor_uuid) == ('doctor', doctor.uid)
    assert status_of(client, patient)[0] == ['checking']
    assert 'Being checked' in status_of(client, patient)[1]

    assert scan(stubber, fake_clamd, obj, b'zip', b'stream: OK\0',
                django_capture_on_commit_callbacks) == 'clean'
    found, page = status_of(client, patient)
    assert found == ['waiting'] and 'Uploaded, waiting' in page
    assert NAME not in caplog.text and patient.code not in caplog.text
    assert NAME not in page


@pytest.mark.parametrize('reply, reason, text', [
    (ENCRYPTED, 'unscannable', 'password-protected'),
    (b'stream: Heuristics.Limits.Exceeded.MaxFiles FOUND\0', 'limit_exceeded',
     'too complex to scan'), (VIRUS, 'infected', 'security check')])
def test_a_refused_file_shows_the_reason_and_blocks_complete(
        doctor, patient, stubber, fake_clamd, django_capture_on_commit_callbacks, reply, reason,
        text):
    wo = case(patient)
    obj = uploaded(doctor)
    client = browser(doctor)
    attach(client, patient, obj)
    assert scan(stubber, fake_clamd, obj, b'zip', reply,
                django_capture_on_commit_callbacks) == reason
    obj.refresh_from_db()
    assert (obj.state, obj.reject_reason) == (
        'rejected', 'unscannable' if reason == 'limit_exceeded' else reason)
    found, page = status_of(client, patient)
    assert found == ['refused'] and 'Refused' in page and text in page
    response = complete(client, patient, wo)
    assert 'Upload your records first' in messages_of(response)[-1]
    wo.refresh_from_db()
    assert wo.stage == stages.PENDING_REGISTERS_UPLOAD


def test_the_scan_that_finishes_before_the_attach_is_still_shown_refused(
        doctor, patient, stubber, fake_clamd, django_capture_on_commit_callbacks):
    case(patient)
    obj = uploaded(doctor)
    scan(stubber, fake_clamd, obj, b'zip', ENCRYPTED, django_capture_on_commit_callbacks)
    client = browser(doctor)
    assert attach(client, patient, obj).status_code == 302
    assert status_of(client, patient)[0] == ['refused']


def test_records_complete_moves_the_case(
        doctor, patient, stubber, fake_clamd, django_capture_on_commit_callbacks):
    client, wo, _obj = clean_file(doctor, patient, stubber, fake_clamd,
                                  django_capture_on_commit_callbacks)
    response = complete(client, patient, wo)
    assert response.status_code == 302
    wo.refresh_from_db()
    assert (wo.stage, wo.version) == (stages.PROCESSING_PRESCRIPTION, 2)
    change = StageChange.objects.get(work_order=wo, move='records_complete')
    assert (change.actor_type, change.actor_uuid) == ('doctor', doctor.uid)
    found, page = status_of(client, patient)
    assert found == ['waiting'] and 'data-records-complete' not in page
    assert 'Uploaded, waiting' in page


@pytest.mark.parametrize('setup', ['none', 'checking'])
def test_complete_too_early_moves_nothing(doctor, patient, setup):
    wo = case(patient)
    client = browser(doctor)
    if setup == 'checking':
        attach(client, patient, uploaded(doctor))
    response = complete(client, patient, wo)
    assert 'Upload your records first' in messages_of(response)[-1]
    wo.refresh_from_db()
    assert (wo.stage, wo.version) == (stages.PENDING_REGISTERS_UPLOAD, 1)
    assert not StageChange.objects.exists()


def test_the_wrong_stage_has_no_upload_and_the_posts_are_refused(doctor, patient):
    wo = case(patient, stages.ASSIGNED)
    obj = uploaded(doctor)
    client = browser(doctor)
    response = attach(client, patient, obj)
    assert 'not waiting for records' in messages_of(response)[-1]
    assert complete(client, patient, wo).status_code == 302
    assert not CaseRecords.objects.exists()
    obj.refresh_from_db()
    assert obj.owner is None
    wo.refresh_from_db()
    assert wo.stage == stages.ASSIGNED


def test_the_status_becomes_processed_when_staff_finish_the_prescription(
        doctor, patient, stubber, fake_clamd, django_capture_on_commit_callbacks):
    client, wo, _obj = clean_file(doctor, patient, stubber, fake_clamd,
                                  django_capture_on_commit_callbacks)
    complete(client, patient, wo)
    WorkOrder.objects.filter(pk=wo.pk).update(stage=stages.PRESCRIPTION_DONE, version=3)
    StageChange.objects.create(work_order=wo, to_stage=stages.PRESCRIPTION_DONE, version=3,
                               actor_type='admin')
    found, page = status_of(client, patient)
    assert found == ['processed'] and 'Processed' in page
    assert 'data-records-widget' not in page


def test_another_doctor_or_an_erased_patient_is_404(
        doctor, other_doctor, patient):
    wo = case(patient)
    obj = uploaded(doctor)
    stranger = browser(other_doctor)
    assert attach(stranger, patient, obj).status_code == 404
    assert complete(stranger, patient, wo).status_code == 404
    assert not CaseRecords.objects.exists()
    obj.refresh_from_db()
    assert obj.owner is None
    Patient.objects.filter(pk=patient.pk).update(status=Patient.ERASED)
    owner = browser(doctor)
    assert attach(owner, patient, obj).status_code == 404
    assert complete(owner, patient, wo).status_code == 404
    assert not CaseRecords.objects.exists()


def test_the_posts_need_a_login_and_a_post(doctor, patient):
    case(patient)
    assert Client().post(attach_url(patient), {'object_id': 'x'}).status_code == 302
    assert browser(doctor).get(attach_url(patient)).status_code == 405
    assert browser(doctor).get(complete_url(patient)).status_code == 405


def test_a_foreign_object_is_refused_and_nothing_is_linked(doctor, other_doctor, patient):
    case(patient)
    client = browser(doctor)
    portrait = uploaded(doctor, purpose='patient_portrait')
    foreign = uploaded(other_doctor)
    owned = uploaded(doctor)
    StoredObject.objects.filter(pk=owned.pk).update(owner=doctor)
    deleting = uploaded(doctor)
    StoredObject.objects.filter(pk=deleting.pk).update(delete_requested_at=timezone.now())
    for obj in (portrait, foreign, owned, deleting):
        assert attach(client, patient, obj).status_code == 404
    assert client.post(attach_url(patient), {'object_id': 'nonsense'}).status_code == 404
    assert client.post(attach_url(patient), {}).status_code == 404
    assert not CaseRecords.objects.exists()
    foreign.refresh_from_db()
    assert foreign.owner is None


def test_a_stale_version_moves_nothing(
        doctor, patient, stubber, fake_clamd, django_capture_on_commit_callbacks):
    client, wo, _obj = clean_file(doctor, patient, stubber, fake_clamd,
                                  django_capture_on_commit_callbacks)
    response = client.post(complete_url(patient), {'version': wo.version + 5})
    assert 'changed meanwhile' in messages_of(response)[-1]
    wo.refresh_from_db()
    assert wo.stage == stages.PENDING_REGISTERS_UPLOAD
    assert client.post(complete_url(patient), {'version': 'x'}).status_code == 302
    wo.refresh_from_db()
    assert wo.stage == stages.PENDING_REGISTERS_UPLOAD


def test_a_second_upload_replaces_the_first_and_deletes_it_with_its_trace(
        doctor, patient, stubber, fake_clamd, django_capture_on_commit_callbacks):
    client, wo, first = clean_file(doctor, patient, stubber, fake_clamd,
                                   django_capture_on_commit_callbacks)
    second = uploaded(doctor)
    assert attach(client, patient, second).status_code == 302
    row = CaseRecords.objects.get()
    assert row.stored_object == second
    first.refresh_from_db()
    assert first.delete_requested_at is not None
    assert status_of(client, patient)[0] == ['checking']
    for key in (f'quarantine/{first.pk}', f'clean/{first.pk}'):
        stubber.add_response('delete_object', {}, {'Bucket': STORAGE_BUCKET, 'Key': key})
    args = [r.args_kwargs['args'] for r in
            DBTaskResult.objects.filter(task_path__endswith='delete_object_task')]
    assert [a[0] for a in args] == [str(first.pk)] and args[0][4] == patient.code
    assert tasks.delete_object_task.call(*args[0]) == 'deleted'
    trace = DeletionTrace.objects.get(object_id=first.pk)
    assert (trace.actor_type, trace.actor_uuid, trace.patient_code) == (
        'user', doctor.uid, patient.code)
    row.refresh_from_db()
    assert row.stored_object == second


def test_an_unattached_upload_is_swept_but_an_attached_one_stays(doctor, patient):
    case(patient)
    attached, loose = uploaded(doctor), uploaded(doctor)
    attach(browser(doctor), patient, attached)
    later = timezone.now() + timedelta(days=2)
    assert sweep.delete_unclaimed(later) == 1
    attached.refresh_from_db()
    assert attached.delete_requested_at is None
    loose.refresh_from_db()
    assert loose.delete_requested_at is not None


def test_the_records_cannot_be_downloaded(doctor, patient):
    case(patient)
    obj = uploaded(doctor)
    attach(browser(doctor), patient, obj)
    StoredObject.objects.filter(pk=obj.pk).update(state=StoredObject.CLEAN)
    obj.refresh_from_db()
    assert not purposes.may_download(purposes.get(service.PURPOSE), doctor, obj)
    assert purposes.get(service.PURPOSE).max_size == 1024 ** 3


def test_the_widget_script_handles_only_its_own_purpose_and_retries_when_online():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    records_js = (root / 'static/js/case-records.js').read_text()
    portrait_js = (root / 'static/js/patient-portrait.js').read_text()
    upload_js = (root / 'storage/static/storage/upload.js').read_text()
    assert "stored.purpose !== 'case_records'" in records_js
    assert "stored.purpose !== 'patient_portrait'" in portrait_js
    assert "'online'" in upload_js and 'retryAll' in upload_js and 'storageReady' in upload_js


def test_the_slot_is_registered_and_the_page_logs_no_names(
        doctor, patient, caplog):
    caplog.set_level(logging.DEBUG)
    case(patient)
    assert any(e['name'] == 'case_records' for e in slots.render_context(
        slots.CASE_ACTIONS, type('R', (), {'user': doctor})(), patient))
    browser(doctor).get(page_url(patient))
    assert NAME not in caplog.text
