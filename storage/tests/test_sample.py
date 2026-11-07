"""The staging sample purpose and its page (T7: 404 for anonymous, non-staff and the
setting off), its download and delete buttons, the URLconf hook, the system checks and
the settings; the upload spike is gone."""
import importlib
import uuid
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings as django_settings
from django.core.checks import run_checks
from django.test import Client, override_settings
from django.urls import clear_url_caches
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

import mytreatplan.urls
from accounts.models import User
from integration.models import RemoteAccess
from storage import checks
from storage.models import StoredObject

from .conftest import FAKE_ACCESS_KEY, FAKE_SECRET_KEY, PASSWORD

PAGE = '/en/storage/sample/'
REPO = Path(django_settings.BASE_DIR)


def download_url(pk):
    return f'/en/storage/sample/{pk}/download/'


def delete_url(pk):
    return f'/en/storage/sample/{pk}/delete/'


@pytest.fixture
def staff_client(staff):
    client = Client()
    client.force_login(staff)
    return client


@pytest.fixture
def sample_object(staff):
    return StoredObject.objects.create(purpose='sample', uploaded_by=staff, size=12,
                                       original_name='staging-test.bin')


# --- Gates (T7) -----------------------------------------------------------------------

def _all_urls(pk):
    return [('get', PAGE), ('post', download_url(pk)), ('post', delete_url(pk))]


@pytest.mark.django_db
def test_anonymous_gets_404(client, sample_object):
    for method, url in _all_urls(sample_object.pk):
        assert getattr(client, method)(url).status_code == 404


@pytest.mark.django_db
def test_non_staff_gets_404(client, sample_object):
    client.force_login(User.objects.create_user('doctor@example.net', PASSWORD))
    for method, url in _all_urls(sample_object.pk):
        assert getattr(client, method)(url).status_code == 404


@pytest.mark.django_db
def test_setting_off_gets_404_even_for_staff(staff_client, settings, sample_object):
    settings.STORAGE_SAMPLE_UPLOADS = False
    for method, url in _all_urls(sample_object.pk):
        assert getattr(staff_client, method)(url).status_code == 404


@pytest.mark.django_db
def test_other_purposes_objects_are_not_reachable_here(staff_client, staff):
    obj = StoredObject.objects.create(purpose='test_files', uploaded_by=staff, size=1)
    assert staff_client.post(download_url(obj.pk)).status_code == 404
    assert staff_client.post(delete_url(obj.pk)).status_code == 404


# --- The page -------------------------------------------------------------------------

@pytest.mark.django_db
def test_staff_sees_the_page_with_the_widget(staff_client, sample_object):
    response = staff_client.get(PAGE)
    assert response.status_code == 200
    html = response.content.decode()
    assert 'js/uppy.min.js' in html and 'css/uppy.min.css' in html
    assert 'storage/upload.js' in html
    assert 'data-sign-url="/storage/sign/"' in html
    assert 'data-complete-url="/storage/complete/"' in html
    assert 'data-purpose="sample"' in html and 'data-max-size="1073741824"' in html
    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert 'mytp-dev-files' in html
    assert str(sample_object.pk) in html and 'staging-test.bin' in html
    assert FAKE_ACCESS_KEY not in html and FAKE_SECRET_KEY not in html
    assert 'no-cache' in response['Cache-Control'] or 'max-age=0' in response['Cache-Control']


@pytest.mark.django_db
def test_page_names_a_missing_setting(staff_client, settings):
    settings.STORAGE_BUCKET = ''
    assert 'STORAGE_BUCKET is not set' in staff_client.get(PAGE).content.decode()


@pytest.mark.django_db
def test_download_button_records_then_redirects_to_a_5_minute_url(staff_client, staff,
                                                                   sample_object):
    response = staff_client.post(download_url(sample_object.pk),
                                 HTTP_USER_AGENT='Mozilla/5.0 (staging test browser)')
    assert response.status_code == 302
    assert response['Location'].startswith(
        f'https://mytp-dev-files.s3.gra.io.cloud.ovh.net/quarantine/{sample_object.pk}?')
    assert 'X-Amz-Expires=300' in response['Location']
    row = RemoteAccess.objects.get(resource='stored_object')
    assert (row.outcome, row.purpose, row.access_reason, row.actor_uuid, row.object_id) == (
        'granted', 'sample', 'sample_page', staff.uid, str(sample_object.pk))
    # With the request: the client IP and browser, as the 1.11 download record had.
    assert row.source_ip == '127.0.0.1'
    assert row.user_agent == 'Mozilla/5.0 (staging test browser)'


@pytest.mark.django_db
def test_download_button_without_a_record_gives_no_url(staff_client, sample_object,
                                                       monkeypatch):
    monkeypatch.setattr(RemoteAccess.objects, 'create',
                        lambda **kwargs: (_ for _ in ()).throw(RuntimeError('down')))
    response = staff_client.post(download_url(sample_object.pk))
    assert response.status_code == 503
    assert 'amazonaws' not in response.content.decode() and 'X-Amz' not in response.content.decode()


@pytest.mark.django_db
def test_download_needs_post(staff_client, sample_object):
    assert staff_client.get(download_url(sample_object.pk)).status_code == 405


@pytest.mark.django_db(transaction=True)
def test_delete_button_enqueues_the_deletion(staff_client, staff, sample_object):
    response = staff_client.post(delete_url(sample_object.pk))
    assert response.status_code == 302 and response['Location'] == PAGE
    queued = DBTaskResult.objects.get()
    assert queued.task_path == 'storage.tasks.delete_object_task'
    assert queued.args_kwargs['args'] == [str(sample_object.pk), 'user', str(staff.uid)]


@pytest.mark.django_db
def test_an_object_being_deleted_is_not_listed(staff_client, sample_object):
    StoredObject.objects.filter(pk=sample_object.pk).update(delete_requested_at=timezone.now())
    assert str(sample_object.pk) not in staff_client.get(PAGE).content.decode()


@pytest.mark.django_db
def test_unknown_object_is_404(staff_client):
    assert staff_client.post(download_url(uuid.uuid4())).status_code == 404


# --- URLconf hook ---------------------------------------------------------------------

@override_settings(STORAGE_SAMPLE_UPLOADS=True)
def test_hook_included_when_enabled():
    patterns = mytreatplan.urls.storage_sample_urlpatterns()
    assert [p.pattern.describe() for p in patterns] == ["'storage/sample/'"]


@override_settings(STORAGE_SAMPLE_UPLOADS=False)
def test_hook_excluded_when_disabled():
    assert mytreatplan.urls.storage_sample_urlpatterns() == []


@pytest.fixture
def reload_urlconf():
    yield lambda: (importlib.reload(mytreatplan.urls), clear_url_caches())
    with override_settings(STORAGE_SAMPLE_UPLOADS=False):
        importlib.reload(mytreatplan.urls)
    clear_url_caches()


@pytest.mark.django_db
@pytest.mark.parametrize('enabled', [True, False])
def test_real_urlconf_serves_the_page_only_when_enabled(staff_client, settings, reload_urlconf,
                                                        enabled):
    settings.ROOT_URLCONF = 'mytreatplan.urls'
    settings.STORAGE_SAMPLE_UPLOADS = enabled
    reload_urlconf()
    assert staff_client.get(PAGE).status_code == (200 if enabled else 404)
    # The upload endpoints are always routed (the purposes decide).
    assert staff_client.get('/storage/sign/').status_code == 405


# --- System checks --------------------------------------------------------------------

def _ids():
    return [error.id for error in checks.check_storage_settings()]


def test_checks_pass_with_the_settings():
    assert _ids() == []


def test_checks_pass_when_storage_is_not_used(settings):
    settings.STORAGE_SAMPLE_UPLOADS = False
    settings.STORAGE_BUCKET = ''
    settings.STORAGE_ACCESS_KEY = ''
    settings.STORAGE_ENDPOINT = ''
    assert _ids() == []


@pytest.mark.parametrize('name', ['STORAGE_ENDPOINT', 'STORAGE_REGION', 'STORAGE_ACCESS_KEY',
                                  'STORAGE_SECRET_KEY'])
def test_a_missing_setting_is_an_error_naming_it(settings, name):
    setattr(settings, name, '')
    (error,) = checks.check_storage_settings()
    assert error.id == 'storage.E001' and name in error.msg


def test_sample_uploads_without_a_bucket_is_an_error(settings):
    settings.STORAGE_BUCKET = ''
    assert _ids() == ['storage.E001']


def test_sample_uploads_on_a_public_site_is_an_error(settings):
    settings.ROBOTS_NOINDEX = False
    assert _ids() == ['storage.E002']
    settings.STORAGE_SAMPLE_UPLOADS = False
    assert _ids() == []


@pytest.mark.parametrize('days', [0, -1, True, '7'])
def test_abort_days_must_be_at_least_1(settings, days):
    settings.STORAGE_ABORT_INCOMPLETE_DAYS = days
    assert _ids() == ['storage.E003']


def test_the_checks_are_registered(settings):
    settings.ROBOTS_NOINDEX = False
    assert 'storage.E002' in [error.id for error in run_checks()]


# --- Settings and the spike's removal -------------------------------------------------

def test_sample_uploads_are_off_by_default():
    source = (REPO / 'mytreatplan' / 'settings.py').read_text()
    assert "config('STORAGE_SAMPLE_UPLOADS', default=False, cast=bool)" in source
    assert "config('STORAGE_ABORT_INCOMPLETE_DAYS', default=7, cast=int)" in source


def test_staging_env_example_lists_the_storage_settings_without_secrets():
    example = REPO / 'deploy' / 'staging.env.example'
    values = dict(line.split('=', 1) for line in example.read_text().splitlines()
                  if line.strip() and not line.startswith('#'))
    assert values['STORAGE_SAMPLE_UPLOADS'] == 'False'
    assert values['STORAGE_ENDPOINT'] == 'https://s3.gra.io.cloud.ovh.net'
    assert values['STORAGE_REGION'] == 'gra'
    assert values['STORAGE_ABORT_INCOMPLETE_DAYS'] == '7'
    for name in ('STORAGE_BUCKET', 'STORAGE_ACCESS_KEY', 'STORAGE_SECRET_KEY',
                 'STORAGE_CORS_ORIGINS'):
        assert values[name] == ''


def test_the_upload_spike_is_gone():
    assert not apps.is_installed('upload_spike')
    assert not (REPO / 'upload_spike').exists()
    for path in [REPO / 'mytreatplan' / 'settings.py', REPO / 'mytreatplan' / 'urls.py',
                 REPO / 'deploy' / 'staging.env.example', REPO / 'pytest.ini']:
        text = path.read_text()
        assert 'UPLOAD_SPIKE' not in text and 'upload_spike' not in text, path
    assert not any(name.startswith('UPLOAD_SPIKE') for name in dir(django_settings))


def test_the_region_is_read_from_an_ovh_endpoint():
    import re
    source = (REPO / 'mytreatplan' / 'settings.py').read_text()
    pattern = re.search(r"re\.fullmatch\(r'(.+?)',", source).group(1)
    assert re.fullmatch(pattern, 'https://s3.gra.io.cloud.ovh.net').group(1) == 'gra'
    assert re.fullmatch(pattern, 'https://s3.sbg.io.cloud.ovh.net/').group(1) == 'sbg'
    assert re.fullmatch(pattern, '') is None
