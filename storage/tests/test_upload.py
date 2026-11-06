"""The upload endpoints: /storage/sign/ (each signed multipart operation, the purpose's
permission and size limit, foreign keys and upload ids) and /storage/complete/ (HeadObject
size, the StoredObject with its encrypted name). Presigning runs locally with fake keys;
HeadObject and DeleteObject go to a botocore Stubber."""
import json
import logging
import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.db import connection
from django.test import Client
from django.utils import timezone

from storage import s3
from storage.models import PendingUpload, StoredObject

from .conftest import (BUCKET, FAKE_ACCESS_KEY, FAKE_SECRET_KEY, GiB, TEST_MAX_SIZE,
                       TEST_PURPOSE)

SIGN = '/storage/sign/'
COMPLETE = '/storage/complete/'
BUCKET_HOST = f'{BUCKET}.s3.gra.io.cloud.ovh.net'
NAME = 'Pérez scan 2026.stl'

pytestmark = pytest.mark.django_db


def post_json(client, url, body):
    return client.post(url, data=json.dumps(body), content_type='application/json')


def logged_in(user):
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def staff_client(staff):
    return logged_in(staff)


@pytest.fixture
def doctor_client(doctor):
    return logged_in(doctor)


def start(client, purpose='sample', size=GiB, content_type='model/stl'):
    body = {'method': 'POST', 'key': 'local-123', 'purpose': purpose, 'size': size}
    if content_type:
        body['contentType'] = content_type
    return post_json(client, SIGN, body)


def started(client, **kwargs):
    response = start(client, **kwargs)
    assert response.status_code == 200, response.content
    return response.json()['key']


def signed(response):
    assert response.status_code == 200, response.content
    url = urlsplit(response.json()['url'])
    return url, parse_qs(url.query, keep_blank_values=True)


def assert_presigned(url, query):
    assert url.scheme == 'https' and url.netloc == BUCKET_HOST
    assert query['X-Amz-Algorithm'] == ['AWS4-HMAC-SHA256']
    assert query['X-Amz-Expires'] == [str(s3.SIGNED_URL_EXPIRES)]
    assert query['X-Amz-Credential'][0].startswith(f'{FAKE_ACCESS_KEY}/')
    assert FAKE_SECRET_KEY not in url.geturl()
    assert not any(name.lower().startswith('x-amz-checksum') for name in query)


def no_signing(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError('nothing may be signed')
    monkeypatch.setattr(s3, 'make_client', boom)


# --- Starting an upload ---------------------------------------------------------------

def test_start_makes_a_server_key_and_a_pending_upload(staff_client, staff):
    response = start(staff_client)
    url, query = signed(response)
    assert_presigned(url, query)
    key = response.json()['key']
    object_id = s3.object_id_from_key(key)
    assert key == f'quarantine/{object_id}' and object_id.version == 4
    assert url.path == '/' + key and 'uploads' in query
    assert 'local-123' not in response.content.decode()
    pending = PendingUpload.objects.get()
    assert (pending.pk, pending.user, pending.purpose, pending.size, pending.content_type,
            pending.upload_id) == (object_id, staff, 'sample', GiB, 'model/stl', '')


def test_each_upload_gets_its_own_key(staff_client):
    assert len({started(staff_client) for _ in range(3)}) == 3


@pytest.mark.parametrize('body', [
    {'method': 'PUT', 'key': 'x'},                                  # single PUT: never signed
    {'method': 'PUT', 'key': 'x', 'contentType': 'text/plain'},
    {'method': 'PATCH'}, {'method': 'HEAD'}, {'method': 'post'}, {},
    {'method': 'GET', 'key': 'x'},                                  # GetObject: not signed here
    {'method': 'DELETE', 'key': 'x'},                               # DeleteObject: not signed here
    {'method': 'POST', 'purpose': 'nope', 'size': 10},
    {'method': 'POST', 'size': 10},
    {'method': 'POST', 'purpose': 'sample'},
    {'method': 'POST', 'purpose': 'sample', 'size': 0},
    {'method': 'POST', 'purpose': 'sample', 'size': -1},
    {'method': 'POST', 'purpose': 'sample', 'size': '10'},
    {'method': 'POST', 'purpose': 'sample', 'size': True},
    {'method': 'POST', 'purpose': 'sample', 'size': 10, 'contentType': 'text/html\r\nX-Evil: 1'},
    {'method': 'POST', 'purpose': 'sample', 'size': 10, 'contentType': 42},
])
def test_bad_start_is_400_and_signs_nothing(staff_client, monkeypatch, body):
    no_signing(monkeypatch)
    response = post_json(staff_client, SIGN, body)
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'bad_request'
    assert not PendingUpload.objects.exists()


@pytest.mark.parametrize('raw', [b'not json', b'[1, 2]', b'null', b'"POST"', b'\xff'])
def test_non_object_body_is_400(staff_client, raw):
    response = staff_client.post(SIGN, data=raw, content_type='application/json')
    assert response.status_code == 400


def test_sign_needs_post(staff_client):
    assert staff_client.get(SIGN).status_code == 405
    assert staff_client.get(COMPLETE).status_code == 405


def test_sign_needs_csrf(staff):
    client = Client(enforce_csrf_checks=True)
    client.force_login(staff)
    assert start(client).status_code == 403


# --- Not allowed ----------------------------------------------------------------------

@pytest.mark.parametrize('url', [SIGN, COMPLETE])
def test_anonymous_is_403(client, monkeypatch, url):
    no_signing(monkeypatch)
    response = post_json(client, url, {'method': 'POST', 'purpose': 'sample', 'size': 10})
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'forbidden'


def test_inactive_user_is_403(staff):
    staff.is_active = False
    staff.save()
    client = Client()
    client.force_login(staff)
    assert start(client).status_code == 403


def test_user_without_the_purposes_permission_is_403(other, doctor, monkeypatch):
    no_signing(monkeypatch)
    assert start(logged_in(other), purpose=TEST_PURPOSE, size=10).status_code == 403
    # The sample purpose: staff only.
    assert start(logged_in(doctor), purpose='sample').status_code == 403
    assert not PendingUpload.objects.exists()


def test_sample_purpose_off_is_403_even_for_staff(staff_client, settings, monkeypatch):
    no_signing(monkeypatch)
    settings.STORAGE_SAMPLE_UPLOADS = False
    assert start(staff_client).status_code == 403


def test_permission_is_asked_again_for_each_request(staff_client, settings):
    key = started(staff_client)
    settings.STORAGE_SAMPLE_UPLOADS = False
    response = post_json(staff_client, SIGN, {'method': 'PUT', 'key': key, 'uploadId': 'u',
                                              'partNumber': 1})
    assert response.status_code == 403


# --- Too large ------------------------------------------------------------------------

def test_too_large_is_refused_before_any_upload(doctor_client, monkeypatch):
    no_signing(monkeypatch)
    response = start(doctor_client, purpose=TEST_PURPOSE, size=TEST_MAX_SIZE + 1)
    assert response.status_code == 413
    error = response.json()['error']
    assert error['code'] == 'too_large' and str(TEST_MAX_SIZE) in error['message']
    assert not PendingUpload.objects.exists()


def test_default_limit_is_1_gib(staff_client, monkeypatch):
    assert start(staff_client, size=GiB).status_code == 200
    no_signing(monkeypatch)
    assert start(staff_client, size=GiB + 1).status_code == 413


# --- The later requests ---------------------------------------------------------------

def test_part_list_complete_and_abort_are_signed_for_the_own_key(staff_client):
    key = started(staff_client)
    url, query = signed(post_json(staff_client, SIGN, {'method': 'PUT', 'key': key,
                                                       'uploadId': 'up-1', 'partNumber': 7}))
    assert_presigned(url, query)
    assert url.path == '/' + key
    assert query['uploadId'] == ['up-1'] and query['partNumber'] == ['7']
    assert PendingUpload.objects.get().upload_id == 'up-1'

    url, query = signed(post_json(staff_client, SIGN, {'method': 'GET', 'key': key,
                                                       'uploadId': 'up-1'}))
    assert query['uploadId'] == ['up-1'] and 'partNumber' not in query
    url, query = signed(post_json(staff_client, SIGN, {'method': 'POST', 'key': key,
                                                       'uploadId': 'up-1'}))
    assert query['uploadId'] == ['up-1'] and 'uploads' not in query
    assert PendingUpload.objects.exists()
    url, query = signed(post_json(staff_client, SIGN, {'method': 'DELETE', 'key': key,
                                                       'uploadId': 'up-1'}))
    assert query['uploadId'] == ['up-1']
    # An aborted upload is forgotten.
    assert not PendingUpload.objects.exists()


@pytest.mark.parametrize('method, operation, http_method', [
    ('PUT', s3.UPLOAD_PART, 'PUT'),
    ('GET', s3.LIST_PARTS, 'GET'),
    ('POST', s3.COMPLETE, 'POST'),
    ('DELETE', s3.ABORT, 'DELETE'),
])
def test_each_request_maps_to_one_operation_and_http_method(staff_client, monkeypatch, method,
                                                            operation, http_method):
    """The signature covers the HTTP method, so a URL signed for one operation cannot be
    used for another."""
    key = started(staff_client)
    calls = []

    class FakeClient:
        def generate_presigned_url(self, name, Params, ExpiresIn, HttpMethod):
            calls.append((name, Params, ExpiresIn, HttpMethod))
            return 'https://signed.example/'

    monkeypatch.setattr(s3, 'make_client', FakeClient)
    body = {'method': method, 'key': key, 'uploadId': 'u'}
    if method == 'PUT':
        body['partNumber'] = 1
    assert post_json(staff_client, SIGN, body).status_code == 200
    (name, params, expires, signed_method), = calls
    assert (name, signed_method, expires) == (operation, http_method, s3.SIGNED_URL_EXPIRES)
    assert params['Bucket'] == BUCKET and params['Key'] == key


def test_another_users_key_is_400(staff_client, monkeypatch, db):
    from accounts.models import User
    second = User.objects.create_user('staff2@example.net', 'a-long-test-password-9',
                                      is_staff=True)
    key = started(staff_client)
    no_signing(monkeypatch)
    for body in [{'method': 'PUT', 'key': key, 'uploadId': 'u', 'partNumber': 1},
                 {'method': 'POST', 'key': key, 'uploadId': 'u'},
                 {'method': 'DELETE', 'key': key, 'uploadId': 'u'}]:
        response = post_json(logged_in(second), SIGN, body)
        assert response.status_code == 400
        assert response.json()['error']['code'] == 'bad_request'
    assert PendingUpload.objects.exists()


def test_a_key_never_started_is_400(staff_client, monkeypatch):
    no_signing(monkeypatch)
    response = post_json(staff_client, SIGN, {'method': 'PUT', 'key': f'quarantine/{uuid.uuid4()}',
                                              'uploadId': 'u', 'partNumber': 1})
    assert response.status_code == 400


def test_another_upload_id_is_400(staff_client):
    key = started(staff_client)
    signed(post_json(staff_client, SIGN, {'method': 'PUT', 'key': key, 'uploadId': 'mine',
                                          'partNumber': 1}))
    for method in ['PUT', 'GET', 'POST', 'DELETE']:
        body = {'method': method, 'key': key, 'uploadId': 'someone-elses', 'partNumber': 2}
        response = post_json(staff_client, SIGN, body)
        assert response.status_code == 400
        assert 'uploadId' in response.json()['error']['message']
    assert PendingUpload.objects.get().upload_id == 'mine'


@pytest.mark.parametrize('body', [
    {'method': 'PUT', 'uploadId': 'u'},                                 # part without a number
    {'method': 'PUT', 'uploadId': 'u', 'partNumber': 0},
    {'method': 'PUT', 'uploadId': 'u', 'partNumber': 10001},
    {'method': 'PUT', 'uploadId': 'u', 'partNumber': '3'},
    {'method': 'PUT', 'uploadId': 'u', 'partNumber': True},
    {'method': 'GET', 'uploadId': 'two words'},
    {'method': 'GET', 'uploadId': 'x' * 1025},
    {'method': 'GET', 'uploadId': ['u']},
    {'method': 'PUT', 'uploadId': 'u', 'partNumber': 1, 'key': 'quarantine/../clean/x'},
    {'method': 'GET', 'uploadId': 'u', 'key': 'staging/mytreatplan.dump.age'},
    {'method': 'POST', 'uploadId': 'u', 'key': ''},
    {'method': 'DELETE', 'uploadId': 'u', 'key': None},
])
def test_bad_later_request_is_400(staff_client, monkeypatch, body):
    key = started(staff_client)
    no_signing(monkeypatch)
    body = {'key': key, **body}
    assert post_json(staff_client, SIGN, body).status_code == 400


def test_part_numbers_are_bounded_by_the_declared_size(staff_client):
    key = started(staff_client, size=12 * 1024 * 1024)  # at most 3 parts of 5 MiB
    ok = post_json(staff_client, SIGN, {'method': 'PUT', 'key': key, 'uploadId': 'u',
                                        'partNumber': 3})
    assert ok.status_code == 200
    too_far = post_json(staff_client, SIGN, {'method': 'PUT', 'key': key, 'uploadId': 'u',
                                             'partNumber': 4})
    assert too_far.status_code == 400


def test_sign_without_bucket_settings_is_503_naming_the_setting(staff_client, settings):
    settings.STORAGE_SECRET_KEY = ''
    response = start(staff_client)
    assert response.status_code == 503
    assert response.json()['error'] == {'code': 'not_configured',
                                        'message': 'STORAGE_SECRET_KEY is not set'}


def test_abandoned_pending_uploads_are_forgotten_after_the_lifecycle_period(staff_client, staff):
    old = PendingUpload.objects.create(id=uuid.uuid4(), purpose='sample', user=staff, size=1,
                                       created_at=timezone.now() - timedelta(days=8))
    recent = PendingUpload.objects.create(id=uuid.uuid4(), purpose='sample', user=staff,
                                          size=1, created_at=timezone.now() - timedelta(days=6))
    started(staff_client)
    assert not PendingUpload.objects.filter(pk=old.pk).exists()
    assert PendingUpload.objects.filter(pk=recent.pk).exists()


# --- Completing -----------------------------------------------------------------------

def head(stubber, key, size):
    stubber.add_response('head_object', {'ContentLength': size, 'ETag': '"e-64"'},
                         {'Bucket': BUCKET, 'Key': key})


def test_upload_1_gib_ends_as_a_stored_object_with_an_encrypted_name(staff_client, staff,
                                                                       stubber, caplog):
    caplog.set_level(logging.DEBUG)
    key = started(staff_client)
    head(stubber, key, GiB)
    response = post_json(staff_client, COMPLETE, {'key': key, 'name': f'/Users/x/{NAME}'})
    assert response.status_code == 200, response.content
    object_id = s3.object_id_from_key(key)
    assert response.json() == {'id': str(object_id), 'size': GiB, 'purpose': 'sample'}
    stored = StoredObject.objects.get()
    assert (stored.pk, stored.purpose, stored.owner, stored.uploaded_by, stored.size,
            stored.content_type, stored.original_name, stored.state) == (
        object_id, 'sample', None, staff, GiB, 'model/stl', NAME, StoredObject.QUARANTINE)
    assert stored.key == key
    assert not PendingUpload.objects.exists()
    # The name is encrypted in the database and in no log line.
    with connection.cursor() as cursor:
        cursor.execute('SELECT original_name FROM storage_storedobject')
        raw = cursor.fetchone()[0]
    assert 'Pérez' not in raw and 'scan' not in raw
    assert 'Pérez' not in caplog.text and 'scan' not in caplog.text


def test_complete_twice_answers_the_same(staff_client, stubber):
    key = started(staff_client)
    head(stubber, key, GiB)
    first = post_json(staff_client, COMPLETE, {'key': key, 'name': NAME})
    second = post_json(staff_client, COMPLETE, {'key': key, 'name': NAME})
    assert first.json() == second.json()
    assert StoredObject.objects.count() == 1


def test_concurrent_complete_answers_as_the_first(staff_client, staff, stubber):
    """The other call recorded the object between this one's pending lookup and its
    insert: the duplicate primary key is answered with the stored object, not a 500."""
    key = started(staff_client)
    object_id = s3.object_id_from_key(key)
    head(stubber, key, GiB)
    StoredObject.objects.create(id=object_id, purpose='sample', uploaded_by=staff, size=GiB,
                                original_name=NAME)  # the first call's row; pending still there
    response = post_json(staff_client, COMPLETE, {'key': key, 'name': NAME})
    assert response.status_code == 200
    assert response.json() == {'id': str(object_id), 'size': GiB, 'purpose': 'sample'}
    assert StoredObject.objects.count() == 1


def test_complete_of_another_users_upload_is_400(staff_client, doctor_client, monkeypatch):
    key = started(staff_client)
    no_signing(monkeypatch)
    response = post_json(doctor_client, COMPLETE, {'key': key, 'name': NAME})
    assert response.status_code == 400
    assert not StoredObject.objects.exists() and PendingUpload.objects.exists()


def test_complete_before_s3_has_it_is_400_and_keeps_the_upload(staff_client, stubber):
    key = started(staff_client)
    stubber.add_client_error('head_object', service_error_code='404', http_status_code=404)
    response = post_json(staff_client, COMPLETE, {'key': key, 'name': NAME})
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'not_uploaded'
    assert PendingUpload.objects.exists() and not StoredObject.objects.exists()


def test_wrong_size_deletes_the_object(staff_client, stubber, caplog):
    key = started(staff_client, size=GiB)
    head(stubber, key, GiB + 5)
    stubber.add_response('delete_object', {}, {'Bucket': BUCKET, 'Key': key})
    response = post_json(staff_client, COMPLETE, {'key': key, 'name': NAME})
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'size_mismatch'
    assert not StoredObject.objects.exists() and not PendingUpload.objects.exists()
    assert key not in caplog.text and NAME not in caplog.text


def test_s3_failure_on_complete_is_502_without_secrets(staff_client, stubber, caplog):
    key = started(staff_client)
    stubber.add_client_error('head_object', service_error_code='AccessDenied',
                             service_message=f'bad key {FAKE_SECRET_KEY}', http_status_code=403)
    response = post_json(staff_client, COMPLETE, {'key': key, 'name': NAME})
    assert response.status_code == 502
    assert FAKE_SECRET_KEY not in response.content.decode()
    assert FAKE_SECRET_KEY not in caplog.text and key not in caplog.text


@pytest.mark.parametrize('body', [
    {}, {'key': 'quarantine/x', 'name': NAME}, {'key': 5, 'name': NAME},
    {'name': NAME}, {'key': None, 'name': ''}, {'key': None, 'name': 'x' * 256},
    {'key': None, 'name': 'bad\nname'}, {'key': None, 'name': 7}, {'key': None, 'name': '/'},
])
def test_complete_bad_body_is_400(staff_client, body):
    if body.get('key', 'missing') is None:
        body = {**body, 'key': started(staff_client)}
    assert post_json(staff_client, COMPLETE, body).status_code == 400
    assert not StoredObject.objects.exists()
