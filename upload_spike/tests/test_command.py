"""manage.py upload_spike_bucket: what --apply sends, what --inspect prints, and how a
refusal is reported. boto3 is stubbed (botocore Stubber); nothing reaches the network."""
from datetime import datetime, timezone
from io import StringIO

import pytest
from botocore.stub import Stubber
from django.core.management import CommandError, call_command

from upload_spike import s3
from upload_spike.management.commands import upload_spike_bucket as command

from .conftest import FAKE_ACCESS_KEY, FAKE_SECRET_KEY

BUCKET = {'Bucket': 'mytp-dev-files'}

EXPECTED_CORS = {'CORSRules': [{
    'AllowedOrigins': ['https://dev.mytpdso.com'],
    'AllowedMethods': ['GET', 'PUT', 'POST', 'DELETE'],
    'AllowedHeaders': ['Content-Type'],
    'ExposeHeaders': ['ETag'],
    'MaxAgeSeconds': 3000,
}]}
EXPECTED_LIFECYCLE = {'Rules': [{
    'ID': 'abort-incomplete-multipart-uploads',
    'Filter': {'Prefix': ''},
    'Status': 'Enabled',
    'AbortIncompleteMultipartUpload': {'DaysAfterInitiation': 1},
}]}
EXPECTED_ENCRYPTION = {'Rules': [{'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]}


@pytest.fixture
def stubber(monkeypatch):
    client = s3.make_client()
    stub = Stubber(client)
    monkeypatch.setattr(s3, 'make_client', lambda: client)
    with stub:
        yield stub
    stub.assert_no_pending_responses()


def add_inspect_responses(stub, uploads=()):
    stub.add_response('get_bucket_cors', EXPECTED_CORS, BUCKET)
    stub.add_response('get_bucket_lifecycle_configuration', EXPECTED_LIFECYCLE, BUCKET)
    stub.add_response('get_bucket_encryption',
                      {'ServerSideEncryptionConfiguration': EXPECTED_ENCRYPTION}, BUCKET)
    stub.add_response('list_multipart_uploads', {'Uploads': list(uploads)}, BUCKET)


def run(*args):
    out = StringIO()
    call_command('upload_spike_bucket', *args, stdout=out)
    return out.getvalue()


def test_apply_sets_cors_lifecycle_and_encryption_then_reads_them_back(stubber):
    stubber.add_response('put_bucket_cors', {},
                         {**BUCKET, 'CORSConfiguration': EXPECTED_CORS})
    stubber.add_response('put_bucket_lifecycle_configuration', {},
                         {**BUCKET, 'LifecycleConfiguration': EXPECTED_LIFECYCLE})
    stubber.add_response('put_bucket_encryption', {},
                         {**BUCKET, 'ServerSideEncryptionConfiguration': EXPECTED_ENCRYPTION})
    add_inspect_responses(stubber)

    out = run('--apply')

    assert 'set CORS: ok' in out
    assert 'set lifecycle: ok' in out
    assert 'set encryption: ok' in out
    assert "expose ['ETag']" in out
    assert 'abort-incomplete-after 1 day(s)' in out
    assert 'encryption: AES256' in out
    assert 'incomplete uploads: 0' in out


def test_apply_names_exactly_what_ovh_refused(stubber):
    stubber.add_response('put_bucket_cors', {}, {**BUCKET, 'CORSConfiguration': EXPECTED_CORS})
    stubber.add_client_error('put_bucket_lifecycle_configuration',
                             service_error_code='NotImplemented',
                             service_message='not supported', http_status_code=501)
    stubber.add_client_error('put_bucket_encryption', service_error_code='AccessDenied',
                             service_message=f'denied for {FAKE_ACCESS_KEY}',
                             http_status_code=403)
    add_inspect_responses(stubber)

    out = StringIO()
    with pytest.raises(CommandError) as raised:
        call_command('upload_spike_bucket', '--apply', stdout=out)

    assert str(raised.value) == 'OVHcloud refused: lifecycle, encryption'
    text = out.getvalue()
    assert 'set CORS: ok' in text
    assert 'set lifecycle: REFUSED NotImplemented (HTTP 501): not supported' in text
    assert 'set encryption: REFUSED AccessDenied (HTTP 403)' in text
    assert FAKE_ACCESS_KEY not in text and FAKE_SECRET_KEY not in text


@pytest.mark.parametrize('args', [(), ('--inspect',)])
def test_inspect_lists_settings_and_incomplete_uploads(stubber, args):
    add_inspect_responses(stubber, uploads=[{
        'Key': 'spike/2f1c7c55-8b0e-4b0e-9f43-5b8a3f2b1e10',
        'UploadId': 'up-1',
        'Initiated': datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc),
    }])

    out = run(*args)

    assert 'set ' not in out  # nothing applied
    assert "origins ['https://dev.mytpdso.com']" in out
    assert 'incomplete uploads: 1' in out
    assert 'spike/2f1c7c55-8b0e-4b0e-9f43-5b8a3f2b1e10 started 2026-10-30T09:00:00+00:00 uploadId up-1' in out


def test_inspect_reports_missing_configuration_without_failing(stubber):
    stubber.add_client_error('get_bucket_cors', service_error_code='NoSuchCORSConfiguration',
                             http_status_code=404)
    stubber.add_client_error('get_bucket_lifecycle_configuration',
                             service_error_code='NoSuchLifecycleConfiguration', http_status_code=404)
    stubber.add_client_error('get_bucket_encryption',
                             service_error_code='ServerSideEncryptionConfigurationNotFoundError',
                             http_status_code=404)
    stubber.add_response('list_multipart_uploads', {}, BUCKET)

    out = run('--inspect')

    assert 'CORS: NoSuchCORSConfiguration (HTTP 404)' in out
    assert 'lifecycle: NoSuchLifecycleConfiguration (HTTP 404)' in out
    assert 'encryption: ServerSideEncryptionConfigurationNotFoundError (HTTP 404)' in out
    assert 'incomplete uploads: 0' in out


def test_missing_setting_is_named(settings):
    settings.UPLOAD_SPIKE_S3_ACCESS_KEY = ''
    with pytest.raises(CommandError, match='UPLOAD_SPIKE_S3_ACCESS_KEY is not set'):
        run('--inspect')


def test_cors_origins_default_to_site_url(settings):
    settings.UPLOAD_SPIKE_CORS_ORIGINS = []
    settings.SITE_URL = 'https://dev.mytpdso.com'
    assert command.cors_origins() == ['https://dev.mytpdso.com']
    settings.UPLOAD_SPIKE_CORS_ORIGINS = ['https://dev.mytpdso.com/', ' ', 'http://localhost:8000']
    assert command.cors_origins() == ['https://dev.mytpdso.com', 'http://localhost:8000']


def test_command_never_uses_the_backup_bucket(settings, monkeypatch):
    """The spike's client is built from UPLOAD_SPIKE_* only."""
    settings.BACKUP_S3_ACCESS_KEY = 'BACKUPKEYMUSTNOTBEUSED'
    settings.BACKUP_S3_BUCKET = 'backup-bucket'
    client = s3.make_client()
    assert client.meta.region_name == 'gra'
    assert client.meta.endpoint_url == 'https://s3.gra.io.cloud.ovh.net'
    assert client._request_signer._credentials.access_key == FAKE_ACCESS_KEY
