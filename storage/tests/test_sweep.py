"""The hourly `sweepstorage` job (manage.py sweep_storage, storage.sweep): row-less
quarantine objects older than a day (or over their limit) deleted, expired pending
uploads forgotten, pending deletions enqueued again; counts only in the output. S3 is a
botocore Stubber."""
import io
import logging
import uuid
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from mytp_core import jobs
from storage import service, sweep
from storage.models import PendingUpload, StoredObject

from .conftest import BUCKET, GiB, REPO_DEPLOY, TEST_MAX_SIZE, TEST_PURPOSE

pytestmark = pytest.mark.django_db
LIST = {'Bucket': BUCKET, 'Prefix': 'quarantine/'}


def item(object_id, *, age, size=1024):
    return {'Key': f'quarantine/{object_id}', 'Size': size,
            'LastModified': timezone.now() - age}


def run():
    out = io.StringIO()
    call_command('sweep_storage', stdout=out)
    return out.getvalue()


def test_rowless_objects_older_than_a_day_are_deleted(stubber, staff, caplog):
    caplog.set_level(logging.DEBUG, logger='storage')
    orphan, recent, recorded = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    StoredObject.objects.create(id=recorded, purpose='sample', uploaded_by=staff, size=1)
    stubber.add_response('list_objects_v2', {'Contents': [
        item(orphan, age=timedelta(days=1, minutes=1)),
        item(recent, age=timedelta(hours=23)),          # its /complete/ may still come
        item(recorded, age=timedelta(days=30)),
        {'Key': 'quarantine/not-a-uuid', 'Size': 1, 'LastModified': timezone.now()},
    ]}, LIST)
    stubber.add_response('delete_object', {}, {'Bucket': BUCKET, 'Key': f'quarantine/{orphan}'})

    out = run()

    assert 'orphan objects 1 deleted' in out
    assert str(orphan) not in out and 'quarantine/' not in out
    assert str(orphan) not in caplog.text


def test_rowless_object_over_its_limit_is_deleted_at_once(stubber, doctor):
    over, within = uuid.uuid4(), uuid.uuid4()
    for object_id in (over, within):
        PendingUpload.objects.create(id=object_id, purpose=TEST_PURPOSE, user=doctor, size=10)
    stubber.add_response('list_objects_v2', {'Contents': [
        item(over, age=timedelta(minutes=1), size=TEST_MAX_SIZE + 1),
        item(within, age=timedelta(minutes=1), size=TEST_MAX_SIZE),
    ]}, LIST)
    stubber.add_response('delete_object', {}, {'Bucket': BUCKET, 'Key': f'quarantine/{over}'})
    assert sweep.sweep()['orphans'] == 1


def test_rowless_object_without_a_pending_upload_uses_the_largest_limit(stubber):
    big = uuid.uuid4()
    stubber.add_response('list_objects_v2', {'Contents': [
        item(big, age=timedelta(minutes=1), size=GiB + 1)]}, LIST)
    stubber.add_response('delete_object', {}, {'Bucket': BUCKET, 'Key': f'quarantine/{big}'})
    assert sweep.sweep()['orphans'] == 1


def test_expired_pending_uploads_are_forgotten(stubber, staff):
    stubber.add_response('list_objects_v2', {}, LIST)
    old = PendingUpload.objects.create(id=uuid.uuid4(), purpose='sample', user=staff, size=1,
                                       created_at=timezone.now() - timedelta(days=8))
    kept = PendingUpload.objects.create(id=uuid.uuid4(), purpose='sample', user=staff, size=1,
                                        created_at=timezone.now() - timedelta(days=6))
    assert '1 pending uploads forgotten' in run()
    assert not PendingUpload.objects.filter(pk=old.pk).exists()
    assert PendingUpload.objects.filter(pk=kept.pk).exists()


def test_pending_deletions_are_enqueued_again(stubber, staff):
    stubber.add_response('list_objects_v2', {}, LIST)
    stuck = StoredObject.objects.create(purpose='sample', size=1,
                                        delete_requested_at=timezone.now() - timedelta(hours=1))
    StoredObject.objects.create(purpose='sample', size=1,
                                delete_requested_at=timezone.now() - timedelta(minutes=5))
    StoredObject.objects.create(purpose='sample', size=1)

    assert '1 deletions enqueued again' in run()

    (queued,) = DBTaskResult.objects.all()
    assert queued.task_path == 'storage.tasks.delete_object_task'
    assert queued.args_kwargs == {'args': [str(stuck.pk), 'system', ''], 'kwargs': {}}


def test_without_bucket_settings_the_bucket_step_is_skipped(settings, monkeypatch):
    settings.STORAGE_BUCKET = ''
    monkeypatch.setattr('storage.s3.make_client', lambda: pytest.fail('no S3 call'))
    assert 'orphan objects skipped (bucket not configured)' in run()


def test_the_job_runs_hourly():
    job = jobs.load_jobs(REPO_DEPLOY / 'jobs.conf')['sweepstorage']
    assert (job.command, job.args, job.schedule) == ('sweep_storage', (), '*-*-* *:20:00 UTC')


# --- delete_requested_at --------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_delete_marks_the_row_in_the_callers_transaction(staff):
    from django.db import transaction
    obj = StoredObject.objects.create(purpose='sample', uploaded_by=staff, size=1)
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            service.delete(obj, staff)
            assert StoredObject.objects.get(pk=obj.pk).delete_requested_at is not None
            raise RuntimeError('the caller failed')
    assert StoredObject.objects.get(pk=obj.pk).delete_requested_at is None
    assert not DBTaskResult.objects.exists()

    service.delete(StoredObject.objects.get(pk=obj.pk), staff)
    assert StoredObject.objects.get(pk=obj.pk).delete_requested_at is not None


def test_no_download_once_deletion_is_asked(staff, monkeypatch):
    obj = StoredObject.objects.create(purpose='sample', uploaded_by=staff, size=1)
    service.delete(obj, staff)
    monkeypatch.setattr('storage.s3.presign_download', lambda *a, **k: pytest.fail('no URL'))
    with pytest.raises(service.DownloadRefused):
        service.download_url(StoredObject.objects.get(pk=obj.pk), staff, 'sample_page')
