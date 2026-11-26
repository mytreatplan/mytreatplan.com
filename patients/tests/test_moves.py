"""Named stage moves (epic-case-lifecycle 10.1): `patients.service.apply_move` (the actor
checks, the version, the assignee), the history row of every stage change (moves and the
existing creation, payment, cancel and ICE-close paths), and the receivers per move."""
import uuid

import pytest

from integration.models import OutboxEvent
from mytp_core import stages
from patients import moves as receivers
from patients import service
from patients.models import StageChange, WorkOrder
from patients.tests.conftest import CLINICAL_OPS, manual_work_order

pytestmark = pytest.mark.django_db

SYSTEM = service.Actor('system')
STAFF = service.Actor('admin', uuid.UUID('c1c1c1c1-0000-4000-8000-0000000000c1'))
ASSIGNEE = uuid.UUID('a5a5a5a5-0000-4000-8000-0000000000a5')


def work_order(patient, stage='pending_assignment'):
    return WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(), stage=stage,
                                    actor_type='system')


def doctor_actor(user):
    return service.Actor('doctor', user.uid)


def history(wo):
    return list(StageChange.objects.filter(work_order=wo).values_list(
        'move', 'from_stage', 'to_stage', 'version'))


def stage_events():
    return OutboxEvent.objects.filter(type='work_order.stage_changed').count()


def test_assign_sets_the_assignee_and_writes_one_history_row_and_one_event(patient):
    wo = work_order(patient)
    moved = service.apply_move(wo, 'assign', 1, STAFF, assignee=ASSIGNEE)
    assert (moved.stage, moved.version, moved.assignee_uuid) == ('assigned', 2, ASSIGNEE)
    assert (moved.actor_type, moved.actor_uuid) == ('admin', STAFF.uuid)
    assert history(wo) == [('assign', 'pending_assignment', 'assigned', 2)]
    row = StageChange.objects.get()
    assert (row.actor_type, row.actor_uuid) == ('admin', STAFF.uuid)
    assert stage_events() == 1


def test_a_chain_of_staff_moves(patient):
    wo = work_order(patient)
    service.apply_move(wo, 'assign', 1, STAFF, assignee=ASSIGNEE)
    service.apply_move(wo, 'request_appointment', 2, STAFF)
    service.apply_move(wo, 'start_development', 3, STAFF)
    service.apply_move(wo, 'send_for_review', 4, STAFF)
    service.apply_move(wo, 'send_for_approval', 5, STAFF)
    done = service.apply_move(wo, 'request_changes', 6, STAFF)
    assert (done.stage, done.version) == ('in_development', 7)
    assert [h[0] for h in history(wo)] == [
        'assign', 'request_appointment', 'start_development', 'send_for_review',
        'send_for_approval', 'request_changes']
    assert stage_events() == 6


def test_the_doctor_moves_need_the_owner(doctor, other_doctor, patient):
    wo = work_order(patient, stages.PENDING_REGISTERS_UPLOAD)
    with pytest.raises(service.MoveRefused):
        service.apply_move(wo, 'records_complete', 1, doctor_actor(other_doctor))
    with pytest.raises(service.MoveRefused):
        service.apply_move(wo, 'records_complete', 1, STAFF)
    moved = service.apply_move(wo, 'records_complete', 1, doctor_actor(doctor))
    assert moved.stage == 'processing_prescription'
    assert (moved.actor_type, moved.actor_uuid) == ('doctor', doctor.uid)
    WorkOrder.objects.filter(pk=wo.pk).update(stage='pending_approval', version=3)
    assert service.apply_move(wo, 'approve', 3, doctor_actor(doctor)).stage == 'approved'


def test_a_staff_move_by_a_doctor_is_refused(doctor, patient):
    wo = work_order(patient)
    with pytest.raises(service.MoveRefused):
        service.apply_move(wo, 'assign', 1, doctor_actor(doctor), assignee=ASSIGNEE)
    with pytest.raises(service.MoveRefused):
        service.apply_move(wo, 'assign', 1, SYSTEM, assignee=ASSIGNEE)


@pytest.mark.parametrize('move, stage', [
    ('request_records', 'pending_assignment'), ('assign', 'assigned'),
    ('approve', 'in_development'), ('nonsense', 'assigned'), ('cancel', 'assigned')])
def test_a_move_not_allowed_from_the_stage_changes_nothing(patient, move, stage):
    wo = work_order(patient, stage)
    with pytest.raises(service.MoveRefused):
        service.apply_move(wo, move, 1, STAFF, assignee=ASSIGNEE if move == 'assign' else None)
    wo.refresh_from_db()
    assert (wo.stage, wo.version, wo.assignee_uuid) == (stage, 1, None)
    assert not StageChange.objects.exists() and not stage_events()


def test_assign_needs_an_assignee_and_the_others_refuse_one(patient):
    wo = work_order(patient)
    with pytest.raises(service.MoveRefused):
        service.apply_move(wo, 'assign', 1, STAFF)
    wo.refresh_from_db()
    assert wo.stage == 'pending_assignment' and wo.assignee_uuid is None
    WorkOrder.objects.filter(pk=wo.pk).update(stage='in_development')
    with pytest.raises(service.MoveRefused):
        service.apply_move(wo, 'send_for_review', 1, STAFF, assignee=ASSIGNEE)
    wo.refresh_from_db()
    assert wo.stage == 'in_development' and wo.assignee_uuid is None


def test_a_non_uuid_assignee_is_refused_and_changes_nothing(patient):
    wo = work_order(patient)
    with pytest.raises(service.MoveRefused):
        service.apply_move(wo, 'assign', 1, STAFF, assignee='not-a-uuid')
    wo.refresh_from_db()
    assert wo.stage == 'pending_assignment' and not StageChange.objects.exists()


def test_the_history_rows_are_append_only(patient):
    from payments.models import AppendOnly
    wo = work_order(patient)
    service.apply_move(wo, 'assign', 1, STAFF, assignee=ASSIGNEE)
    row = StageChange.objects.get()
    row.move = 'x'
    with pytest.raises(AppendOnly):
        row.save()
    with pytest.raises(AppendOnly):
        row.delete()
    with pytest.raises(AppendOnly):
        StageChange.objects.update(move='x')
    with pytest.raises(AppendOnly):
        StageChange.objects.all().delete()


def test_a_stale_version_changes_nothing(patient):
    wo = work_order(patient)
    with pytest.raises(service.StaleVersion) as caught:
        service.apply_move(wo, 'assign', 5, STAFF, assignee=ASSIGNEE)
    assert caught.value.current == 1
    assert not StageChange.objects.exists() and not stage_events()


def test_receivers_fire_per_move_inside_the_move_and_a_failure_rolls_it_back(patient):
    seen = []
    receivers.register('assign', 'seen', lambda wo: seen.append((wo.stage, wo.version)))
    receivers.register('request_records', 'boom', lambda wo: 1 / 0)
    try:
        wo = work_order(patient)
        service.apply_move(wo, 'assign', 1, STAFF, assignee=ASSIGNEE)
        assert seen == [('assigned', 2)]
        with pytest.raises(ZeroDivisionError):
            service.apply_move(wo, 'request_records', 2, STAFF)
        wo.refresh_from_db()
        assert wo.stage == 'assigned' and len(history(wo)) == 1
    finally:
        receivers.unregister('assign', 'seen')
        receivers.unregister('request_records', 'boom')


def test_a_stage_change_that_is_not_a_move_fires_nothing(patient):
    seen = []
    receivers.register('assign', 'seen', seen.append)
    try:
        wo = work_order(patient)
        service.move_work_order(wo, 'cancelled', 1, SYSTEM)
    finally:
        receivers.unregister('assign', 'seen')
    assert seen == []


def test_the_registry_checks_names_and_moves():
    with pytest.raises(ValueError):
        receivers.register('cancel', 'x', print)
    with pytest.raises(ValueError):
        receivers.register('assign', 'Bad Name', print)
    with pytest.raises(ValueError):
        receivers.register('assign', 'x', 'not callable')
    receivers.register('assign', 'once', print)
    try:
        with pytest.raises(ValueError):
            receivers.register('assign', 'once', print)
    finally:
        receivers.unregister('assign', 'once')


# --- History rows of the existing paths --------------------------------------------------

def test_creation_and_other_changes_write_a_row_with_an_empty_move(doctor, patient,
                                                                   catalogue_loaded):
    wo = manual_work_order(doctor, patient)
    assert history(wo) == [('', '', 'pending_payment', 1)]
    row = StageChange.objects.get()
    assert (row.actor_type, row.actor_uuid) == ('admin', CLINICAL_OPS.uuid)
    service.move_work_order(wo, 'cancelled', 1, SYSTEM)
    assert history(wo) == [('', '', 'pending_payment', 1), ('', 'pending_payment',
                                                            'cancelled', 2)]
    assert StageChange.objects.filter(work_order=wo).count() == 2


def test_the_history_goes_with_its_work_order(patient):
    wo = work_order(patient)
    service.apply_move(wo, 'assign', 1, STAFF, assignee=ASSIGNEE)
    WorkOrder.objects.filter(pk=wo.pk).delete()
    assert not StageChange.objects.exists()
