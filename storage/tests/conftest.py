from pathlib import Path

import pytest
from botocore.stub import Stubber

from accounts.models import User
from storage import purposes, s3

# Fake, non-secret bucket settings: a developer's settings.env must never carry real
# bucket keys into a test. Nothing here reaches the network (presigning is local; the
# S3 calls go to a botocore Stubber).
FAKE_ACCESS_KEY = 'STORAGEACCESSKEYTEST1'
FAKE_SECRET_KEY = 'storage-secret-key-for-tests-only-0123456789'
BUCKET = 'mytp-dev-files'
PASSWORD = 'a-long-test-password-9'
REPO_DEPLOY = Path(__file__).resolve().parents[2] / 'deploy'
GiB = 1024 ** 3
MiB = 1024 ** 2

# A purpose for the tests: uploads by users whose email starts with "doctor", downloads
# by the uploader only, 100 MiB at most.
TEST_PURPOSE = 'test_files'
TEST_MAX_SIZE = 100 * MiB


@pytest.fixture(autouse=True)
def storage_settings(settings):
    settings.STORAGE_SAMPLE_UPLOADS = True
    settings.ROBOTS_NOINDEX = True
    settings.STORAGE_ENDPOINT = 'https://s3.gra.io.cloud.ovh.net'
    settings.STORAGE_REGION = 'gra'
    settings.STORAGE_BUCKET = BUCKET
    settings.STORAGE_ACCESS_KEY = FAKE_ACCESS_KEY
    settings.STORAGE_SECRET_KEY = FAKE_SECRET_KEY
    settings.STORAGE_CORS_ORIGINS = ['https://dev.mytpdso.com']
    settings.STORAGE_ABORT_INCOMPLETE_DAYS = 7
    # Nothing listens there: the scan tests start a fake clamd and point at it.
    settings.STORAGE_CLAMD_SOCKET = '/nonexistent/clamd.ctl'
    settings.ROOT_URLCONF = 'storage.tests.urls_storage'
    return settings


@pytest.fixture(autouse=True)
def test_purpose():
    purpose = purposes.register(
        TEST_PURPOSE,
        lambda user: user.email.startswith('doctor'),
        lambda user, obj: obj.uploaded_by_id == user.pk,
        max_size=TEST_MAX_SIZE)
    yield purpose
    purposes.unregister(TEST_PURPOSE)


@pytest.fixture
def staff(db):
    return User.objects.create_user('staff@example.net', PASSWORD, is_staff=True)


@pytest.fixture
def doctor(db):
    return User.objects.create_user('doctor@example.net', PASSWORD)


@pytest.fixture
def other(db):
    return User.objects.create_user('other@example.net', PASSWORD)


@pytest.fixture
def stubber(monkeypatch):
    """The S3 client every storage call gets, with a botocore Stubber on it."""
    client = s3.make_client()
    stub = Stubber(client)
    monkeypatch.setattr(s3, 'make_client', lambda **kwargs: client)
    with stub:
        yield stub
    stub.assert_no_pending_responses()
