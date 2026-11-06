"""Deleting (storage.service.delete and delete_object_task): enqueued on commit with the
object's UUID only; the task removes the S3 object under both prefixes and the row, and
leaves one DeletionTrace however often it runs; an S3 failure keeps everything and
retries."""
import io
import logging
import uuid

import pytest
from django.core.management import call_command
from django.db import transaction
from django_tasks_db.models import DBTaskResult

from storage import s3, service, tasks
from storage.models import DeletionTrace, StoredObject

from .conftest import BUCKET, FAKE_SECRET_KEY, GiB

NAME = 'Ruiz implant plan.pdf'


def run_worker():
    call_command('db_worker', '--batch', '--no-startup-delay', '--no-reload', verbosity=0,
                 stdout=io.StringIO())


def stored(uploaded_by=None):
    return StoredObject.objects.create(purpose='sample', uploaded_by=uploaded_by, size=GiB,
                                       original_name=NAME)


def expect_deletes(stubber, object_id):
    for key in (f'quarantine/{object_id}', f'clean/{object_id}'):
        stubber.add_response('delete_object', {}, {'Bucket': BUCKET, 'Key': key})


@pytest.mark.django_db(transaction=True)
def test_delete_removes_object_and_row_and_leaves_a_trace(staff, stubber, caplog):
    caplog.set_level(logging.DEBUG, logger='storage')
    caplog.set_level(logging.DEBUG, logger='django.tasks')
    obj = stored(uploaded_by=staff)
    expect_deletes(stubber, obj.pk)
    with transaction.atomic():
        service.delete(obj, staff)
        assert not DBTaskResult.objects.exists()  # only once the transaction commits

    queued = DBTaskResult.objects.get()
    # The object's UUID and the actor only: no key, URL or file name (AD-23).
    assert queued.args_kwargs == {'args': [str(obj.pk), 'user', str(staff.uid)], 'kwargs': {}}

    run_worker()

    queued.refresh_from_db()
    assert queued.return_value == 'deleted'
    assert not StoredObject.objects.filter(pk=obj.pk).exists()
    trace = DeletionTrace.objects.get()
    assert (trace.object_id, trace.patient_code, trace.actor_type, trace.actor_uuid) == (
        obj.pk, '', 'user', staff.uid)
    assert trace.deleted_at is not None
    assert NAME not in caplog.text and 'quarantine/' not in caplog.text


@pytest.mark.django_db(transaction=True)
def test_delete_task_twice_leaves_one_trace_and_no_error(staff, stubber):
    obj = stored(uploaded_by=staff)
    expect_deletes(stubber, obj.pk)
    service.delete(obj, staff)
    service.delete(obj, staff)

    run_worker()

    assert sorted(r.return_value for r in DBTaskResult.objects.all()) == [
        'already deleted', 'deleted']
    assert all(r.status == 'SUCCESSFUL' for r in DBTaskResult.objects.all())
    assert DeletionTrace.objects.count() == 1
    assert not StoredObject.objects.exists()


@pytest.mark.django_db
def test_delete_by_the_system(stubber):
    obj = stored()
    expect_deletes(stubber, obj.pk)
    assert tasks.delete_object_task.call(str(obj.pk)) == 'deleted'
    trace = DeletionTrace.objects.get()
    assert (trace.actor_type, trace.actor_uuid) == ('system', None)


@pytest.mark.django_db
def test_s3_failure_keeps_everything_and_retries_later(staff, stubber, caplog):
    obj = stored(uploaded_by=staff)
    stubber.add_client_error('delete_object', service_error_code='AccessDenied',
                             service_message=f'denied {FAKE_SECRET_KEY}', http_status_code=403)

    result = tasks.delete_object_task.call(str(obj.pk), 'user', str(staff.uid))

    assert result == 'retry'
    assert StoredObject.objects.filter(pk=obj.pk).exists()
    assert not DeletionTrace.objects.exists()
    retry = DBTaskResult.objects.get()
    assert retry.args_kwargs == {'args': [str(obj.pk), 'user', str(staff.uid), 2], 'kwargs': {}}
    assert retry.run_after is not None
    assert 'AccessDenied (HTTP 403)' in caplog.text
    assert FAKE_SECRET_KEY not in caplog.text and NAME not in caplog.text


@pytest.mark.django_db
def test_the_last_failed_attempt_is_reported_without_retrying(stubber, monkeypatch):
    obj = stored()
    stubber.add_client_error('delete_object', service_error_code='InternalError',
                             http_status_code=500)
    reports = []
    monkeypatch.setattr(tasks.sentry_sdk, 'capture_message',
                        lambda message, level=None: reports.append(message))

    result = tasks.delete_object_task.call(str(obj.pk), 'system', '', tasks.MAX_ATTEMPTS)

    assert result == 'failed'
    assert not DBTaskResult.objects.exists()
    assert StoredObject.objects.filter(pk=obj.pk).exists()
    (report,) = reports
    assert str(obj.pk) in report and NAME not in report and 'quarantine/' not in report


@pytest.mark.django_db
def test_a_bad_actor_type_is_refused():
    with pytest.raises(ValueError):
        tasks.delete_object_task.call(str(uuid.uuid4()), 'robot', '')


def test_object_keys_cover_both_prefixes():
    object_id = uuid.uuid4()
    assert s3.object_keys(object_id) == [f'quarantine/{object_id}', f'clean/{object_id}']


@pytest.mark.django_db
def test_s3_request_urls_stay_out_of_sentry(sentry_capture, monkeypatch):
    """Sentry's boto3 integration records each request's URL (bucket and key) as a
    breadcrumb; storage's S3 calls drop them, so a later error does not carry them."""
    import sentry_sdk

    class Client:
        def delete_object(self, Bucket, Key):
            sentry_sdk.add_breadcrumb(category='httplib', type='http',
                                      data={'aws.request.url': f'https://{Bucket}.example/{Key}'})

    object_id = uuid.uuid4()
    s3.delete_keys(s3.object_keys(object_id), client=Client())
    sentry_sdk.add_breadcrumb(message='after the S3 calls')
    sentry_sdk.capture_message('a later error')
    sentry_sdk.flush()

    (event,) = sentry_capture.events
    crumbs = (event.get('breadcrumbs') or {}).get('values', [])
    # (Other breadcrumbs, e.g. SQL from earlier tests, may be there too.)
    assert 'after the S3 calls' in [c.get('message') for c in crumbs]
    assert not [c for c in crumbs if 'aws.request.url' in (c.get('data') or {})]
    assert str(object_id) not in str(event) and BUCKET not in str(event)
