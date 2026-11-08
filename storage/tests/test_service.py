"""Downloads (storage.service.download_url): the purpose's hook, the access record written
first in the access log (integration.RemoteAccess, epic-admin-interface entry 5; no record,
no URL), the 5-minute presigned GET, the refusal recorded; and the
purpose registry."""
import logging
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

from eventlog.models import Attempt, Event
from integration.models import RemoteAccess
from storage import purposes, s3, service
from storage.models import StoredObject

from .conftest import BUCKET, GiB, TEST_PURPOSE

pytestmark = pytest.mark.django_db

NAME = 'García molar scan.stl'


def stored(purpose=TEST_PURPOSE, uploaded_by=None, state=StoredObject.QUARANTINE):
    return StoredObject.objects.create(purpose=purpose, uploaded_by=uploaded_by, size=GiB,
                                       content_type='model/stl', original_name=NAME,
                                       state=state)


def file_records():
    return list(RemoteAccess.objects.filter(resource=service.RESOURCE).order_by('id'))


# --- Download -------------------------------------------------------------------------

def test_download_records_the_access_then_gives_a_5_minute_url(doctor, caplog):
    caplog.set_level(logging.DEBUG)
    obj = stored(uploaded_by=doctor, state=StoredObject.CLEAN)
    url = urlsplit(service.download_url(obj, doctor, 'doctor_view'))
    query = parse_qs(url.query)
    assert url.netloc == f'{BUCKET}.s3.gra.io.cloud.ovh.net'
    assert url.path == f'/clean/{obj.pk}'
    assert query['X-Amz-Expires'] == ['300'] and s3.DOWNLOAD_URL_EXPIRES <= 300
    # Saved as <uuid>.stl: the extension, never the name.
    assert query['response-content-disposition'] == [f'attachment; filename="{obj.pk}.stl"']
    assert query['response-content-type'] == ['model/stl']
    assert 'García' not in url.geturl() and 'molar' not in url.geturl()

    (row,) = file_records()
    assert (row.actor_type, row.actor_uuid, row.outcome, row.reason, row.purpose,
            row.access_reason, row.object_id, row.fields, row.source_site) == (
        'doctor', doctor.uid, 'granted', '', TEST_PURPOSE, 'doctor_view', str(obj.pk),
        ['download_url'], 'dev')
    assert 'García' not in caplog.text and 'molar' not in caplog.text
    # No new eventlog file record (decision 2026-11-07).
    assert not Event.objects.filter(attempt__kind=Attempt.FILE).exists()


def test_access_record_is_written_before_the_url_is_made(doctor, monkeypatch):
    obj = stored(uploaded_by=doctor, state=StoredObject.CLEAN)
    seen = []

    def presign_download(key, **kwargs):
        seen.append(len(file_records()))
        return 'https://signed.example/'
    monkeypatch.setattr(s3, 'presign_download', presign_download)
    service.download_url(obj, doctor, 'doctor_view')
    assert seen == [1]


def test_no_access_record_means_no_url(doctor, monkeypatch):
    obj = stored(uploaded_by=doctor, state=StoredObject.CLEAN)

    def fail(*args, **kwargs):
        raise RuntimeError('database down')
    monkeypatch.setattr(RemoteAccess.objects, 'create', fail)
    monkeypatch.setattr(s3, 'presign_download',
                        lambda *a, **k: pytest.fail('no URL may be made'))
    with pytest.raises(service.AccessRecordFailed):
        service.download_url(obj, doctor, 'doctor_view')
    assert file_records() == []


def test_another_user_is_refused_and_the_refusal_recorded(doctor, other, monkeypatch):
    obj = stored(uploaded_by=doctor, state=StoredObject.CLEAN)
    monkeypatch.setattr(s3, 'presign_download',
                        lambda *a, **k: pytest.fail('no URL may be made'))
    with pytest.raises(service.DownloadRefused):
        service.download_url(obj, other, 'doctor_view')
    (row,) = file_records()
    assert (row.outcome, row.reason, row.actor_uuid, row.object_id, row.fields,
            row.purpose, row.access_reason) == (
        'refused', 'download_refused', other.uid, str(obj.pk), [], TEST_PURPOSE,
        'doctor_view')


def test_a_refusal_is_refused_even_when_its_record_fails(doctor, other, monkeypatch):
    obj = stored(uploaded_by=doctor, state=StoredObject.CLEAN)

    def fail(*args, **kwargs):
        raise RuntimeError('database down')
    monkeypatch.setattr(RemoteAccess.objects, 'create', fail)
    with pytest.raises(service.DownloadRefused):
        service.download_url(obj, other, 'doctor_view')


def test_real_purposes_download_only_clean_objects(doctor):
    obj = stored(uploaded_by=doctor, state=StoredObject.QUARANTINE)
    with pytest.raises(service.DownloadRefused):
        service.download_url(obj, doctor, 'doctor_view')


def test_the_sample_purpose_downloads_only_clean_objects_too(staff):
    """Decision 2026-11-07: no quarantine downloads for the sample purpose any more."""
    obj = stored(purpose='sample', uploaded_by=staff)
    with pytest.raises(service.DownloadRefused):
        service.download_url(obj, staff, 'sample_page')
    obj = stored(purpose='sample', uploaded_by=staff, state=StoredObject.CLEAN)
    url = urlsplit(service.download_url(obj, staff, 'sample_page'))
    assert url.path == f'/clean/{obj.pk}'


def test_unknown_purpose_or_inactive_user_is_refused(doctor):
    obj = stored(purpose='gone', uploaded_by=doctor, state=StoredObject.CLEAN)
    with pytest.raises(service.DownloadRefused):
        service.download_url(obj, doctor, 'doctor_view')
    obj = stored(uploaded_by=doctor, state=StoredObject.CLEAN)
    doctor.is_active = False
    with pytest.raises(service.DownloadRefused):
        service.download_url(obj, doctor, 'doctor_view')


@pytest.mark.parametrize('reason', ['', 'Has Spaces', 'x' * 51, None, 'García'])
def test_reason_must_be_a_code(doctor, reason):
    obj = stored(uploaded_by=doctor, state=StoredObject.CLEAN)
    with pytest.raises(ValueError):
        service.download_url(obj, doctor, reason)
    assert file_records() == []


def test_download_without_settings_is_refused_before_any_record(doctor, settings):
    settings.STORAGE_BUCKET = ''
    obj = stored(uploaded_by=doctor, state=StoredObject.CLEAN)
    with pytest.raises(service.NotConfigured, match='STORAGE_BUCKET'):
        service.download_url(obj, doctor, 'doctor_view')
    assert file_records() == []


def test_download_filename_keeps_only_a_plain_extension():
    obj = StoredObject(id=uuid.uuid4())
    for name, expected in [('scan.STL', f'{obj.pk}.stl'), ('noext', str(obj.pk)),
                           ('a.tar.gz', f'{obj.pk}.gz'), ('evil."; x', str(obj.pk)),
                           ('', str(obj.pk))]:
        obj.original_name = name
        assert service.download_filename(obj) == expected


def test_old_file_records_are_still_kept_as_activity_records():
    from eventlog import retention
    assert Attempt.FILE in retention.ACTIVITY_KINDS


# --- Purposes -------------------------------------------------------------------------

def test_register_refuses_a_bad_or_duplicate_purpose():
    allow = lambda *args: True  # noqa: E731
    with pytest.raises(ValueError):
        purposes.register(TEST_PURPOSE, allow, allow)
    for name in ['', 'Bad', '1x', 'x' * 41, None]:
        with pytest.raises(ValueError):
            purposes.register(name, allow, allow)
    with pytest.raises(ValueError):
        purposes.register('other_files', allow, allow, max_size=0)
    # Above clamd's scan limits (1 GiB) every file would be rejected.
    with pytest.raises(ValueError):
        purposes.register('other_files', allow, allow, max_size=1024 ** 3 + 1)
    with pytest.raises(TypeError):
        purposes.register('other_files', None, allow)
    assert purposes.get('other_files') is None


def test_default_size_limit_is_1_gib():
    assert purposes.get('sample').max_size == purposes.DEFAULT_MAX_SIZE == 1024 ** 3
    assert purposes.MAX_SCANNED_SIZE == 1024 ** 3
    # No purpose may serve unscanned files any more (decision 2026-11-07).
    assert not hasattr(purposes.get('sample'), 'download_quarantine')
