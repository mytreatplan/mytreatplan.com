"""The doctor's profile photo (epic-doctor-profile entry 4, accounts.photo): the rows 1-18
of the plan's I/O matrix. The bucket is a botocore Stubber and clamd a fake on a unix
socket (the root conftest's storage fakes); nothing reaches the network."""
import io
import logging
import re
import struct
import uuid
import zlib

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import translation
from django_tasks_db.models import DBTaskResult

from accounts import client_sync, photo
from accounts.erasure import erase_user, purge_erased
from accounts.models import Profile
from conftest import STORAGE_BUCKET, expect_get, expect_promotion, expect_rejection
from integration.models import OutboxEvent, RemoteAccess
from signup.tests.helpers import PASSWORD
from storage import purposes, s3, service, tasks
from storage.models import DeletionTrace, StoredObject

from .helpers import _html

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls', 'fake_bucket')]

User = get_user_model()
MiB = 1024 * 1024
NAME = 'Espuny portrait.jpg'
EICAR = (rb'X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*')
SETTINGS_PAGE = '/en/settings/'
PHOTO_PAGE = '/en/settings/photo/'
DELETE_PAGE = '/en/settings/photo/delete/'
REJECTED_TEXT = 'The photo could not be accepted; try another file.'


@pytest.fixture
def doctor():
    return User.objects.create_user('maria.espuny@example.net', PASSWORD, title='mrs',
                                    first_name='María José', last_name='Espuny Marín',
                                    country='ES', phone='+34611222333')


@pytest.fixture
def other():
    return User.objects.create_user('other.doctor@example.net', PASSWORD, first_name='Ot')


@pytest.fixture
def staff():
    return User.objects.create_user('staff@example.net', PASSWORD, is_staff=True)


@pytest.fixture(autouse=True)
def retention(settings):
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 30


@pytest.fixture(autouse=True)
def deletions_enqueued_at_once(monkeypatch):
    """storage.service.delete enqueues its task when the transaction commits, which a
    test's transaction never does: enqueue it at once (a queued row, not run)."""
    monkeypatch.setattr(service, 'enqueue_on_commit', lambda task, *args: task.enqueue(*args))


@pytest.fixture
def presigns(monkeypatch):
    """Every presigned GET made (key, inline)."""
    made = []
    real = s3.presign_download

    def counting(key, **kwargs):
        made.append((key, kwargs.get('inline', False)))
        return real(key, **kwargs)
    monkeypatch.setattr(s3, 'presign_download', counting)
    return made


def _browser(user):
    browser = Client()
    browser.force_login(user)
    return browser


# --- Images -------------------------------------------------------------------------------

def image_bytes(kind='JPEG', size=(64, 48), noise=False):
    from PIL import Image

    if noise:
        import os
        image = Image.frombytes('RGB', size, os.urandom(size[0] * size[1] * 3))
    else:
        image = Image.new('RGB', size, (120, 40, 200))
    out = io.BytesIO()
    image.save(out, kind, **({'quality': 95} if kind == 'JPEG' else {}))
    return out.getvalue()


def png_header_only(width, height):
    """A PNG that claims `width` x `height` (its pixels never come)."""
    def chunk(kind, data):
        return (struct.pack('!I', len(data)) + kind + data
                + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff))
    ihdr = struct.pack('!IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IEND', b'')


def uploaded(user, data, content_type='image/jpeg', name=NAME):
    """A completed upload of the photo purpose (as /storage/complete/ records it)."""
    return StoredObject.objects.create(purpose=photo.PURPOSE, uploaded_by=user,
                                       size=len(data), content_type=content_type,
                                       original_name=name)


def scan(stubber, fake_clamd, obj, data, outcome, capture):
    """Run the scan task on `obj` with the bucket holding `data`: `outcome` 'clean'
    (clamd OK, image check reads it again), 'infected' (clamd FOUND) or 'invalid' (clamd
    OK, the image check refuses it)."""
    fake_clamd.reply = (b'stream: Win.Test.EICAR_HDB-1 FOUND\0' if outcome == 'infected'
                        else b'stream: OK\0')
    expect_get(stubber, obj, data)
    if outcome != 'infected':
        expect_get(stubber, obj, data)
    if outcome == 'clean':
        expect_promotion(stubber, obj)
    else:
        expect_rejection(stubber, obj)
    with capture(execute=True):
        return tasks.scan_object_task.call(str(obj.pk))


def queued_deletions():
    return [row.args_kwargs['args'] for row in
            DBTaskResult.objects.filter(task_path__endswith='delete_object_task')
            .order_by('enqueued_at')]


def run_deletions(stubber):
    """Run each queued deletion (S3 deletes stubbed); returns their args."""
    done = []
    for args in queued_deletions():
        object_id = args[0]
        for key in (f'quarantine/{object_id}', f'clean/{object_id}'):
            stubber.add_response('delete_object', {}, {'Bucket': STORAGE_BUCKET, 'Key': key})
        assert tasks.delete_object_task.call(*args) == 'deleted'
        done.append(args)
    DBTaskResult.objects.filter(task_path__endswith='delete_object_task').delete()
    return done


def attach(browser, obj):
    return browser.post(PHOTO_PAGE, {'object_id': str(obj.pk)})


def profile_of(user):
    return Profile.objects.get(user=user)


# --- Row 1: a clean JPEG becomes the photo, shown through one link per request ----------

def test_row1_a_clean_jpeg_becomes_the_photo_everywhere(doctor, stubber, fake_clamd,
                                                        presigns, caplog,
                                                        django_capture_on_commit_callbacks):
    caplog.set_level(logging.DEBUG, logger='storage')
    caplog.set_level(logging.DEBUG, logger='accounts')
    data = image_bytes(size=(900, 900), noise=True)
    assert MiB // 2 < len(data) <= photo.MAX_SIZE
    obj = uploaded(doctor, data)
    browser = _browser(doctor)

    response = attach(browser, obj)
    assert response.status_code == 302 and response['Location'] == SETTINGS_PAGE
    obj.refresh_from_db()
    assert obj.owner == doctor and profile_of(doctor).photo_pending == obj

    assert scan(stubber, fake_clamd, obj, data, 'clean',
                django_capture_on_commit_callbacks) == 'clean'
    stored = profile_of(doctor)
    assert (stored.photo, stored.photo_pending) == (obj, None)

    html = _html(browser.get('/en/home/'))
    assert presigns == [(f'clean/{obj.pk}', True)]        # one link for the whole page
    (url,) = set(re.findall(r'<img src="([^"]+)"', html))
    assert f'/clean/{obj.pk}?' in url and 'response-content-disposition=inline' in url
    assert 'data-avatar-photo' in html and 'data-profile-photo' in html
    assert 'data-profile-initials' not in html

    presigns.clear()
    html = _html(browser.get(SETTINGS_PAGE))
    assert len(presigns) == 1
    assert html.count(f'/clean/{obj.pk}?') == 3          # header, settings card, photo block
    assert 'data-photo-image' in html and 'Replace photo' in html and 'Delete photo' in html
    assert 'Photo being checked' not in html
    assert not RemoteAccess.objects.exists()
    assert NAME not in caplog.text and 'clean/' not in caplog.text
    assert 'quarantine/' not in caplog.text


@pytest.mark.parametrize('kind, content_type', [('PNG', 'image/png'), ('WEBP', 'image/webp')])
def test_png_and_webp_pass_the_image_check(doctor, kind, content_type):
    data = image_bytes(kind)
    obj = uploaded(doctor, data, content_type)
    assert photo.check_image(obj, lambda: data)


# --- Row 2: before the scan finishes ------------------------------------------------------

def test_row2_before_the_scan_the_initials_stay_and_my_details_says_checking(doctor,
                                                                             presigns):
    obj = uploaded(doctor, image_bytes())
    browser = _browser(doctor)
    attach(browser, obj)

    home = _html(browser.get('/en/home/'))
    assert re.search(r'data-avatar><span aria-hidden="true">ME</span>', home)
    assert 'data-profile-initials>ME<' in home
    page = _html(browser.get(SETTINGS_PAGE))
    assert 'data-photo-pending>Photo being checked<' in page
    assert 'data-photo-image' not in page and '/clean/' not in page and presigns == []
    # Delete is offered for the upload being checked; "Upload a photo" (no photo yet).
    assert 'data-photo-delete' in page and 'Upload a photo' in page


# --- Rows 3-5: rejected files ------------------------------------------------------------

def _assert_rejected(browser, doctor, obj, presigns):
    obj.refresh_from_db()
    assert obj.state == StoredObject.REJECTED
    trace = DeletionTrace.objects.get(object_id=obj.pk)
    assert (trace.actor_type, trace.actor_uuid, trace.patient_code) == ('system', None, '')
    with pytest.raises(service.DownloadRefused):
        service.download_url(obj, doctor, 'test_reason')
    page = _html(browser.get(SETTINGS_PAGE))
    assert REJECTED_TEXT in page and 'data-photo-rejected' in page
    assert re.search(r'role="img" aria-label="[^"]*"><span aria-hidden="true">ME</span>', page)
    assert presigns == []
    # Shown once: the rejected upload is unlinked.
    assert profile_of(doctor).photo_pending is None
    assert REJECTED_TEXT not in _html(browser.get(SETTINGS_PAGE))


def test_row3_eicar_named_jpg_is_rejected_by_the_scan(doctor, stubber, fake_clamd, presigns,
                                                       django_capture_on_commit_callbacks):
    obj = uploaded(doctor, EICAR, name='x.jpg')
    browser = _browser(doctor)
    attach(browser, obj)
    assert scan(stubber, fake_clamd, obj, EICAR, 'infected',
                django_capture_on_commit_callbacks) == 'infected'
    _assert_rejected(browser, doctor, obj, presigns)


def test_row4_text_renamed_jpg_is_rejected_by_the_image_check(
        doctor, stubber, fake_clamd, presigns, caplog, django_capture_on_commit_callbacks):
    caplog.set_level(logging.DEBUG, logger='storage')
    data = b'Just some text, not an image.\n' * 20
    obj = uploaded(doctor, data, 'image/jpeg', name='notes.jpg')
    browser = _browser(doctor)
    attach(browser, obj)
    assert scan(stubber, fake_clamd, obj, data, 'invalid',
                django_capture_on_commit_callbacks) == 'invalid_content'
    assert f'object {obj.pk} rejected (invalid_content)' in caplog.text
    _assert_rejected(browser, doctor, obj, presigns)


def test_row5_an_8000px_png_is_rejected_by_the_image_check(doctor, stubber, fake_clamd,
                                                            django_capture_on_commit_callbacks):
    from PIL import Image

    out = io.BytesIO()
    Image.new('1', (8000, 8000)).save(out, 'PNG')
    data = out.getvalue()
    assert len(data) < photo.MAX_SIZE
    obj = uploaded(doctor, data, 'image/png')
    attach(_browser(doctor), obj)
    assert scan(stubber, fake_clamd, obj, data, 'invalid',
                django_capture_on_commit_callbacks) == 'invalid_content'
    assert profile_of(doctor).photo is None


@pytest.mark.parametrize('data, content_type', [
    (png_header_only(20000, 20000), 'image/png'),     # a decompression bomb
    (png_header_only(6001, 10), 'image/png'),          # over the side limit
    (png_header_only(100, 100), 'image/png'),          # claims pixels it does not have
    (image_bytes('PNG'), 'image/jpeg'),               # a real PNG declared as JPEG
    (image_bytes('GIF'), 'image/jpeg'),               # GIF is not accepted
    (image_bytes()[:200], 'image/jpeg'),               # truncated
    (b'<svg xmlns="http://www.w3.org/2000/svg"/>', 'image/png'),
    (b'', 'image/jpeg'),
])
def test_the_image_check_refuses(doctor, data, content_type):
    obj = uploaded(doctor, data or b'x', content_type)
    assert not photo.check_image(obj, lambda: data)


def test_the_image_check_keeps_pillows_bomb_guard_on():
    from PIL import Image
    assert Image.MAX_IMAGE_PIXELS is not None and Image.MAX_IMAGE_PIXELS > 6000 * 6000


# --- Rows 6-8: the purpose and the widget --------------------------------------------------

def _sign(browser, size, content_type):
    import json
    body = {'method': 'POST', 'key': 'local-1', 'purpose': photo.PURPOSE, 'size': size}
    if content_type is not None:
        body['contentType'] = content_type
    return browser.post('/storage/sign/', data=json.dumps(body),
                        content_type='application/json')


@pytest.mark.parametrize('content_type', ['image/svg+xml', 'image/gif', 'text/html',
                                          'image/heic', None])
def test_row6_other_declared_types_are_refused_at_sign(doctor, content_type):
    response = _sign(_browser(doctor), 1000, content_type)
    assert response.status_code == 400


@pytest.mark.parametrize('content_type', photo.CONTENT_TYPES)
def test_the_three_types_are_signed(doctor, content_type):
    assert _sign(_browser(doctor), 1000, content_type).status_code == 200


def test_row7_over_5_mib_is_refused_at_sign(doctor):
    assert _sign(_browser(doctor), 5 * MiB + 1, 'image/jpeg').status_code == 413
    assert _sign(_browser(doctor), 5 * MiB, 'image/jpeg').status_code == 200


def test_rows_6_to_8_the_widget_offers_one_image_of_the_three_types(doctor):
    html = _html(_browser(doctor).get(PHOTO_PAGE))
    assert 'data-purpose="profile_photo"' in html
    assert f'data-max-size="{5 * MiB}"' in html
    assert 'data-allowed-types="image/jpeg image/png image/webp"' in html
    assert 'data-single-file="true"' in html
    assert 'data-photo-attach' in html and 'js/profile-photo.js' in html


def test_the_purpose_is_registered_as_decided():
    purpose = purposes.get(photo.PURPOSE)
    assert purpose.max_size == 5 * MiB
    assert purpose.content_types == {'image/jpeg', 'image/png', 'image/webp'}
    assert (purpose.validate, purpose.on_clean) == (photo.check_image, photo.on_clean)


def test_staff_may_not_upload_a_photo(staff):
    browser = _browser(staff)
    assert _sign(browser, 1000, 'image/jpeg').status_code == 403
    assert browser.get(PHOTO_PAGE).status_code == 404
    assert browser.get(DELETE_PAGE).status_code == 404
    page = _html(browser.get(SETTINGS_PAGE))
    assert 'data-photo-upload' not in page


# --- Rows 9-11: replacing ----------------------------------------------------------------

def _with_photo(doctor, stubber, fake_clamd, capture):
    data = image_bytes()
    obj = uploaded(doctor, data)
    attach(_browser(doctor), obj)
    scan(stubber, fake_clamd, obj, data, 'clean', capture)
    assert profile_of(doctor).photo == obj
    return obj


def test_row9_the_old_photo_stays_until_the_new_one_is_clean(
        doctor, stubber, fake_clamd, presigns, django_capture_on_commit_callbacks):
    old = _with_photo(doctor, stubber, fake_clamd, django_capture_on_commit_callbacks)
    data = image_bytes('PNG')
    new = uploaded(doctor, data, 'image/png')
    browser = _browser(doctor)
    attach(browser, new)

    page = _html(browser.get(SETTINGS_PAGE))
    assert f'/clean/{old.pk}?' in page and 'Photo being checked' in page
    assert queued_deletions() == []

    scan(stubber, fake_clamd, new, data, 'clean', django_capture_on_commit_callbacks)
    stored = profile_of(doctor)
    assert (stored.photo, stored.photo_pending) == (new, None)
    old.refresh_from_db()
    assert old.delete_requested_at is not None
    assert run_deletions(stubber) == [[str(old.pk), 'user', str(doctor.uid)]]
    trace = DeletionTrace.objects.get(object_id=old.pk)
    assert (trace.actor_type, trace.actor_uuid, trace.patient_code) == (
        'user', doctor.uid, '')
    presigns.clear()
    assert f'/clean/{new.pk}?' in _html(browser.get('/en/home/'))
    assert presigns == [(f'clean/{new.pk}', True)]


def test_row10_a_rejected_replacement_keeps_the_old_photo(
        doctor, stubber, fake_clamd, django_capture_on_commit_callbacks):
    old = _with_photo(doctor, stubber, fake_clamd, django_capture_on_commit_callbacks)
    new = uploaded(doctor, EICAR)
    browser = _browser(doctor)
    attach(browser, new)
    scan(stubber, fake_clamd, new, EICAR, 'infected', django_capture_on_commit_callbacks)

    page = _html(browser.get(SETTINGS_PAGE))
    assert REJECTED_TEXT in page and f'/clean/{old.pk}?' in page
    assert profile_of(doctor).photo == old
    old.refresh_from_db()
    assert old.delete_requested_at is None


def test_row11_a_second_upload_replaces_the_pending_one(doctor, stubber):
    first = uploaded(doctor, image_bytes())
    second = uploaded(doctor, image_bytes('PNG'), 'image/png')
    browser = _browser(doctor)
    attach(browser, first)
    attach(browser, second)

    assert profile_of(doctor).photo_pending == second
    assert run_deletions(stubber) == [[str(first.pk), 'user', str(doctor.uid)]]
    assert DeletionTrace.objects.get(object_id=first.pk).patient_code == ''
    assert not StoredObject.objects.filter(pk=first.pk).exists()


def test_an_upload_already_clean_when_attached_is_shown_at_once(doctor, presigns):
    obj = uploaded(doctor, image_bytes())
    StoredObject.objects.filter(pk=obj.pk).update(state=StoredObject.CLEAN)
    browser = _browser(doctor)
    attach(browser, obj)

    assert profile_of(doctor).photo_id == obj.pk
    page = _html(browser.get(SETTINGS_PAGE))
    assert 'Your photo has been saved.' in page and f'/clean/{obj.pk}?' in page
    assert 'Photo being checked' not in page and 'is being checked' not in page
    assert f'/clean/{obj.pk}?' in _html(browser.get('/en/home/'))


def test_row13_attaching_ones_own_rejected_upload_shows_the_reject_message(doctor,
                                                                           presigns):
    """The scan rejected a tiny file before the attach came: My details, not a 404."""
    obj = uploaded(doctor, EICAR)
    StoredObject.objects.filter(pk=obj.pk).update(state=StoredObject.REJECTED)
    DeletionTrace.objects.create(object_id=obj.pk, actor_type='system')   # the scan's
    browser = _browser(doctor)

    response = attach(browser, obj)

    assert response.status_code == 302 and response['Location'] == SETTINGS_PAGE
    page = _html(browser.get(SETTINGS_PAGE))
    assert REJECTED_TEXT in page and 'data-photo-pending' not in page
    assert not Profile.objects.filter(photo_pending__isnull=False).exists()
    obj.refresh_from_db()
    assert obj.owner_id is None and obj.delete_requested_at is not None
    assert queued_deletions() == [[str(obj.pk), 'user', str(doctor.uid)]]
    trace = DeletionTrace.objects.get(object_id=obj.pk)
    assert (trace.actor_type, trace.actor_uuid) == ('system', None)   # the scan's stays


def test_a_clean_pending_photo_is_promoted_by_my_details(doctor):
    """The scan's hook did not run (it failed): the page settles it."""
    obj = uploaded(doctor, image_bytes())
    browser = _browser(doctor)
    attach(browser, obj)
    StoredObject.objects.filter(pk=obj.pk).update(state=StoredObject.CLEAN)

    page = _html(browser.get(SETTINGS_PAGE))
    assert f'/clean/{obj.pk}?' in page and 'Photo being checked' not in page
    assert profile_of(doctor).photo_id == obj.pk


# --- Row 12: delete ---------------------------------------------------------------------

def test_row12_delete_asks_then_unlinks_and_deletes_both(
        doctor, stubber, fake_clamd, django_capture_on_commit_callbacks):
    current = _with_photo(doctor, stubber, fake_clamd, django_capture_on_commit_callbacks)
    pending = uploaded(doctor, image_bytes())
    browser = _browser(doctor)
    attach(browser, pending)

    confirm = browser.get(DELETE_PAGE)
    assert confirm.status_code == 200
    html = _html(confirm)
    assert 'Delete your profile photo?' in html and 'data-confirm-delete' in html
    assert profile_of(doctor).photo == current        # a GET changes nothing

    response = browser.post(DELETE_PAGE)
    assert response.status_code == 302 and response['Location'] == SETTINGS_PAGE
    stored = profile_of(doctor)
    assert (stored.photo, stored.photo_pending) == (None, None)
    page = _html(browser.get(SETTINGS_PAGE))
    assert 'Your photo has been deleted.' in page
    assert re.search(r'data-avatar><span aria-hidden="true">ME</span>', page)
    assert sorted(run_deletions(stubber)) == sorted([
        [str(current.pk), 'user', str(doctor.uid)], [str(pending.pk), 'user', str(doctor.uid)]])
    assert set(DeletionTrace.objects.values_list('object_id', 'patient_code')) == {
        (current.pk, ''), (pending.pk, '')}
    # Nothing left to delete: the page goes back to My details.
    assert browser.get(DELETE_PAGE)['Location'] == SETTINGS_PAGE


def test_delete_and_attach_need_csrf_and_post(doctor):
    obj = uploaded(doctor, image_bytes())
    browser = Client(enforce_csrf_checks=True)
    browser.force_login(doctor)
    assert browser.post(PHOTO_PAGE, {'object_id': str(obj.pk)}).status_code == 403
    assert browser.post(DELETE_PAGE).status_code == 403
    assert _browser(doctor).put(PHOTO_PAGE).status_code == 405
    assert not Profile.objects.exists()


def test_anonymous_goes_to_the_login(doctor):
    obj = uploaded(doctor, image_bytes())
    for url in (PHOTO_PAGE, DELETE_PAGE):
        response = Client().post(url, {'object_id': str(obj.pk)})
        assert response.status_code == 302 and '/login/' in response['Location']
    assert not Profile.objects.exists()


# --- Row 13: the attach checks -----------------------------------------------------------

@pytest.mark.parametrize('case', ['other_uploader', 'other_purpose', 'owned', 'deleting',
                                  'unknown', 'not_a_uuid', 'others_rejected'])
def test_row13_attach_refuses_what_is_not_the_doctors_upload(doctor, other, case):
    from django.utils import timezone

    obj = uploaded(doctor, image_bytes())
    object_id = str(obj.pk)
    if case == 'other_uploader':
        StoredObject.objects.filter(pk=obj.pk).update(uploaded_by=other)
    elif case == 'other_purpose':
        StoredObject.objects.filter(pk=obj.pk).update(purpose='test_files')
    elif case == 'owned':
        StoredObject.objects.filter(pk=obj.pk).update(owner=other)
    elif case == 'others_rejected':
        StoredObject.objects.filter(pk=obj.pk).update(state=StoredObject.REJECTED,
                                                      uploaded_by=other)
    elif case == 'deleting':
        StoredObject.objects.filter(pk=obj.pk).update(delete_requested_at=timezone.now())
    elif case == 'unknown':
        object_id = str(uuid.uuid4())
    elif case == 'not_a_uuid':
        object_id = '../etc'

    response = _browser(doctor).post(PHOTO_PAGE, {'object_id': object_id})

    assert response.status_code == 404
    assert not Profile.objects.filter(photo_pending__isnull=False).exists()
    obj.refresh_from_db()
    assert obj.owner_id in (None, other.pk)


def test_an_upload_attached_once_cannot_be_attached_again_by_another(doctor, other):
    obj = uploaded(doctor, image_bytes())
    attach(_browser(doctor), obj)
    assert attach(_browser(other), obj).status_code == 404


# --- Row 14: downloads -------------------------------------------------------------------

def test_row14_only_the_owner_may_download_the_photo(doctor, other, staff):
    from django.contrib.auth.models import AnonymousUser

    obj = uploaded(doctor, image_bytes())
    StoredObject.objects.filter(pk=obj.pk).update(owner=doctor, state=StoredObject.CLEAN)
    obj.refresh_from_db()
    for user in (other, staff, AnonymousUser()):
        with pytest.raises(service.DownloadRefused):
            service.download_url(obj, user, 'test_reason')
        with pytest.raises(service.DownloadRefused):
            service.owner_inline_url(obj, user)
    assert f'/clean/{obj.pk}' in service.download_url(obj, doctor, 'test_reason')


def test_another_doctors_pages_never_show_the_photo(doctor, other, presigns):
    obj = uploaded(doctor, image_bytes())
    attach(_browser(doctor), obj)
    StoredObject.objects.filter(pk=obj.pk).update(state=StoredObject.CLEAN)
    Profile.objects.filter(user=doctor).update(photo=obj, photo_pending=None)

    html = _html(_browser(other).get('/en/home/'))
    assert str(obj.pk) not in html and presigns == []


# --- Row 15: erasure ---------------------------------------------------------------------

def test_row15_erasure_deletes_the_photo_and_the_pending_upload(
        doctor, staff, stubber, fake_clamd, django_capture_on_commit_callbacks):
    current = _with_photo(doctor, stubber, fake_clamd, django_capture_on_commit_callbacks)
    pending = uploaded(doctor, image_bytes())
    attach(_browser(doctor), pending)
    stray = uploaded(doctor, image_bytes())       # uploaded, never attached

    with django_capture_on_commit_callbacks(execute=True):
        assert erase_user(doctor, by=staff)

    assert not Profile.objects.filter(user=doctor).exists()
    assert sorted(run_deletions(stubber)) == sorted(
        [str(obj.pk), 'user', str(staff.uid)] for obj in (current, pending, stray))
    assert {(t.object_id, t.actor_uuid) for t in DeletionTrace.objects.filter(
        object_id__in=[current.pk, pending.pk, stray.pk])} == {
        (current.pk, staff.uid), (pending.pk, staff.uid), (stray.pk, staff.uid)}

    purge_after = User.objects.get(pk=doctor.pk).purge_after
    assert purge_erased(now=purge_after) == 1
    assert not User.objects.filter(pk=doctor.pk).exists()


def test_row15_purge_detaches_and_deletes_objects_still_owned(doctor, staff, stubber):
    obj = uploaded(doctor, image_bytes())
    StoredObject.objects.filter(pk=obj.pk).update(owner=doctor, state=StoredObject.CLEAN)
    erase_user(doctor, by=staff)
    # An object the erasure did not reach (another purpose), still owned.
    kept = StoredObject.objects.create(purpose='test_files', owner=doctor, size=1)
    purge_after = User.objects.get(pk=doctor.pk).purge_after

    assert purge_erased(now=purge_after) == 1

    assert not User.objects.filter(pk=doctor.pk).exists()
    kept.refresh_from_db()
    assert kept.owner_id is None and kept.delete_requested_at is not None
    assert [str(kept.pk), 'system', ''] in queued_deletions()


# --- Row 16: no client event --------------------------------------------------------------

PAYLOAD_KEYS = {'title', 'first_name', 'middle_name', 'last_name', 'email', 'phone', 'country',
                'home_region', 'active', 'joined_at', 'how_found', 'city', 'time_zone',
                'profession', 'landline', 'alternative_email', 'language',
                'fiscal_entities', 'shipping_addresses'}


def test_row16_the_sync_payload_keys_are_unchanged(doctor):
    assert set(client_sync.payload_of(doctor)) == PAYLOAD_KEYS
    assert 'photo' not in Profile.SYNCED_FIELDS and 'photo_pending' not in Profile.SYNCED_FIELDS


def test_row16_photo_changes_emit_no_client_event(doctor, stubber, fake_clamd,
                                                  django_capture_on_commit_callbacks):
    before = OutboxEvent.objects.count()
    _with_photo(doctor, stubber, fake_clamd, django_capture_on_commit_callbacks)
    replacement = uploaded(doctor, image_bytes())
    attach(_browser(doctor), replacement)
    _browser(doctor).post(DELETE_PAGE)
    assert OutboxEvent.objects.count() == before
    doctor.refresh_from_db()
    assert doctor.sync_version == 0
    with transaction.atomic():
        client_sync.emit(doctor)
    assert set(OutboxEvent.objects.latest('pk').payload) == PAYLOAD_KEYS


# --- Row 17: the presign fails -------------------------------------------------------------

def test_row17_a_failing_presign_shows_the_initials(doctor, monkeypatch, caplog):
    obj = uploaded(doctor, image_bytes())
    attach(_browser(doctor), obj)
    StoredObject.objects.filter(pk=obj.pk).update(state=StoredObject.CLEAN)
    Profile.objects.filter(user=doctor).update(photo=obj, photo_pending=None)

    def broken(*args, **kwargs):
        raise RuntimeError(f'could not sign clean/{obj.pk} {NAME}')
    monkeypatch.setattr(s3, 'presign_download', broken)
    caplog.set_level(logging.DEBUG, logger='accounts')

    response = _browser(doctor).get('/en/home/')
    assert response.status_code == 200
    assert re.search(r'data-avatar><span aria-hidden="true">ME</span>', _html(response))
    assert 'profile photo link failed (RuntimeError)' in caplog.text
    assert 'clean/' not in caplog.text and NAME not in caplog.text


def test_row17_missing_settings_show_the_initials(doctor, settings):
    obj = uploaded(doctor, image_bytes())
    attach(_browser(doctor), obj)
    StoredObject.objects.filter(pk=obj.pk).update(state=StoredObject.CLEAN)
    Profile.objects.filter(user=doctor).update(photo=obj, photo_pending=None)
    settings.STORAGE_BUCKET = ''
    assert 'data-avatar><span' in _html(_browser(doctor).get('/en/home/'))


# --- Row 18: Spanish ------------------------------------------------------------------------

def test_row18_the_new_texts_in_spanish(doctor, stubber):
    obj = uploaded(doctor, image_bytes())
    browser = _browser(doctor)
    browser.post('/es/settings/photo/', {'object_id': str(obj.pk)})
    page = _html(browser.get('/es/settings/'))
    assert 'Foto en revisión' in page
    assert 'Subir una foto' in page and 'Eliminar foto' in page
    assert 'Su foto se está revisando' in page
    upload = _html(browser.get('/es/settings/photo/'))
    assert 'Foto de perfil' in upload and 'Elija una imagen' in upload
    confirm = _html(browser.get('/es/settings/photo/delete/'))
    assert '¿Desea eliminar su foto de perfil?' in confirm
    with translation.override('es'):
        from django.utils.translation import gettext as _
        for text in (REJECTED_TEXT, 'Replace photo', 'Your photo has been deleted.',
                     'Your photo has been saved.'):
            assert _(text) != text, text
        assert _(REJECTED_TEXT) == 'No se ha podido aceptar la foto; pruebe con otro archivo.'
    browser.post('/es/settings/photo/delete/')
    assert 'Su foto se ha eliminado.' in _html(browser.get('/es/settings/'))



# --- Review follow-ups (2026-11-16) ---------------------------------------------------------

def test_an_unattached_upload_is_deleted_by_the_sweep_after_6_hours(doctor, other):
    from datetime import timedelta

    from django.utils import timezone

    from storage import sweep

    now = timezone.now()
    old = uploaded(doctor, image_bytes())
    young = uploaded(doctor, image_bytes())
    linked = uploaded(other, image_bytes())
    owned = uploaded(doctor, image_bytes())
    StoredObject.objects.filter(pk__in=[old.pk, linked.pk, owned.pk]).update(
        created_at=now - timedelta(hours=6, minutes=1))
    StoredObject.objects.filter(pk=young.pk).update(created_at=now - timedelta(hours=5))
    StoredObject.objects.filter(pk=owned.pk).update(owner=doctor)
    # Linked as a profile's pending upload, without an owner (should never happen; kept).
    Profile.objects.create(user=other, photo_pending=linked)

    assert sweep.delete_unclaimed(now) == 1

    assert queued_deletions() == [[str(old.pk), 'system', '']]
    for kept in (young, linked, owned):
        kept.refresh_from_db()
        assert kept.delete_requested_at is None


def test_without_storage_the_photo_is_not_offered(doctor, settings):
    settings.STORAGE_BUCKET = ''
    browser = _browser(doctor)
    page = _html(browser.get(SETTINGS_PAGE))
    assert 'data-photo-upload' not in page and 'data-photo-delete' not in page
    assert browser.get(PHOTO_PAGE).status_code == 404
    assert browser.get(DELETE_PAGE).status_code == 404
    obj = uploaded(doctor, image_bytes())
    assert attach(browser, obj).status_code == 404


def test_the_delete_page_does_not_depend_on_the_presigned_link(doctor, monkeypatch):
    obj = uploaded(doctor, image_bytes())
    StoredObject.objects.filter(pk=obj.pk).update(owner=doctor, state=StoredObject.CLEAN)
    Profile.objects.create(user=doctor, photo=obj)

    def broken(*args, **kwargs):
        raise RuntimeError('presign failed')
    monkeypatch.setattr(s3, 'presign_download', broken)
    browser = _browser(doctor)

    assert 'data-photo-delete' in _html(browser.get(SETTINGS_PAGE))
    confirm = browser.get(DELETE_PAGE)
    assert confirm.status_code == 200 and 'data-confirm-delete' in _html(confirm)
    browser.post(DELETE_PAGE)
    assert profile_of(doctor).photo is None


def test_a_rejected_pending_row_goes_on_the_messages_first_render(doctor):
    """settle: the reject message once; the rejected row is deleted then (the scan's
    system trace stays)."""
    obj = uploaded(doctor, EICAR)
    browser = _browser(doctor)
    attach(browser, obj)
    StoredObject.objects.filter(pk=obj.pk).update(state=StoredObject.REJECTED)
    DeletionTrace.objects.create(object_id=obj.pk, actor_type='system')

    assert REJECTED_TEXT in _html(browser.get(SETTINGS_PAGE))
    obj.refresh_from_db()
    assert obj.delete_requested_at is not None
    assert queued_deletions() == [[str(obj.pk), 'user', str(doctor.uid)]]
    assert DeletionTrace.objects.get(object_id=obj.pk).actor_type == 'system'
