"""The patient page and portrait (epic-ordering 9.2): one test per row of the plan's I/O
matrix. The bucket is a botocore Stubber and clamd a fake (the root conftest's fakes)."""
import logging
import re

import pytest
from django.test import Client
from django_tasks_db.models import DBTaskResult

from accounts.tests.test_profile_photo import (
    EICAR, image_bytes, queued_deletions, run_deletions, scan)
from patients import portrait, service
from patients.models import Patient
from storage import purposes, service as storage_service
from storage.models import DeletionTrace, StoredObject

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls', 'fake_bucket')]
NAME = 'face.jpg'


@pytest.fixture(autouse=True)
def deletions_enqueued_at_once(monkeypatch):
    monkeypatch.setattr(storage_service, 'enqueue_on_commit',
                        lambda task, *args: task.enqueue(*args))


@pytest.fixture(autouse=True)
def purpose_registered():
    purposes.unregister(portrait.PURPOSE)
    portrait.register()
    yield


def browser(user):
    client = Client()
    client.force_login(user)
    return client


def page_url(patient):
    return f'/en/patients/{patient.code}/'


def html(response):
    return response.content.decode()


def uploaded(user, data, content_type='image/jpeg'):
    return StoredObject.objects.create(purpose=portrait.PURPOSE, uploaded_by=user,
                                       size=len(data), content_type=content_type,
                                       original_name=NAME)


def attach(client, patient, obj):
    return client.post(page_url(patient), {'object_id': str(obj.pk)})


def reload(patient):
    return Patient.objects.get(pk=patient.pk)


def test_the_page_shows_the_patient_and_a_placeholder(doctor, patient):
    page = html(browser(doctor).get(page_url(patient)))
    assert patient.code in page and 'Ana Pérez' in page and 'data-portrait-image' not in page
    assert 'data-portrait-widget' in page and 'data-portrait-delete' not in page


def test_another_doctors_patient_page_is_404(other_doctor, patient):
    client = browser(other_doctor)
    assert client.get(page_url(patient)).status_code == 404
    assert client.get(f'{page_url(patient)}portrait/delete/').status_code == 404
    assert client.post(page_url(patient), {'object_id': 'x'}).status_code == 404


def test_an_unknown_code_is_404(doctor):
    assert browser(doctor).get('/en/patients/EU-NOBODY00/').status_code == 404


def test_a_clean_upload_is_checked_then_shown_on_the_page_and_in_the_list(
        doctor, patient, stubber, fake_clamd, caplog, django_capture_on_commit_callbacks):
    caplog.set_level(logging.DEBUG, logger='storage')
    caplog.set_level(logging.DEBUG, logger='patients')
    data = image_bytes(size=(300, 300), noise=True)
    obj = uploaded(doctor, data)
    client = browser(doctor)

    response = attach(client, patient, obj)
    assert response.status_code == 302 and response['Location'] == page_url(patient)
    obj.refresh_from_db()
    assert obj.owner == doctor and reload(patient).portrait_pending == obj
    page = html(client.get(page_url(patient)))
    assert 'data-portrait-pending>Portrait being checked<' in page
    assert 'data-portrait-image' not in page and '/clean/' not in page
    assert '/clean/' not in html(client.get('/en/patients/'))

    assert scan(stubber, fake_clamd, obj, data, 'clean',
                django_capture_on_commit_callbacks) == 'clean'
    stored = reload(patient)
    assert (stored.portrait, stored.portrait_pending) == (obj, None)
    page = html(client.get(page_url(patient)))
    stable = f'/en/patients/{patient.code}/portrait/'
    assert f'src="{stable}"' in page and 'data-portrait-delete' in page
    assert 'being checked' not in page and '/clean/' not in page
    listing = html(client.get('/en/patients/'))
    assert f'src="{stable}"' in listing and 'data-portrait-image' in listing
    assert '/clean/' not in listing
    redirect = client.get(stable)
    assert redirect.status_code == 302 and f'/clean/{obj.pk}?' in redirect['Location']
    assert NAME not in caplog.text and 'clean/' not in caplog.text and patient.code not in (
        caplog.text)


@pytest.mark.parametrize('kind', ['infected', 'invalid'])
def test_a_refused_file_clears_pending_and_the_old_portrait_stays(
        doctor, patient, stubber, fake_clamd, django_capture_on_commit_callbacks, kind):
    client = browser(doctor)
    good = image_bytes(noise=True)
    first = uploaded(doctor, good)
    attach(client, patient, first)
    scan(stubber, fake_clamd, first, good, 'clean', django_capture_on_commit_callbacks)
    data = EICAR if kind == 'infected' else b'not an image\n' * 20
    bad = uploaded(doctor, data)
    attach(client, patient, bad)
    scan(stubber, fake_clamd, bad, data, kind, django_capture_on_commit_callbacks)
    page = html(client.get(page_url(patient)))
    assert 'data-portrait-rejected' in page and 'could not be accepted' in page
    stored = reload(patient)
    assert stored.portrait == first and stored.portrait_pending is None
    assert f'src="{page_url(patient)}portrait/"' in page
    trace = DeletionTrace.objects.get(object_id=bad.pk)
    assert trace.actor_type == 'system' and trace.patient_code == patient.code
    # shown once
    assert 'data-portrait-rejected' not in html(client.get(page_url(patient)))


def test_a_file_already_rejected_when_attached_is_deleted_and_refused(doctor, patient):
    obj = uploaded(doctor, b'x')
    obj.state = StoredObject.REJECTED
    obj.save()
    client = browser(doctor)
    response = attach(client, patient, obj)
    assert response.status_code == 302
    assert 'could not be accepted' in html(client.get(page_url(patient)))
    obj.refresh_from_db()
    assert obj.delete_requested_at is not None and reload(patient).portrait_pending is None


def test_replace_deletes_the_old_object_with_a_trace(
        doctor, patient, stubber, fake_clamd, django_capture_on_commit_callbacks):
    client = browser(doctor)
    one, two = image_bytes(noise=True), image_bytes(size=(80, 60), noise=True)
    first, second = uploaded(doctor, one), uploaded(doctor, two)
    attach(client, patient, first)
    scan(stubber, fake_clamd, first, one, 'clean', django_capture_on_commit_callbacks)
    attach(client, patient, second)
    scan(stubber, fake_clamd, second, two, 'clean', django_capture_on_commit_callbacks)
    assert reload(patient).portrait == second
    assert f'src="{page_url(patient)}portrait/"' in html(client.get(page_url(patient)))
    assert f'/clean/{second.pk}?' in client.get(f'{page_url(patient)}portrait/')['Location']
    assert [args[0] for args in queued_deletions()] == [str(first.pk)]
    assert queued_deletions()[0][4] == patient.code
    run_deletions(stubber)
    trace = DeletionTrace.objects.get(object_id=first.pk)
    assert (trace.patient_code, trace.actor_type, str(trace.actor_uuid)) == (
        patient.code, 'user', str(doctor.uid))
    assert not StoredObject.objects.filter(pk=first.pk).exists()


def test_a_second_pending_upload_deletes_the_first(doctor, patient, stubber):
    client = browser(doctor)
    first, second = uploaded(doctor, b'a'), uploaded(doctor, b'b')
    attach(client, patient, first)
    attach(client, patient, second)
    assert reload(patient).portrait_pending == second
    assert [args[0] for args in queued_deletions()] == [str(first.pk)]
    run_deletions(stubber)


def test_delete_removes_the_portrait_with_a_trace(
        doctor, patient, stubber, fake_clamd, django_capture_on_commit_callbacks):
    client = browser(doctor)
    data = image_bytes(noise=True)
    obj = uploaded(doctor, data)
    attach(client, patient, obj)
    scan(stubber, fake_clamd, obj, data, 'clean', django_capture_on_commit_callbacks)
    confirm = html(client.get(f'{page_url(patient)}portrait/delete/'))
    assert 'data-portrait-delete-form' in confirm
    response = client.post(f'{page_url(patient)}portrait/delete/')
    assert response.status_code == 302 and response['Location'] == page_url(patient)
    stored = reload(patient)
    assert stored.portrait is None and stored.portrait_pending is None
    page = html(client.get(page_url(patient)))
    assert 'data-portrait-image' not in page and 'data-portrait-delete>' not in page
    run_deletions(stubber)
    assert DeletionTrace.objects.get(object_id=obj.pk).patient_code == patient.code


def test_delete_with_nothing_to_delete_goes_back(doctor, patient):
    client = browser(doctor)
    assert client.get(f'{page_url(patient)}portrait/delete/').status_code == 302


def test_without_a_bucket_there_are_no_portrait_controls(settings, doctor, patient):
    settings.STORAGE_BUCKET = ''
    client = browser(doctor)
    page = html(client.get(page_url(patient)))
    assert 'data-portrait-widget' not in page and patient.code in page
    listing = html(client.get('/en/patients/'))
    assert patient.code in listing and 'data-portrait-thumb' not in listing
    assert client.post(page_url(patient), {'object_id': 'x'}).status_code == 404
    assert client.get(f'{page_url(patient)}portrait/delete/').status_code == 404


def test_an_object_of_someone_else_or_already_owned_is_not_attachable(
        doctor, other_doctor, patient):
    theirs = uploaded(other_doctor, b'x')
    client = browser(doctor)
    assert attach(client, patient, theirs).status_code == 404
    owned = uploaded(doctor, b'x')
    owned.owner = other_doctor
    owned.save()
    assert attach(client, patient, owned).status_code == 404
    assert client.post(page_url(patient), {'object_id': 'nope'}).status_code == 404


def test_a_portrait_is_not_shown_to_another_doctor_or_for_an_unscanned_object(
        doctor, other_doctor, patient):
    obj = uploaded(doctor, b'x')
    attach(browser(doctor), patient, obj)
    obj.refresh_from_db()
    stored = reload(patient)
    assert portrait.portrait_of(stored, other_doctor) == portrait.Portrait()
    assert not portrait.can_download(other_doctor, obj) and portrait.can_download(doctor, obj)
    assert portrait.portrait_of(stored, doctor).pending is True
    assert portrait.portrait_of(stored, doctor).url == ''


def test_the_unattached_upload_is_kept_by_the_purpose_hooks(doctor, patient):
    obj = uploaded(doctor, b'x')
    assert not portrait.in_use(obj)
    attach(browser(doctor), patient, obj)
    assert portrait.in_use(obj)


def test_the_page_is_in_spanish_too(doctor, patient):
    page = html(browser(doctor).get(f'/es/patients/{patient.code}/'))
    assert 'Retrato' in page and '<title>Paciente' in page


def _clean_portrait(doctor, patient):
    obj = uploaded(doctor, b'x')
    obj.state, obj.owner = StoredObject.CLEAN, doctor
    obj.save()
    Patient.objects.filter(pk=patient.pk).update(portrait=obj)
    return obj


def test_the_portrait_url_redirects_to_a_fresh_presigned_link(doctor, patient, monkeypatch):
    obj = _clean_portrait(doctor, patient)
    monkeypatch.setattr(storage_service.s3, 'presign_download',
                        lambda key, **kwargs: f'https://bucket.example/{obj.pk}?sig=1')
    response = browser(doctor).get(f'{page_url(patient)}portrait/')
    assert response.status_code == 302
    assert response['Location'] == f'https://bucket.example/{obj.pk}?sig=1'
    assert 'no-store' in response['Cache-Control']


def test_the_portrait_url_is_404_for_all_but_the_owner_with_a_clean_portrait(
        doctor, other_doctor, patient, django_user_model):
    url = f'{page_url(patient)}portrait/'
    assert browser(doctor).get(url).status_code == 404  # no portrait
    _clean_portrait(doctor, patient)
    assert browser(other_doctor).get(url).status_code == 404
    staff = django_user_model.objects.create_user('staff@example.com', 'violet-orbit-canyon-42',
                                                  is_staff=True)
    assert browser(staff).get(url).status_code == 404
    Patient.objects.filter(pk=patient.pk).update(status=Patient.ERASED)
    assert browser(doctor).get(url).status_code == 404  # erased


def test_the_portrait_url_needs_a_login(patient):
    response = Client().get(f'{page_url(patient)}portrait/')
    assert response.status_code == 302 and '/login' in response['Location']
