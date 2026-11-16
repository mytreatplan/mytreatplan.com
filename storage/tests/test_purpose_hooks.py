"""A purpose's options (epic-doctor-profile entry 4): the declared types allowed at sign,
the content check (`validate`) after a clean scan and before the promotion, the hook after
it (`on_clean`), and the owner's inline link (storage.service.owner_inline_url). A purpose
without them behaves as before (the other storage tests)."""
import logging
import os
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import expect_get, expect_promotion, expect_rejection
from integration.models import RemoteAccess
from storage import purposes, s3, service, tasks
from storage.models import DeletionTrace, StoredObject

from .conftest import BUCKET, MiB, TEST_PURPOSE
from .test_upload import logged_in, no_signing, post_json, start

pytestmark = pytest.mark.django_db

HOOKED = 'hooked_files'
NAME = 'Molina portrait.png'


@pytest.fixture
def hooks():
    """A purpose with every option; `calls` records the hooks' calls, `valid` is the
    check's answer (or an exception to raise)."""
    state = {'calls': [], 'valid': True}

    def validate(obj, read):
        data = read()
        state['calls'].append(('validate', obj.pk, data))
        if isinstance(state['valid'], Exception):
            raise state['valid']
        return state['valid']

    def on_clean(obj):
        state['calls'].append(('on_clean', obj.pk, obj.state))
        if state.get('on_clean_fails'):
            raise RuntimeError('boom')

    purposes.register(HOOKED, lambda user: True, lambda user, obj: obj.owner_id == user.pk,
                      max_size=5 * MiB, content_types=['image/png', 'image/jpeg'],
                      validate=validate, on_clean=on_clean)
    yield state
    purposes.unregister(HOOKED)


def stored(**kwargs):
    defaults = {'purpose': HOOKED, 'size': 3, 'original_name': NAME,
                'content_type': 'image/png'}
    return StoredObject.objects.create(**{**defaults, **kwargs})


# --- Registration and sign ---------------------------------------------------------------

def test_register_checks_the_new_options():
    allow = lambda *args: True  # noqa: E731
    for bad in ['image/png', [], ['Image/PNG'], [''], [' image/png']]:
        with pytest.raises(ValueError):
            purposes.register('other_files', allow, allow, content_types=bad)
    for hook in ({'validate': 'x'}, {'on_clean': 42}):
        with pytest.raises(TypeError):
            purposes.register('other_files', allow, allow, **hook)
    assert purposes.get('other_files') is None
    # Without them: any type, no hooks.
    plain = purposes.get(TEST_PURPOSE)
    assert (plain.content_types, plain.validate, plain.on_clean) == (None, None, None)
    assert plain.allows_type('') and plain.allows_type('model/stl')


def test_allows_type_ignores_parameters_and_case(hooks):
    purpose = purposes.get(HOOKED)
    assert purpose.allows_type('image/png') and purpose.allows_type('IMAGE/JPEG; q=1')
    assert not purpose.allows_type('image/gif') and not purpose.allows_type('')


@pytest.mark.parametrize('content_type', ['image/svg+xml', 'image/gif', 'text/html', '',
                                          'image/heic'])
def test_a_type_the_purpose_does_not_allow_is_refused_at_sign(hooks, doctor, monkeypatch,
                                                              content_type):
    no_signing(monkeypatch)
    response = start(logged_in(doctor), purpose=HOOKED, size=10, content_type=content_type)
    assert response.status_code == 400
    assert response.json()['error'] == {'code': 'bad_request',
                                        'message': 'contentType is not allowed for this purpose'}


def test_an_allowed_type_is_signed(hooks, doctor):
    response = start(logged_in(doctor), purpose=HOOKED, size=10, content_type='image/png')
    assert response.status_code == 200, response.content


def test_the_purpose_size_limit_still_applies(hooks, doctor, monkeypatch):
    no_signing(monkeypatch)
    response = post_json(logged_in(doctor), '/storage/sign/', {
        'method': 'POST', 'key': 'local-1', 'purpose': HOOKED, 'size': 5 * MiB + 1,
        'contentType': 'image/png'})
    assert response.status_code == 413


# --- validate and on_clean in the scan ----------------------------------------------------

def test_a_valid_file_is_promoted_then_on_clean_runs(hooks, fake_clamd, stubber, doctor,
                                                     django_capture_on_commit_callbacks):
    data = os.urandom(3000)
    obj = stored(uploaded_by=doctor, size=len(data))
    expect_get(stubber, obj, data)      # the scan
    expect_get(stubber, obj, data)      # the content check
    expect_promotion(stubber, obj)

    with django_capture_on_commit_callbacks(execute=True):
        assert tasks.scan_object_task.call(str(obj.pk)) == 'clean'

    obj.refresh_from_db()
    assert obj.state == StoredObject.CLEAN
    assert hooks['calls'] == [('validate', obj.pk, data), ('on_clean', obj.pk, 'clean')]


def test_on_clean_waits_for_the_commit(hooks, fake_clamd, stubber, doctor,
                                       django_capture_on_commit_callbacks):
    obj = stored(uploaded_by=doctor)
    expect_get(stubber, obj, b'abc')
    expect_get(stubber, obj, b'abc')
    expect_promotion(stubber, obj)

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        tasks.scan_object_task.call(str(obj.pk))
    assert [call[0] for call in hooks['calls']] == ['validate']
    for callback in callbacks:
        callback()
    assert [call[0] for call in hooks['calls']] == ['validate', 'on_clean']


@pytest.mark.parametrize('answer', [False, ValueError('not an image')])
def test_a_refused_file_is_rejected_as_the_scan_rejects(hooks, fake_clamd, stubber, doctor,
                                                        caplog, answer,
                                                        django_capture_on_commit_callbacks):
    caplog.set_level(logging.DEBUG, logger='storage')
    hooks['valid'] = answer
    obj = stored(uploaded_by=doctor)
    expect_get(stubber, obj, b'abc')
    expect_get(stubber, obj, b'abc')
    expect_rejection(stubber, obj)

    with django_capture_on_commit_callbacks(execute=True):
        assert tasks.scan_object_task.call(str(obj.pk)) == 'invalid_content'

    obj.refresh_from_db()
    assert obj.state == StoredObject.REJECTED
    trace = DeletionTrace.objects.get()
    assert (trace.object_id, trace.actor_type, trace.actor_uuid, trace.patient_code) == (
        obj.pk, 'system', None, '')
    assert [call[0] for call in hooks['calls']] == ['validate']   # no on_clean
    assert f'object {obj.pk} rejected (invalid_content)' in caplog.text
    assert NAME not in caplog.text and 'quarantine/' not in caplog.text


def test_an_s3_failure_while_checking_is_retried(hooks, fake_clamd, stubber, doctor):
    obj = stored(uploaded_by=doctor)
    expect_get(stubber, obj, b'abc')
    stubber.add_client_error('get_object', service_error_code='InternalError',
                             http_status_code=500)

    assert tasks.scan_object_task.call(str(obj.pk)) == 'retry'
    obj.refresh_from_db()
    assert obj.state == StoredObject.QUARANTINE
    assert not DeletionTrace.objects.exists()


def test_a_check_that_swallows_the_s3_failure_is_retried_too(fake_clamd, stubber, doctor):
    def validate(obj, read):
        try:
            read()
        except Exception:  # noqa: BLE001
            return False
        return True

    purposes.register('swallowing', lambda user: True, lambda user, obj: True,
                      validate=validate)
    try:
        obj = stored(purpose='swallowing', uploaded_by=doctor)
        expect_get(stubber, obj, b'abc')
        stubber.add_client_error('get_object', service_error_code='InternalError',
                                 http_status_code=500)
        assert tasks.scan_object_task.call(str(obj.pk)) == 'retry'
    finally:
        purposes.unregister('swallowing')


def test_a_failing_on_clean_leaves_the_object_clean(hooks, fake_clamd, stubber, doctor,
                                                    caplog, monkeypatch,
                                                    django_capture_on_commit_callbacks):
    sent = []
    monkeypatch.setattr(tasks.sentry_sdk, 'capture_message',
                        lambda message, level=None: sent.append(message))
    hooks['on_clean_fails'] = True
    obj = stored(uploaded_by=doctor)
    expect_get(stubber, obj, b'abc')
    expect_get(stubber, obj, b'abc')
    expect_promotion(stubber, obj)

    with django_capture_on_commit_callbacks(execute=True):
        assert tasks.scan_object_task.call(str(obj.pk)) == 'clean'

    obj.refresh_from_db()
    assert obj.state == StoredObject.CLEAN
    assert f'on_clean of object {obj.pk} failed (RuntimeError)' in caplog.text
    assert sent == [f'Stored object on_clean failed: {obj.pk} (RuntimeError)']


def test_a_rerun_after_the_copy_skips_the_check_and_runs_on_clean(
        hooks, fake_clamd, stubber, doctor, django_capture_on_commit_callbacks):
    obj = stored(uploaded_by=doctor)
    stubber.add_client_error('get_object', service_error_code='NoSuchKey',
                             http_status_code=404,
                             expected_params={'Bucket': BUCKET, 'Key': f'quarantine/{obj.pk}'})
    stubber.add_response('head_object', {'ContentLength': 3},
                         {'Bucket': BUCKET, 'Key': f'clean/{obj.pk}'})
    stubber.add_response('delete_object', {}, {'Bucket': BUCKET, 'Key': f'quarantine/{obj.pk}'})

    with django_capture_on_commit_callbacks(execute=True):
        assert tasks.scan_object_task.call(str(obj.pk)) == 'clean'
    assert [call[0] for call in hooks['calls']] == ['on_clean']


# --- The owner's inline link -----------------------------------------------------------

def test_the_owner_gets_an_inline_link_without_an_access_record(hooks, doctor):
    obj = stored(uploaded_by=doctor, owner=doctor, state=StoredObject.CLEAN)
    url = urlsplit(service.owner_inline_url(obj, doctor))
    query = parse_qs(url.query)
    assert url.path == f'/clean/{obj.pk}'
    assert query['X-Amz-Expires'] == ['300']
    assert query['response-content-disposition'] == ['inline']
    assert query['response-content-type'] == ['image/png']
    assert 'Molina' not in url.geturl()
    assert not RemoteAccess.objects.exists()


@pytest.mark.parametrize('case', ['other', 'no_owner', 'quarantine', 'rejected', 'deleting',
                                  'anonymous', 'inactive'])
def test_the_inline_link_is_for_the_owner_of_a_clean_object_only(hooks, doctor, other, case):
    from django.contrib.auth.models import AnonymousUser
    from django.utils import timezone

    fields = {'uploaded_by': doctor, 'owner': doctor, 'state': StoredObject.CLEAN}
    user = doctor
    if case == 'other':
        user = other
    elif case == 'no_owner':
        fields['owner'] = None
    elif case in ('quarantine', 'rejected'):
        fields['state'] = case
    elif case == 'deleting':
        fields['delete_requested_at'] = timezone.now()
    elif case == 'anonymous':
        user = AnonymousUser()
    elif case == 'inactive':
        doctor.is_active = False
    obj = stored(**fields)
    with pytest.raises(service.DownloadRefused):
        service.owner_inline_url(obj, user)
    assert not RemoteAccess.objects.exists()


def test_the_inline_link_needs_the_settings(hooks, doctor, settings):
    settings.STORAGE_BUCKET = ''
    obj = stored(uploaded_by=doctor, owner=doctor, state=StoredObject.CLEAN)
    with pytest.raises(service.NotConfigured):
        service.owner_inline_url(obj, doctor)


def test_presign_download_inline_has_no_file_name():
    url = s3.presign_download('clean/x', content_type='image/png', filename='x.png',
                              inline=True)
    assert parse_qs(urlsplit(url).query)['response-content-disposition'] == ['inline']


# --- unclaimed_after (the sweep) -------------------------------------------------------

def test_register_checks_unclaimed_after():
    from datetime import timedelta
    allow = lambda *args: True  # noqa: E731
    for bad in [0, timedelta(0), timedelta(hours=-1), '6h']:
        with pytest.raises(ValueError):
            purposes.register('other_files', allow, allow, unclaimed_after=bad)
    with pytest.raises(TypeError):
        purposes.register('other_files', allow, allow, in_use='x')
    assert purposes.get('other_files') is None
    assert purposes.get(TEST_PURPOSE).unclaimed_after is None


def test_the_sweep_deletes_unclaimed_objects_of_purposes_that_ask(doctor, monkeypatch):
    from datetime import timedelta

    from django.utils import timezone

    from storage import sweep

    deleted = []
    monkeypatch.setattr(service, 'delete', lambda obj, actor: deleted.append((obj.pk, actor)))
    purposes.register('claimed_files', lambda user: True, lambda user, obj: True,
                      unclaimed_after=timedelta(hours=2), in_use=lambda obj: obj.size == 7)
    try:
        now = timezone.now()
        old = timezone.now() - timedelta(hours=3)
        gone = StoredObject.objects.create(purpose='claimed_files', size=1, created_at=old)
        StoredObject.objects.create(purpose='claimed_files', size=1)               # young
        StoredObject.objects.create(purpose='claimed_files', size=1, created_at=old,
                                    owner=doctor)                                   # claimed
        StoredObject.objects.create(purpose='claimed_files', size=7, created_at=old)  # in use
        StoredObject.objects.create(purpose='claimed_files', size=1, created_at=old,
                                    delete_requested_at=now)                        # deleting
        StoredObject.objects.create(purpose=TEST_PURPOSE, size=1, created_at=old)  # no option

        assert sweep.delete_unclaimed(now) == 1
        assert deleted == [(gone.pk, None)]
    finally:
        purposes.unregister('claimed_files')


def test_the_sweep_command_prints_the_unclaimed_count(stubber):
    import io

    from django.core.management import call_command

    stubber.add_response('list_objects_v2', {}, {'Bucket': BUCKET, 'Prefix': 'quarantine/'})
    out = io.StringIO()
    call_command('sweep_storage', stdout=out)
    assert out.getvalue().strip().endswith('0 unclaimed objects deleted')
