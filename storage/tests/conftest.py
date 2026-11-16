"""The storage tests' fixtures. The fake bucket settings, the S3 Stubber, the fake clamd
and the S3 expectations are the root conftest's (shared with the accounts tests of the
profile photo)."""
from pathlib import Path

import pytest

from accounts.models import User
from conftest import STORAGE_BUCKET as BUCKET
from conftest import STORAGE_FAKE_ACCESS_KEY as FAKE_ACCESS_KEY
from conftest import STORAGE_FAKE_SECRET_KEY as FAKE_SECRET_KEY
from conftest import use_fake_bucket
from storage import purposes
PASSWORD = 'a-long-test-password-9'
REPO_DEPLOY = Path(__file__).resolve().parents[2] / 'deploy'
GiB = 1024 ** 3
MiB = 1024 ** 2

# A purpose for the tests: uploads by users whose email starts with "doctor", downloads
# by the uploader only, 100 MiB at most.
TEST_PURPOSE = 'test_files'
TEST_MAX_SIZE = 100 * MiB
# A staff-only purpose of the default size (1 GiB), as the staging sample purpose was:
# uploads and downloads by active staff while STAFF_GATE['open'] (a test closes it to
# take the permission away mid-upload).
STAFF_PURPOSE = 'staff_files'
STAFF_GATE = {'open': True}


def _staff_allowed(user):
    return bool(STAFF_GATE['open'] and user.is_staff)


@pytest.fixture(autouse=True)
def storage_settings(settings):
    settings.ROBOTS_NOINDEX = True
    # Nothing listens at its clamd socket: the scan tests start a fake clamd.
    use_fake_bucket(settings)
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


@pytest.fixture(autouse=True)
def staff_purpose():
    STAFF_GATE['open'] = True
    purpose = purposes.register(STAFF_PURPOSE, _staff_allowed,
                                lambda user, obj: _staff_allowed(user))
    yield purpose
    purposes.unregister(STAFF_PURPOSE)
    STAFF_GATE['open'] = True


@pytest.fixture
def staff(db):
    return User.objects.create_user('staff@example.net', PASSWORD, is_staff=True)


@pytest.fixture
def doctor(db):
    return User.objects.create_user('doctor@example.net', PASSWORD)


@pytest.fixture
def other(db):
    return User.objects.create_user('other@example.net', PASSWORD)
