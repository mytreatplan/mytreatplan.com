"""The patient list's stage and the patient file (epic-case-lifecycle 10.4): services history,
comments, active/inactive control, slots."""
import logging
import re
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from patients import service, slots
from patients.models import Patient, StageChange, WorkOrder
from patients.tests.conftest import BRANDED

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls', 'fake_bucket')]


@pytest.fixture
def slot_template(settings):
    templates = [dict(settings.TEMPLATES[0])]
    templates[0]['OPTIONS'] = dict(templates[0]['OPTIONS'], loaders=[
        ('django.template.loaders.locmem.Loader',
         {'slot_demo.html': '<p>{{ slot.word }}</p>'}),
        'django.template.loaders.filesystem.Loader',
        'django.template.loaders.app_directories.Loader'])
    templates[0].pop('APP_DIRS')
    settings.TEMPLATES = templates


LIST = '/en/patients/'


def browser(user):
    client = Client()
    client.force_login(user)
    return client


def file_url(patient, lang='en'):
    return f'/{lang}/patients/{patient.code}/'


def case(patient, stage='pending_assignment', product=None, created=None):
    work_order = WorkOrder.objects.create(
        patient=patient, order_line_id=uuid.uuid4(), stage=stage, actor_type='system',
        code='TPS-1', product_id=product)
    if created is not None:
        WorkOrder.objects.filter(pk=work_order.pk).update(created_at=created)
    return work_order


def new_patient(doctor, ref):
    return service.create_patient(doctor, reference=ref,
                                  actor=service.Actor('doctor', doctor.uid))


def doctor_actor(doctor):
    return service.Actor('doctor', doctor.uid)


# --- The list -------------------------------------------------------------------------

def test_the_list_shows_the_stage_or_a_dash(doctor):
    live, finished, none = (new_patient(doctor, r) for r in ('A', 'B', 'C'))
    case(live, 'in_development')
    case(finished, 'closed')
    page = browser(doctor).get(LIST).content.decode()
    row = lambda p: re.search(rf'data-patient="{p.code}".*?</tr>', page, re.S).group(0)
    assert 'In development' in row(live) and 'Closed' in row(finished)
    assert 'data-stage=""' in row(none) and '&mdash;' in row(none)


def test_the_list_prefers_the_live_case_to_a_later_finished_one(doctor, patient):
    now = timezone.now()
    case(patient, 'in_development', created=now - timedelta(days=2))
    case(patient, 'cancelled', created=now - timedelta(days=1))
    page = browser(doctor).get(LIST).content.decode()
    assert 'In development' in page and 'Cancelled' not in page


def test_an_inactive_patient_row_is_highlighted_and_keeps_its_pill(doctor, patient):
    other = new_patient(doctor, 'Z')
    Patient.objects.filter(pk=patient.pk).update(status=Patient.INACTIVE)
    page = browser(doctor).get(LIST).content.decode()
    inactive = re.search(rf'<tr [^>]*data-patient="{patient.code}"', page).group(0)
    active = re.search(rf'<tr [^>]*data-patient="{other.code}"', page).group(0)
    assert 'bg-ink/5' in inactive and 'bg-ink/5' not in active
    assert 'data-status="inactive"' in page


def test_the_list_query_count_does_not_grow_with_the_rows(doctor):
    client = browser(doctor)
    client.get(LIST)  # warm the caches

    def count():
        with CaptureQueriesContext(connection) as queries:
            assert client.get(LIST).status_code == 200
        return len([q for q in queries if 'SAVEPOINT' not in q['sql']])
    for index in range(2):
        case(new_patient(doctor, f'P{index}'), 'in_development')
    few = count()
    for index in range(2, 8):
        case(new_patient(doctor, f'P{index}'), 'closed')
    assert count() == few


def test_the_cart_wizards_chooser_has_no_stage_column(patient):
    from django.template.loader import render_to_string
    html = render_to_string('patients/_patient_chooser.html', {
        'mode': 'choose', 'rows': [{'patient': patient, 'search': 'x'}]})
    assert 'Stage' not in html and 'data-stage' not in html


# --- The file: services history ----------------------------------------------------------

def test_the_file_lists_both_services_newest_first_with_dated_changes(doctor, patient):
    now = timezone.now()
    old = case(patient, 'pending_assignment', created=now - timedelta(days=5))
    service.move_work_order(old, 'cancelled', old.version, doctor_actor(doctor))
    new = case(patient, 'pending_assignment', product=BRANDED['medium'])
    StageChange.objects.create(work_order=new, to_stage='pending_assignment', version=1,
                               actor_type='system')
    page = browser(doctor).get(file_url(patient)).content.decode()
    items = re.findall(r'<li [^>]*data-service>.*?</li>\s*(?=<li [^>]*data-service>|</ul>)',
                       page, re.S)
    assert len(items) == 2
    assert 'TPS branded medium' in items[0] and 'data-service-stage="pending_assignment"' in items[0]
    assert 'data-service-stage="cancelled"' in items[1]
    assert 'data-stage-change="cancelled"' in items[1] and 'Cancelled' in items[1]
    assert 'Earlier stage changes were not recorded' not in items[0]
    assert 'data-service-created' not in items[0]
    assert 'data-service-created' in items[1] and 'Cancelled' in items[1]
    assert 'Earlier stage changes were not recorded' in items[1]


def test_an_older_service_shows_creation_and_the_note(doctor, patient):
    case(patient, 'in_development')
    page = browser(doctor).get(file_url(patient)).content.decode()
    assert 'Earlier stage changes were not recorded' in page
    assert 'data-stage-changes' not in page and 'In development' in page


def test_the_history_is_translated(doctor, patient):
    work_order = case(patient, 'pending_approval')
    StageChange.objects.create(work_order=work_order, to_stage='pending_approval', version=1,
                               actor_type='system')
    page = browser(doctor).get(file_url(patient, 'es')).content.decode()
    assert 'Servicios' in page and 'Pendiente de su aprobación' in page
    assert 'Guardar comentarios' in page


# --- Comments ---------------------------------------------------------------------------

def post_comments(client, patient, text, version=None):
    return client.post(f'/en/patients/{patient.code}/comments/', {
        'comments': text, 'version': patient.version if version is None else version})


def test_the_doctor_saves_comments(doctor, patient):
    client = browser(doctor)
    response = post_comments(client, patient, 'Needs a retainer')
    assert response.status_code == 302 and response['Location'] == file_url(patient)
    patient.refresh_from_db()
    assert patient.comments == 'Needs a retainer' and patient.version == 2
    assert patient.actor_type == 'doctor' and patient.actor_uuid == doctor.uid
    page = client.get(file_url(patient)).content.decode()
    assert 'The comments have been saved.' in page and 'Needs a retainer' in page


def test_a_stale_comments_edit_saves_nothing(doctor, patient):
    client = browser(doctor)
    stale = patient.version
    post_comments(client, patient, 'first')
    response = post_comments(client, patient, 'second', version=stale)
    assert response.status_code == 302
    patient.refresh_from_db()
    assert patient.comments == 'first' and patient.version == 2
    assert 'changed meanwhile' in client.get(file_url(patient)).content.decode()


def test_too_long_comments_are_refused(doctor, patient):
    response = post_comments(browser(doctor), patient, 'x' * 2001)
    assert response.status_code == 200
    assert 'data-comments-error' in response.content.decode()
    patient.refresh_from_db()
    assert patient.comments == '' and patient.version == 1
    assert post_comments(browser(doctor), patient, 'x' * 2000).status_code == 302


def test_comments_of_2000_characters_with_crlf_line_breaks_save(doctor, patient):
    text = ('x' * 99 + '\r\n') * 19 + 'x' * 100  # 2000 characters once CRLF counts as 1; 2019 as sent
    assert len(text.replace('\r\n', '\n')) == 2000 and len(text) > 2000
    assert post_comments(browser(doctor), patient, text).status_code == 302
    patient.refresh_from_db()
    assert patient.comments == text.replace('\r\n', '\n') and patient.version == 2


def test_a_bad_status_version_is_not_called_stale(doctor, patient):
    client = browser(doctor)
    client.post(f'/en/patients/{patient.code}/status/', {'version': 'x'})
    page = client.get(file_url(patient)).content.decode()
    assert 'The change could not be saved' in page and 'changed meanwhile' not in page


def test_comments_are_never_logged(doctor, patient, caplog):
    with caplog.at_level(logging.DEBUG):
        post_comments(browser(doctor), patient, 'SECRETNOTE-4711')
        browser(doctor).get(file_url(patient))
    assert 'SECRETNOTE-4711' not in caplog.text


# --- Active / inactive -------------------------------------------------------------------

def test_the_doctor_sets_a_patient_inactive_and_active_again(doctor, patient):
    client = browser(doctor)
    case(patient, 'in_development')
    url = f'/en/patients/{patient.code}/status/'
    assert client.post(url, {'version': 1}).status_code == 302
    patient.refresh_from_db()
    assert patient.status == Patient.INACTIVE and patient.version == 2
    assert patient.actor_uuid == doctor.uid
    assert WorkOrder.objects.get().stage == 'in_development'
    assert service.patient_of(doctor, patient.code) is None
    page = client.get(LIST).content.decode()
    assert 'bg-ink/5' in re.search(rf'<tr [^>]*data-patient="{patient.code}"', page).group(0)
    assert 'data-set-active' in client.get(file_url(patient)).content.decode()
    client.post(url, {'version': 2, 'active': 'on'})
    patient.refresh_from_db()
    assert patient.status == Patient.ACTIVE and patient.version == 3


def test_a_stale_status_change_does_nothing(doctor, patient):
    response = browser(doctor).post(f'/en/patients/{patient.code}/status/', {'version': 7})
    assert response.status_code == 302
    patient.refresh_from_db()
    assert patient.status == Patient.ACTIVE and patient.version == 1


def test_the_cart_wizard_no_longer_offers_an_inactive_patient(doctor, patient):
    browser(doctor).post(f'/en/patients/{patient.code}/status/', {'version': 1})
    page = browser(doctor).get('/en/start-a-case/patient/').content.decode()
    assert patient.code not in page


# --- Ownership ----------------------------------------------------------------------------

def test_another_doctors_patient_is_a_404_and_unchanged(doctor, other_doctor, patient):
    client = browser(other_doctor)
    assert client.get(file_url(patient)).status_code == 404
    assert post_comments(client, patient, 'hijack').status_code == 404
    assert client.post(f'/en/patients/{patient.code}/status/', {'version': 1}).status_code == 404
    patient.refresh_from_db()
    assert patient.comments == '' and patient.version == 1 and patient.status == Patient.ACTIVE


def test_an_erased_patient_is_a_404(doctor, patient):
    Patient.objects.filter(pk=patient.pk).update(status=Patient.ERASED)
    client = browser(doctor)
    assert client.get(file_url(patient)).status_code == 404
    assert post_comments(client, patient, 'x').status_code == 404
    assert client.post(f'/en/patients/{patient.code}/status/', {'version': 1}).status_code == 404


def test_the_writes_are_post_only(doctor, patient):
    client = browser(doctor)
    assert client.get(f'/en/patients/{patient.code}/comments/').status_code == 405
    assert client.get(f'/en/patients/{patient.code}/status/').status_code == 405


# --- Service ---------------------------------------------------------------------------------

def test_the_service_refuses_a_bad_actor_and_stale_version(doctor, patient):
    with pytest.raises(service.StaleVersion):
        service.update_comments(patient, 'x', 9, doctor_actor(doctor))
    with pytest.raises(service.InvalidActor):
        service.set_active(patient, False, 1, 'nobody')
    with pytest.raises(service.PatientInvalid):
        service.update_comments(patient, 'x' * 2001, 1, doctor_actor(doctor))


# --- Slots ------------------------------------------------------------------------------------

@pytest.fixture
def without_the_records_entry():
    """The records app (10.5) fills `case_actions` in production; these tests check the
    slots themselves, empty, so take its entry out for the test."""
    entry = slots._registry[slots.CASE_ACTIONS].get('case_records')
    slots.unregister(slots.CASE_ACTIONS, 'case_records')
    yield
    if entry is not None:
        slots._registry[slots.CASE_ACTIONS]['case_records'] = entry


def test_the_slots_render_nothing_until_an_app_registers(doctor, patient,
                                                         without_the_records_entry):
    page = browser(doctor).get(file_url(patient)).content.decode()
    assert 'data-slot=' not in page


def test_a_registered_slot_is_rendered_and_can_be_removed(doctor, patient, slot_template,
                                                          without_the_records_entry):
    slots.register(slots.CASE_ACTIONS, 'demo', 'slot_demo.html',
                   lambda request, p: {'word': 'hello-' + p.code})
    slots.register(slots.APPOINTMENTS, 'demo2', 'slot_demo.html',
                   lambda request, p: {'word': 'appt'})
    try:
        page = browser(doctor).get(file_url(patient)).content.decode()
        assert 'data-slot="case_actions"' in page and f'hello-{patient.code}' in page
        assert 'data-slot="appointments"' in page and 'appt' in page
    finally:
        slots.unregister(slots.CASE_ACTIONS, 'demo')
        slots.unregister(slots.APPOINTMENTS, 'demo2')
    slots.unregister(slots.CASE_ACTIONS, 'demo')  # a no-op
    assert 'data-slot=' not in browser(doctor).get(file_url(patient)).content.decode()


def test_an_unknown_slot_is_refused():
    with pytest.raises(ValueError):
        slots.register('nope', 'x', 't.html')
