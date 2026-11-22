"""Patients and work orders (epic-ordering 9.1): the code, the create service, the stage moves
through the core's table, the one-live rule and the cross-sites convention."""
import re
import uuid

import pytest
from django.db import IntegrityError, connection, transaction

from mytp_core import stages
from patients import service
from patients.models import Patient, WorkOrder

pytestmark = pytest.mark.django_db

CODE_RE = re.compile(r'^EU-[0-9A-HJKMNP-TV-Z]{8}$')
SYSTEM = service.Actor('system')


def actor(user):
    return service.Actor('doctor', user.uid)


def work_order(patient, stage='pending_assignment'):
    return WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(), stage=stage,
                                    actor_type='system')


# --- Patients ------------------------------------------------------------------------------

def test_a_patient_with_a_name_gets_a_code(doctor):
    patient = service.create_patient(doctor, first_name='Ana', surname='Pérez',
                                     actor=actor(doctor))
    assert CODE_RE.match(patient.code)
    assert (patient.first_name, patient.surname, patient.doctor_id) == ('Ana', 'Pérez',
                                                                        doctor.pk)
    assert (patient.version, patient.status, patient.actor_type, patient.actor_uuid) == (
        1, 'active', 'doctor', doctor.uid)
    assert patient.display_name == 'Ana Pérez'


def test_the_names_and_comments_are_encrypted_at_rest(doctor):
    service.create_patient(doctor, first_name='Ana', surname='Pérez', reference='P-12',
                           comments='Allergic to latex', actor=actor(doctor))
    with connection.cursor() as cursor:
        cursor.execute('SELECT first_name, surname, reference, comments FROM patients_patient')
        stored = ' '.join(cursor.fetchone())
    assert not any(text in stored for text in ('Ana', 'rez', 'P-12', 'latex'))


def test_a_reference_alone_is_enough(doctor):
    patient = service.create_patient(doctor, reference='P-12', actor=actor(doctor))
    assert patient.display_name == 'P-12' and patient.first_name == ''


def test_nothing_given_creates_nothing(doctor):
    with pytest.raises(service.PatientInvalid):
        service.create_patient(doctor, first_name=' ', comments='only a comment',
                               actor=actor(doctor))
    assert not Patient.objects.exists()


def test_a_taken_code_is_drawn_again(doctor, monkeypatch):
    first = service.create_patient(doctor, reference='a', actor=actor(doctor))
    draws = iter([first.code, first.code, 'EU-ABCDEFGH'])
    monkeypatch.setattr(service, 'generate_code', lambda: next(draws))
    second = service.create_patient(doctor, reference='b', actor=actor(doctor))
    assert second.code == 'EU-ABCDEFGH' and Patient.objects.count() == 2


def test_the_code_uses_the_region_prefix(doctor, ae_portal):
    assert service.generate_code().startswith('AE-')


def test_codes_do_not_depend_on_the_patient(doctor):
    codes = {service.create_patient(doctor, reference='same', actor=actor(doctor)).code
             for _ in range(5)}
    assert len(codes) == 5 and all(CODE_RE.match(code) for code in codes)


def test_a_bad_actor_is_refused(doctor):
    with pytest.raises(service.InvalidActor):
        service.create_patient(doctor, reference='x', actor='me')
    with pytest.raises(service.InvalidActor):
        service.create_patient(doctor, reference='x', actor=service.Actor('doctor'))
    assert not Patient.objects.exists()


def test_a_doctor_finds_only_their_own_patients(doctor, other_doctor, patient):
    assert service.patient_of(doctor, patient.code) == patient
    assert service.patient_of(other_doctor, patient.code) is None
    assert list(service.patients_of(other_doctor)) == []


# --- Work orders: moves and the one-live rule ---------------------------------------------------

def test_a_legal_move_raises_the_version_and_stores_who(patient, doctor):
    order = work_order(patient)
    moved = service.move_work_order(order, 'assigned', 1, actor(doctor))
    assert (moved.stage, moved.version, moved.actor_type, moved.actor_uuid) == (
        'assigned', 2, 'doctor', doctor.uid)
    order.refresh_from_db()
    assert order.stage == 'assigned'


def test_an_illegal_move_is_refused_by_the_core_check(patient):
    order = work_order(patient)
    with pytest.raises(stages.InvalidStageTransition):
        service.move_work_order(order, 'in_development', 1, SYSTEM)
    order.refresh_from_db()
    assert (order.stage, order.version) == ('pending_assignment', 1)


def test_a_stale_version_is_refused(patient):
    order = work_order(patient)
    with pytest.raises(service.StaleVersion) as stale:
        service.move_work_order(order, 'assigned', 5, SYSTEM)
    assert stale.value.current == 1


def test_a_patient_has_at_most_one_live_work_order(patient):
    work_order(patient)
    with pytest.raises(IntegrityError), transaction.atomic():
        work_order(patient, 'assigned')
    assert service.live_work_order(patient) is not None


def test_cancelling_frees_the_patient_for_another_case(patient):
    first = work_order(patient)
    service.move_work_order(first, 'cancelled', 1, SYSTEM)
    assert service.live_work_order(patient) is None
    work_order(patient)  # allowed: the first is final
    assert WorkOrder.objects.filter(patient=patient).count() == 2


def test_pending_payment_orders_are_not_live(patient):
    work_order(patient, 'pending_payment')
    work_order(patient, 'pending_payment')
    assert service.live_work_order(patient) is None


def test_going_live_with_another_live_order_is_refused(patient):
    work_order(patient)
    waiting = work_order(patient, 'pending_payment')
    with pytest.raises(service.LiveWorkOrderExists):
        service.move_work_order(waiting, 'pending_assignment', 1, SYSTEM)
    waiting.refresh_from_db()
    assert waiting.stage == 'pending_payment'


# --- The cross-sites convention ---------------------------------------------------------------

def test_the_models_cross_sites_with_uuid_keys():
    from mytreatplan.uids import crossing_site_problems
    assert Patient.CROSSES_SITES and WorkOrder.CROSSES_SITES
    assert crossing_site_problems([Patient, WorkOrder]) == []
