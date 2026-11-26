"""Named stage moves on the admin site (epic-case-lifecycle 10.1; mytp_core 0.36.0, resource
`work_order`): the signed read of one case and the staff moves written over the remote
write (one service call as `admin_actor`, one history row, one outbox event, versioned,
idempotent), and every refusal of the plan's matrix with its access log row. The admin site
is played by its signing key (a fixed, non-secret test seed); no test reaches the network."""
import uuid

import pytest
from django.test import Client

from integration.models import OutboxEvent, RemoteAccess, RemoteWrite
from integration.tests.snapshot_rows import ADMIN
from mytp_core import interface
from mytp_core.contracts import RemoteRecord, WorkOrderData
from patients import service
from patients.models import StageChange, WorkOrder
from patients.tests.conftest import *  # noqa: F401,F403  (the doctor, patient, catalogue)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls', 'admin_site')]

URL = '/api/v1/remote/work-orders'
ME = '6f1c2b8a-3d4e-4f5a-9b6c-7d8e9f0a1b2c'
OTHER = '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d'
ASSIGNEE = 'a5a5a5a5-0000-4000-8000-0000000000a5'
FIELDS = ['assignee', 'code', 'move', 'product_category', 'stage']


@pytest.fixture
def admin_site(interface_settings):
    interface_settings.INTERFACE_ADMIN_SITE = 'admindev'
    return interface_settings


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def case(patient):
    return WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(),
                                    stage='pending_assignment', actor_type='system',
                                    code='TPS-1')


def scope(role='clinical_manager', sub=ME, regions=('ae', 'eu')):
    return {'sub': sub, 'role': role, 'regions': list(regions)}


def _headers(method, url, raw, extra):
    token = interface.sign_request(method, url, raw, audience='dev', issuer='admindev',
                                   signing_key=ADMIN, extra=extra)
    return {'HTTP_AUTHORIZATION': interface.authorization_header(token),
            'REMOTE_ADDR': '203.0.113.9', 'HTTP_USER_AGENT': 'mytp-admindev-test'}


def read(client, case, **kwargs):
    url = f'{URL}?id={case.pk}'
    return client.get(url, **_headers('GET', url, b'', scope(**kwargs)))


def body(case, move='assign', assignee=ASSIGNEE, expected=None, key=None):
    changes = {'move': move}
    if assignee is not None:
        changes['assignee'] = assignee
    return {'idempotency_key': key or str(uuid.uuid4()),
            'expected_version': case.version if expected is None else expected,
            'object_id': str(case.pk), 'changes': changes}


def post(client, request, **kwargs):
    raw = interface.json_body(request)
    return client.generic('POST', URL, raw, content_type='application/json',
                          **_headers('POST', URL, raw, scope(**kwargs)))


def rows(**filters):
    return list(RemoteAccess.objects.filter(**filters).order_by('at', 'id'))


def stage_events():
    return OutboxEvent.objects.filter(type='work_order.stage_changed').count()


# --- Read -------------------------------------------------------------------------------

def test_the_case_is_read_with_the_roles_fields_and_its_version(client, case):
    response = read(client, case)
    assert response.status_code == 200 and 'no-store' in response['Cache-Control']
    answer = response.json()
    RemoteRecord.model_validate(answer)
    assert answer['object_id'] == str(case.pk) and answer['version'] == 1
    assert answer['fields'] == FIELDS
    WorkOrderData.model_validate(answer['data'])
    assert answer['data'] == {'code': 'TPS-1', 'stage': 'pending_assignment',
                              'product_category': '', 'assignee': None, 'move': None}
    (row,) = rows()
    assert (row.operation, row.outcome, row.object_id) == ('read', 'granted', str(case.pk))


def test_no_patient_data_in_the_answer(client, case, patient):
    text = read(client, case).content.decode()
    assert patient.code not in text and 'Ana' not in text and 'Pérez' not in text


def test_an_unknown_case_is_not_found(client, case):
    url = f'{URL}?id={uuid.uuid4()}'
    response = client.get(url, **_headers('GET', url, b'', scope()))
    assert response.status_code == 404


@pytest.mark.parametrize('role', ['financial_manager', 'commercial_manager'])
def test_roles_without_access_cannot_read(client, case, role):
    assert read(client, case, role=role).status_code == 403
    assert {(r.outcome, r.reason) for r in rows()} == {('refused', 'role')}


# --- Assign and a move ------------------------------------------------------------------

def test_a_clinical_manager_assigns_then_requests_the_appointment(client, case):
    response = post(client, body(case))
    assert response.status_code == 200 and 'no-store' in response['Cache-Control']
    answer = response.json()
    RemoteRecord.model_validate(answer)
    assert answer['version'] == 2
    assert (answer['data']['stage'], answer['data']['assignee'], answer['data']['move']) == (
        'assigned', ASSIGNEE, 'assign')
    case.refresh_from_db()
    assert (case.stage, str(case.assignee_uuid), case.version) == ('assigned', ASSIGNEE, 2)
    assert (case.actor_type, str(case.actor_uuid)) == ('admin', ME)
    (row,) = rows()
    assert (row.operation, row.outcome, row.access_reason) == (
        'write', 'granted', 'write:assignee,move')

    second = post(client, body(case, 'request_appointment', None, expected=2))
    assert second.status_code == 200 and second.json()['data']['stage'] == 'pending_appointment'
    history = list(StageChange.objects.filter(work_order=case).values_list(
        'move', 'from_stage', 'to_stage', 'version', 'actor_type', 'actor_uuid'))
    assert history == [
        ('assign', 'pending_assignment', 'assigned', 2, 'admin', uuid.UUID(ME)),
        ('request_appointment', 'assigned', 'pending_appointment', 3, 'admin', uuid.UUID(ME))]
    assert stage_events() == 2


def test_the_same_key_and_body_replays_the_stored_answer(client, case):
    request = body(case)
    first = post(client, request)
    again = post(client, request)
    assert again.status_code == 200 and again.json() == first.json()
    assert StageChange.objects.count() == 1 and stage_events() == 1
    assert RemoteWrite.objects.count() == 1


def test_the_same_key_with_another_body_is_refused(client, case):
    key = str(uuid.uuid4())
    assert post(client, body(case, key=key)).status_code == 200
    other = body(case, assignee=OTHER, key=key)
    assert post(client, other).status_code == 422
    assert StageChange.objects.count() == 1


def test_a_stale_version_is_refused_with_the_current_one(client, case):
    response = post(client, body(case, expected=7))
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'stale_version'
    assert response.json()['current_version'] == 1
    case.refresh_from_db()
    assert case.stage == 'pending_assignment'
    assert not StageChange.objects.exists() and not stage_events()


def test_a_move_not_allowed_from_the_stage_is_stale(client, case):
    response = post(client, body(case, 'send_for_review', None))
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'stale_version'
    assert response.json()['current_version'] == 1
    case.refresh_from_db()
    assert case.stage == 'pending_assignment' and not StageChange.objects.exists()
    assert not RemoteWrite.objects.exists() and not stage_events()
    # The granted row is written before the service runs; the refusal follows it.
    assert [(r.operation, r.outcome, r.reason) for r in rows()] == [
        ('write', 'granted', ''), ('write', 'refused', 'stale_version')]


def test_the_read_keeps_the_last_named_move_after_a_cancel(client, case):
    assert post(client, body(case)).status_code == 200
    case.refresh_from_db()
    service.move_work_order(case, 'cancelled', case.version, service.SYSTEM_ACTOR)
    data = read(client, case).json()['data']
    assert (data['stage'], data['move']) == ('cancelled', 'assign')


# --- Refusals ---------------------------------------------------------------------------

@pytest.mark.parametrize('move', ['approve', 'records_complete', 'cancel', 'set_stage'])
def test_a_doctor_move_or_unknown_move_is_an_invalid_body(client, case, move):
    response = post(client, body(case, move, None))
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'invalid_body'
    case.refresh_from_db()
    assert case.version == 1
    assert {(r.outcome, r.reason) for r in rows()} == {('refused', 'invalid_body')}


@pytest.mark.parametrize('changes, status, reason', [
    ({'move': 'assign'}, 400, 'invalid_body'),
    ({'move': 'assign', 'assignee': 'nope'}, 400, 'invalid_body'),
    ({'move': 'request_records', 'assignee': ASSIGNEE}, 400, 'invalid_body'),
    ({'stage': 'assigned'}, 403, 'field')])
def test_bad_changes_are_refused(client, case, changes, status, reason):
    request = body(case)
    request['changes'] = changes
    response = post(client, request)
    assert response.status_code == status
    assert response.json()['error']['code'] == ('forbidden' if status == 403 else reason)
    case.refresh_from_db()
    assert case.version == 1 and not stage_events()
    assert {(r.outcome, r.reason) for r in rows()} == {('refused', reason)}


def test_a_clinical_user_cannot_assign(client, case):
    WorkOrder.objects.filter(pk=case.pk).update(assignee_uuid=ME)
    response = post(client, body(case), role='clinical_user')
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'forbidden'
    assert {(r.outcome, r.reason) for r in rows()} == {('refused', 'field')}
    case.refresh_from_db()
    assert case.stage == 'pending_assignment' and not StageChange.objects.exists()


def test_a_clinical_user_moves_a_case_assigned_to_them(client, case):
    WorkOrder.objects.filter(pk=case.pk).update(stage='assigned', assignee_uuid=ME)
    case.refresh_from_db()
    response = post(client, body(case, 'request_records', None), role='clinical_user')
    assert response.status_code == 200 and response.json()['data']['stage'] == (
        'pending_registers_upload')
    assert read(client, case, role='clinical_user').status_code == 200


def test_a_clinical_user_cannot_move_anothers_case(client, case):
    WorkOrder.objects.filter(pk=case.pk).update(stage='assigned', assignee_uuid=OTHER)
    case.refresh_from_db()
    response = post(client, body(case, 'request_records', None), role='clinical_user')
    assert response.status_code == 403
    assert {(r.outcome, r.reason) for r in rows()} == {('refused', 'assigned')}
    assert read(client, case, role='clinical_user').status_code == 403
    case.refresh_from_db()
    assert case.stage == 'assigned' and case.version == 1


@pytest.mark.parametrize('role', ['financial_manager', 'commercial_manager',
                                  'commercial_regional_manager'])
def test_other_roles_cannot_move(client, case, role):
    assert post(client, body(case), role=role).status_code == 403
    case.refresh_from_db()
    assert case.version == 1


def test_a_doctor_move_by_the_service_is_refused_for_an_admin_actor(doctor, case):
    admin = service.Actor('admin', uuid.UUID(ME))
    WorkOrder.objects.filter(pk=case.pk).update(stage='pending_registers_upload')
    with pytest.raises(service.MoveRefused):
        service.apply_move(case, 'records_complete', 1, admin)


def test_an_unsigned_request_is_refused(client, case):
    assert client.get(f'{URL}?id={case.pk}').status_code == 401
