"""Upload spike page and endpoints: the staff and setting gates, each signed S3 operation,
and the 400s. Presigning runs locally with fake keys; HeadObject is stubbed."""
import importlib
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.stub import Stubber
from django.conf import settings as django_settings
from django.test import override_settings
from django.urls import clear_url_caches

import mytreatplan.urls
from accounts.models import User
from upload_spike import s3

from .conftest import FAKE_ACCESS_KEY, FAKE_SECRET_KEY

PAGE = '/en/upload-spike/'
SIGN = '/en/upload-spike/sign/'
OBJECT = '/en/upload-spike/object/'
PASSWORD = 'a-long-test-password-9'
BUCKET_HOST = 'mytp-dev-files.s3.gra.io.cloud.ovh.net'


@pytest.fixture
def staff(db):
    return User.objects.create_user('staff@example.net', PASSWORD, is_staff=True)


@pytest.fixture
def staff_client(client, staff):
    client.force_login(staff)
    return client


def post_json(client, url, body):
    return client.post(url, data=json.dumps(body), content_type='application/json')


def signed(client, body):
    response = post_json(client, SIGN, body)
    assert response.status_code == 200, response.content
    data = response.json()
    url = urlsplit(data['url'])
    return data, url, parse_qs(url.query, keep_blank_values=True)


def a_key():
    return f'spike/{uuid.uuid4()}'


# --- Gates ----------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('url', [PAGE, SIGN, OBJECT])
def test_anonymous_gets_404(client, url):
    assert client.get(url).status_code == 404
    assert client.post(url, data='{}', content_type='application/json').status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize('url', [PAGE, SIGN, OBJECT])
def test_non_staff_gets_404(client, url):
    client.force_login(User.objects.create_user('doctor@example.net', PASSWORD))
    assert client.get(url).status_code == 404
    assert post_json(client, url, {'method': 'POST', 'key': 'x'}).status_code == 404


@pytest.mark.parametrize('url', [PAGE, SIGN, OBJECT])
def test_setting_off_gets_404_even_for_staff(staff_client, settings, url):
    settings.UPLOAD_SPIKE_ENABLED = False
    assert staff_client.get(url).status_code == 404
    assert post_json(staff_client, url, {'method': 'POST', 'key': 'x'}).status_code == 404


def test_staff_sees_the_page(staff_client):
    response = staff_client.get(PAGE)
    assert response.status_code == 200
    html = response.content.decode()
    assert 'js/uppy.min.js' in html and 'css/uppy.min.css' in html
    assert 'upload_spike/upload-spike.js' in html
    assert f'data-sign-url="{SIGN}"' in html
    assert f'data-object-url="{OBJECT}"' in html
    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert 'mytp-dev-files' in html
    assert FAKE_ACCESS_KEY not in html and FAKE_SECRET_KEY not in html
    assert 'no-cache' in response['Cache-Control'] or 'max-age=0' in response['Cache-Control']


def test_page_names_a_missing_setting(staff_client, settings):
    settings.UPLOAD_SPIKE_S3_BUCKET = ''
    assert 'UPLOAD_SPIKE_S3_BUCKET is not set' in staff_client.get(PAGE).content.decode()


def test_sign_needs_post(staff_client):
    assert staff_client.get(SIGN).status_code == 405


def test_sign_needs_csrf(staff):
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    client.force_login(staff)
    assert post_json(client, SIGN, {'method': 'POST', 'key': 'x'}).status_code == 403


# --- Signed operations ----------------------------------------------------------------

def _assert_presigned(url, query, method_marker=None):
    assert url.scheme == 'https'
    assert url.netloc == BUCKET_HOST
    assert query['X-Amz-Algorithm'] == ['AWS4-HMAC-SHA256']
    assert query['X-Amz-Expires'] == [str(s3.SIGNED_URL_EXPIRES)]
    credential = query['X-Amz-Credential'][0]
    assert credential.startswith(f'{FAKE_ACCESS_KEY}/') and '/gra/s3/aws4_request' in credential
    assert FAKE_SECRET_KEY not in url.geturl()
    # No CRC checksum the browser could not match.
    assert not any(name.lower().startswith('x-amz-checksum') for name in query)
    assert not any(name.lower().startswith('x-amz-sdk-checksum') for name in query)


def test_put_object_gets_a_new_uuid_key(staff_client):
    data, url, query = signed(staff_client, {'method': 'PUT', 'key': 'my scan.stl',
                                             'contentType': 'model/stl'})
    _assert_presigned(url, query)
    key = data['key']
    assert s3.is_spike_key(key)
    assert url.path == '/' + key
    assert 'scan' not in data['url']
    assert data['headers'] == {'Content-Type': 'model/stl'}
    assert 'content-type' in query['X-Amz-SignedHeaders'][0].split(';')
    assert 'uploadId' not in query and 'uploads' not in query


def test_put_object_without_content_type_signs_no_headers(staff_client):
    data, url, query = signed(staff_client, {'method': 'PUT', 'key': 'x'})
    assert 'headers' not in data
    assert query['X-Amz-SignedHeaders'] == ['host']


def test_create_multipart_upload(staff_client):
    data, url, query = signed(staff_client, {'method': 'POST', 'key': 'patient-name.dcm'})
    _assert_presigned(url, query)
    assert s3.is_spike_key(data['key'])
    assert url.path == '/' + data['key']
    assert 'uploads' in query
    assert 'patient-name' not in data['url']


def test_each_new_object_gets_its_own_key(staff_client):
    keys = {signed(staff_client, {'method': 'POST', 'key': 'same'})[0]['key'] for _ in range(3)}
    assert len(keys) == 3


def test_upload_part(staff_client):
    key = a_key()
    data, url, query = signed(staff_client, {'method': 'PUT', 'key': key,
                                             'uploadId': 'upload-1', 'partNumber': 7})
    _assert_presigned(url, query)
    assert url.path == '/' + key
    assert query['uploadId'] == ['upload-1']
    assert query['partNumber'] == ['7']
    assert 'key' not in data


def test_list_parts(staff_client):
    key = a_key()
    data, url, query = signed(staff_client, {'method': 'GET', 'key': key, 'uploadId': 'u-2'})
    _assert_presigned(url, query)
    assert url.path == '/' + key
    assert query['uploadId'] == ['u-2']
    assert 'partNumber' not in query


def test_complete_multipart_upload(staff_client):
    key = a_key()
    data, url, query = signed(staff_client, {'method': 'POST', 'key': key, 'uploadId': 'u-3'})
    _assert_presigned(url, query)
    assert query['uploadId'] == ['u-3']
    assert 'uploads' not in query


def test_abort_multipart_upload(staff_client):
    key = a_key()
    data, url, query = signed(staff_client, {'method': 'DELETE', 'key': key, 'uploadId': 'u-4'})
    _assert_presigned(url, query)
    assert query['uploadId'] == ['u-4']


@pytest.mark.parametrize('body, operation, method', [
    ({'method': 'PUT'}, 'put_object', 'PUT'),
    ({'method': 'POST'}, 'create_multipart_upload', 'POST'),
    ({'method': 'PUT', 'uploadId': 'u', 'partNumber': 1}, 'upload_part', 'PUT'),
    ({'method': 'GET', 'uploadId': 'u'}, 'list_parts', 'GET'),
    ({'method': 'POST', 'uploadId': 'u'}, 'complete_multipart_upload', 'POST'),
    ({'method': 'DELETE', 'uploadId': 'u'}, 'abort_multipart_upload', 'DELETE'),
])
def test_each_request_maps_to_one_operation_and_http_method(body, operation, method):
    """The operation and the HTTP method the URL is signed for (the signature covers the
    method, so a URL signed for one cannot be used for another)."""
    calls = []

    class Client:
        def generate_presigned_url(self, name, Params, ExpiresIn, HttpMethod):
            calls.append((name, Params, ExpiresIn, HttpMethod))
            return 'https://signed.example/'

    s3.presign({'key': a_key(), **body}, client=Client())
    (name, params, expires, http_method), = calls
    assert (name, http_method, expires) == (operation, method, s3.SIGNED_URL_EXPIRES)
    assert params['Bucket'] == 'mytp-dev-files'


# --- Bad sign requests ----------------------------------------------------------------

@pytest.mark.parametrize('body', [
    {'method': 'PATCH', 'key': 'spike/x'},
    {'method': 'HEAD'},
    {'method': 'put'},
    {},
    {'method': 'GET', 'key': None},                             # GetObject: not signed here
    {'method': 'DELETE', 'key': None},                          # DeleteObject: not signed here
    {'method': 'PUT', 'key': None, 'uploadId': 'u'},            # part without a number
    {'method': 'PUT', 'key': None, 'uploadId': 'u', 'partNumber': 0},
    {'method': 'PUT', 'key': None, 'uploadId': 'u', 'partNumber': 10001},
    {'method': 'PUT', 'key': None, 'uploadId': 'u', 'partNumber': '3'},
    {'method': 'PUT', 'key': None, 'uploadId': 'u', 'partNumber': True},
    {'method': 'PUT', 'key': 'spike/../backups/x', 'uploadId': 'u', 'partNumber': 1},
    {'method': 'GET', 'key': 'staging/mytreatplan.dump.age', 'uploadId': 'u'},
    {'method': 'POST', 'key': '', 'uploadId': 'u'},
    {'method': 'DELETE', 'uploadId': 'u'},                      # missing key
    {'method': 'GET', 'key': None, 'uploadId': 'two words'},
    {'method': 'GET', 'key': None, 'uploadId': 'x' * 1025},
    {'method': 'GET', 'key': None, 'uploadId': ['u']},
    {'method': 'PUT', 'contentType': 'text/html\r\nX-Evil: 1'},
    {'method': 'PUT', 'contentType': 42},
])
def test_bad_sign_request_is_400_and_signs_nothing(staff_client, monkeypatch, body):
    def boom(*args, **kwargs):
        raise AssertionError('nothing may be signed')
    monkeypatch.setattr(s3, 'make_client', boom)
    if 'key' in body and body['key'] is None:
        body = {**body, 'key': a_key()}
    response = post_json(staff_client, SIGN, body)
    assert response.status_code == 400
    error = response.json()['error']
    assert error['code'] == 'bad_request' and error['message']


@pytest.mark.parametrize('raw', [b'not json', b'[1, 2]', b'null', b'"PUT"', b'\xff'])
def test_non_object_body_is_400(staff_client, raw):
    response = staff_client.post(SIGN, data=raw, content_type='application/json')
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'bad_request'


def test_sign_without_bucket_settings_is_503_naming_the_setting(staff_client, settings):
    settings.UPLOAD_SPIKE_S3_SECRET_KEY = ''
    response = post_json(staff_client, SIGN, {'method': 'POST', 'key': 'x'})
    assert response.status_code == 503
    assert response.json()['error'] == {'code': 'not_configured',
                                        'message': 'UPLOAD_SPIKE_S3_SECRET_KEY is not set'}


# --- Object read-back -----------------------------------------------------------------

@pytest.fixture
def stubbed_client(monkeypatch):
    client = s3.make_client()
    stubber = Stubber(client)
    monkeypatch.setattr(s3, 'make_client', lambda: client)
    with stubber:
        yield stubber


def test_object_returns_key_size_etag_and_a_short_download_link(staff_client, stubbed_client):
    key = a_key()
    stubbed_client.add_response(
        'head_object',
        {'ContentLength': 1073741824, 'ETag': '"abc123-128"', 'ServerSideEncryption': 'AES256',
         'LastModified': datetime(2026, 10, 30, tzinfo=timezone.utc)},
        {'Bucket': 'mytp-dev-files', 'Key': key})
    response = post_json(staff_client, OBJECT, {'key': key})
    assert response.status_code == 200
    data = response.json()
    assert data['key'] == key
    assert data['size'] == 1073741824
    assert data['etag'] == 'abc123-128'
    assert data['serverSideEncryption'] == 'AES256'
    download = urlsplit(data['downloadUrl'])
    assert download.path == '/' + key
    assert parse_qs(download.query)['X-Amz-Expires'] == ['300']
    stubbed_client.assert_no_pending_responses()


def test_object_missing_is_404(staff_client, stubbed_client):
    stubbed_client.add_client_error('head_object', service_error_code='404', http_status_code=404)
    response = post_json(staff_client, OBJECT, {'key': a_key()})
    assert response.status_code == 404
    assert response.json()['error']['code'] == 's3_error'


def test_object_s3_failure_is_502_without_secrets(staff_client, stubbed_client):
    stubbed_client.add_client_error('head_object', service_error_code='AccessDenied',
                                    service_message=f'bad key {FAKE_SECRET_KEY}',
                                    http_status_code=403)
    response = post_json(staff_client, OBJECT, {'key': a_key()})
    assert response.status_code == 502
    assert FAKE_SECRET_KEY not in response.content.decode()


@pytest.mark.parametrize('body', [{}, {'key': 'staging/x'}, {'key': 5}, []])
def test_object_bad_key_is_400(staff_client, body):
    assert post_json(staff_client, OBJECT, body).status_code == 400


# --- URLconf hook ---------------------------------------------------------------------

@override_settings(UPLOAD_SPIKE_ENABLED=True)
def test_hook_included_when_enabled():
    assert [p.pattern.describe() for p in mytreatplan.urls.upload_spike_urlpatterns()] == \
        ["'upload-spike/'"]


@override_settings(UPLOAD_SPIKE_ENABLED=False)
def test_hook_excluded_when_disabled():
    assert mytreatplan.urls.upload_spike_urlpatterns() == []


@pytest.fixture
def reload_urlconf():
    yield lambda: (importlib.reload(mytreatplan.urls), clear_url_caches())
    # Rebuild with the real default (off), not the autouse fixture's True, so the spike
    # routes don't stay in the URLconf for the rest of the session.
    with override_settings(UPLOAD_SPIKE_ENABLED=False):
        importlib.reload(mytreatplan.urls)
    clear_url_caches()


@pytest.mark.parametrize('enabled', [True, False])
def test_real_urlconf_serves_the_spike_only_when_enabled(staff_client, settings, reload_urlconf,
                                                         enabled):
    settings.ROOT_URLCONF = 'mytreatplan.urls'
    settings.UPLOAD_SPIKE_ENABLED = enabled
    reload_urlconf()
    assert staff_client.get(PAGE).status_code == (200 if enabled else 404)


def test_spike_is_off_by_default():
    source = (django_settings.BASE_DIR / 'mytreatplan' / 'settings.py').read_text()
    assert "config('UPLOAD_SPIKE_ENABLED', default=False, cast=bool)" in source


def test_staging_env_example_lists_the_spike_settings_off_and_without_secrets():
    example = django_settings.BASE_DIR / 'deploy' / 'staging.env.example'
    values = dict(line.split('=', 1) for line in example.read_text().splitlines()
                  if line.strip() and not line.startswith('#'))
    assert values['UPLOAD_SPIKE_ENABLED'] == 'False'
    assert values['UPLOAD_SPIKE_S3_ENDPOINT'] == 'https://s3.gra.io.cloud.ovh.net'
    assert values['UPLOAD_SPIKE_S3_REGION'] == 'gra'
    for name in ('UPLOAD_SPIKE_S3_BUCKET', 'UPLOAD_SPIKE_S3_ACCESS_KEY',
                 'UPLOAD_SPIKE_S3_SECRET_KEY', 'UPLOAD_SPIKE_CORS_ORIGINS'):
        assert values[name] == ''
