"""Case messages by email (epic-case-lifecycle 10.2): one test per row of the plan's matrix.
The receivers write the row in the move's transaction and queue the task after commit; the
tests run the task by hand."""
import logging
import uuid

import pytest
from django.db import IntegrityError
from django.core import mail
from django_tasks_db.models import DBTaskResult

from accounts.models import Profile
from notifications import receivers
from notifications import service as messages
from notifications.models import Message
from notifications.tasks import send_message
from patients import service as patients
from patients.models import WorkOrder
from integration.tests.test_remote_work_orders import admin_site, client  # noqa: F401
from integration.tests.test_remote_work_orders import body as remote_body
from integration.tests.test_remote_work_orders import post as remote_post
from patients.tests.conftest import *  # noqa: F401,F403

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

STAFF = patients.Actor('admin', uuid.UUID('c1c1c1c1-0000-4000-8000-0000000000c1'))


def work_order(patient, stage='in_development'):
    return WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(), stage=stage,
                                    actor_type='system')


@pytest.fixture
def capture(django_capture_on_commit_callbacks):
    def run(fn):
        with django_capture_on_commit_callbacks(execute=True):
            return fn()
    return run


def queued():
    return [row.args_kwargs['args'][0] for row in notification_tasks()]


def notification_tasks():
    return DBTaskResult.objects.filter(task_path__startswith='notifications.')


def move(capture, wo, name, version):
    return capture(lambda: patients.apply_move(wo, name, version, STAFF))


def test_send_for_review_writes_a_row_and_one_email(doctor, patient, capture):
    wo = work_order(patient)
    move(capture, wo, 'send_for_review', 1)
    row = Message.objects.get()
    assert (row.kind, row.state, row.recipient, row.work_order_version) == (
        'case_pending_review', 'pending', doctor, 2)
    assert queued() == [str(row.pk)]
    assert send_message.call(str(row.pk)) == 'sent'
    row.refresh_from_db()
    assert row.state == 'sent' and row.sent_at and row.reason == ''
    message = mail.outbox[0]
    assert len(mail.outbox) == 1 and message.to == [doctor.email]
    assert patient.code in message.subject and patient.code in message.body
    assert 'Ana' not in message.body + message.subject and 'Pérez' not in message.body
    assert f'/patients/{patient.code}/' in message.body


@pytest.mark.parametrize('name, stage, kind', [
    ('request_appointment', 'assigned', 'case_pending_appointment'),
    ('send_for_review', 'in_development', 'case_pending_review'),
    ('send_for_approval', 'pending_review', 'case_pending_approval')])
def test_each_of_the_three_moves_gets_its_kind(patient, capture, name, stage, kind):
    move(capture, work_order(patient, stage), name, 1)
    row = Message.objects.get()
    assert row.kind == kind
    send_message.call(str(row.pk))
    assert len(mail.outbox) == 1 and patient.code in mail.outbox[0].subject


def test_a_spanish_doctor_gets_spanish_text(doctor, patient, capture):
    Profile.objects.create(user=doctor, language='es')
    wo = work_order(patient, 'pending_review')
    move(capture, wo, 'send_for_approval', 1)
    send_message.call(str(Message.objects.get().pk))
    message = mail.outbox[0]
    assert 'pendiente de su aprobación' in message.subject
    assert 'Fase: Pendiente de su aprobación' in message.body
    assert 'Puede ver el caso aquí' in message.body


def test_a_replayed_write_makes_no_second_row_or_email(patient, capture):
    wo = work_order(patient)
    moved = move(capture, wo, 'send_for_review', 1)
    capture(lambda: messages.case_message(moved, Message.PENDING_REVIEW))
    assert Message.objects.count() == 1 and len(queued()) == 1


def test_the_task_run_twice_sends_once(patient, capture):
    move(capture, work_order(patient), 'send_for_review', 1)
    row = Message.objects.get()
    assert send_message.call(str(row.pk)) == 'sent'
    assert send_message.call(str(row.pk)) is None
    assert len(mail.outbox) == 1


def test_a_later_return_to_the_same_stage_is_a_new_message(patient, capture):
    wo = work_order(patient)
    move(capture, wo, 'send_for_review', 1)
    move(capture, wo, 'request_changes', 2)
    move(capture, wo, 'send_for_review', 3)
    assert Message.objects.count() == 2
    for row in Message.objects.all():
        send_message.call(str(row.pk))
    assert len(mail.outbox) == 2


def test_a_rolled_back_move_leaves_no_row_and_queues_nothing(patient, capture):
    wo = work_order(patient)
    receivers.moves.register('send_for_review', 'zz_raises', lambda w: 1 / 0)
    try:
        with pytest.raises(ZeroDivisionError):
            move(capture, wo, 'send_for_review', 1)
    finally:
        receivers.moves.unregister('send_for_review', 'zz_raises')
    assert not Message.objects.exists() and queued() == []
    wo.refresh_from_db()
    assert wo.stage == 'in_development'


def test_no_login_email_fails_with_no_address(doctor, patient, capture, caplog):
    move(capture, work_order(patient), 'send_for_review', 1)
    doctor.email = ''
    doctor.save()
    row = Message.objects.get()
    with caplog.at_level(logging.INFO):
        assert send_message.call(str(row.pk)) == 'failed'
    row.refresh_from_db()
    assert (row.state, row.reason, row.sent_at) == ('failed', 'no_address', None)
    assert not mail.outbox and str(row.pk) in caplog.text


def test_an_smtp_error_fails_with_its_type_and_is_not_retried(patient, capture, monkeypatch,
                                                              caplog):
    class Boom(Exception):
        pass

    def raises(self, fail_silently=False):
        raise Boom('mailbox for ana@example.com')
    monkeypatch.setattr('django.core.mail.EmailMessage.send', raises)
    move(capture, work_order(patient), 'send_for_review', 1)
    row = Message.objects.get()
    with caplog.at_level(logging.DEBUG):
        assert send_message.call(str(row.pk)) == 'failed'
    row.refresh_from_db()
    assert (row.state, row.reason) == ('failed', 'Boom')
    assert str(row.pk) in caplog.text and 'ana@example.com' not in caplog.text
    assert send_message.call(str(row.pk)) is None


def test_other_moves_write_nothing(patient, capture):
    wo = work_order(patient, 'assigned')
    move(capture, wo, 'request_appointment', 1)
    Message.objects.all().delete()
    move(capture, wo, 'start_development', 2)
    assert not Message.objects.exists() and len(mail.outbox) == 0


def test_the_logs_carry_no_name_email_or_code(doctor, patient, capture, caplog):
    move(capture, work_order(patient), 'send_for_review', 1)
    row = Message.objects.get()
    with caplog.at_level(logging.DEBUG):
        send_message.call(str(row.pk))
    logged = caplog.text
    assert str(row.pk) in logged and 'case_pending_review' in logged
    for private in (doctor.email, patient.code, 'Ana', 'Pérez'):
        assert private not in logged
    assert notification_tasks().get().args_kwargs['args'] == [str(row.pk)]


def test_a_message_that_cannot_be_written_does_not_fail_the_move(patient, capture, monkeypatch,
                                                                 caplog):
    wo = work_order(patient)

    def raises(*args, **kwargs):
        raise IntegrityError('boom for ana@example.com')
    monkeypatch.setattr(Message.objects, 'get_or_create', raises)
    with caplog.at_level(logging.DEBUG):
        moved = move(capture, wo, 'send_for_review', 1)
    assert moved.stage == 'pending_review'
    wo.refresh_from_db()
    assert (wo.stage, wo.version) == ('pending_review', 2)
    assert not Message.objects.exists() and queued() == []
    assert str(wo.pk) in caplog.text and 'IntegrityError' in caplog.text
    assert 'ana@example.com' not in caplog.text


def test_the_email_names_the_kinds_stage_after_the_case_moved_on(patient, capture):
    wo = work_order(patient)
    move(capture, wo, 'send_for_review', 1)
    move(capture, wo, 'send_for_approval', 2)
    review = Message.objects.get(kind='case_pending_review')
    send_message.call(str(review.pk))
    assert 'Stage: Under review' in mail.outbox[0].body
    assert 'approval' not in mail.outbox[0].body


def test_a_remote_replay_with_the_same_key_makes_one_row_and_one_task(
        patient, django_capture_on_commit_callbacks, client, admin_site):
    wo = WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(),
                                  stage='in_development', actor_type='system', code='TPS-1')
    request = remote_body(wo, 'send_for_review', None)
    with django_capture_on_commit_callbacks(execute=True):
        first = remote_post(client, request)
        again = remote_post(client, request)
    assert first.status_code == 200 and again.json() == first.json()
    assert Message.objects.count() == 1 and len(queued()) == 1
