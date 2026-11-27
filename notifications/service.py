"""Case messages (epic-case-lifecycle 10.2). `case_message` runs in the move's transaction
(get-or-create of the row, one per work order, version and channel, then the send queued on
commit); `send` runs in the task, once per row (the state is the once-only mark; a crash
between sending and marking can still send twice, as the order emails). When the email ends
`failed`, `send` writes the SMS row of the same work order and version (10.3) and queues it;
the SMS row is sent by the same task, one try, no retry."""
import logging
import uuid

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation

from invoices import pdf
from mytp_core import stages
from mytp_core.tasks import enqueue_on_commit
from patients.models import WorkOrder
from patients.stages import stage_label

from . import sms
from .models import Message

logger = logging.getLogger(__name__)

NO_ADDRESS = 'no_address'
NO_MOBILE = 'no_mobile'
# The stage each kind announces: the label never follows the work order's live stage.
STAGES = {Message.PENDING_APPOINTMENT: stages.PENDING_APPOINTMENT,
          Message.PENDING_REVIEW: stages.PENDING_REVIEW,
          Message.PENDING_APPROVAL: stages.PENDING_APPROVAL}
TEMPLATES = {kind: f'notifications/email/{kind}' for kind, _label in Message.KINDS}
SMS_TEMPLATES = {kind: f'notifications/sms/{kind}.txt' for kind, _label in Message.KINDS}


def case_message(work_order, kind):
    """Write the email row for `work_order` (the locked, just-moved row) at its version, and
    queue its send after commit. Replaying the same version finds the row and does nothing.
    Returns the row."""
    from .tasks import send_message
    try:
        # Its own savepoint: a message that cannot be written never fails the move.
        with transaction.atomic():
            message, created = Message.objects.get_or_create(
                work_order_id=work_order.pk, work_order_version=work_order.version,
                channel=Message.EMAIL,
                defaults={'kind': kind, 'recipient_id': work_order.patient.doctor_id})
            if created:
                enqueue_on_commit(send_message, str(message.pk))
    except Exception as failure:  # noqa: BLE001 - logged by UUID and type only
        logger.error('message not written: kind %s work order %s (%s)', kind, work_order.pk,
                     type(failure).__name__)
        return None
    return message


def render(message, work_order):
    """(subject, body) in the recipient's language: the patient by code only."""
    patient = work_order.patient
    with translation.override(pdf.language_of(message.recipient)):
        url = f"{settings.SITE_URL}{reverse('patient', args=[patient.code])}"
        context = {'code': patient.code, 'stage': stage_label(STAGES[message.kind]), 'url': url}
        template = TEMPLATES[message.kind]
        subject = ''.join(render_to_string(f'{template}_subject.txt', context).splitlines())
        body = render_to_string(f'{template}.txt', context)
    return subject.strip(), body


def _outcome(message, state, reason=''):
    message.state, message.reason = state, reason
    message.sent_at = timezone.now() if state == Message.SENT else None
    message.save(update_fields=['state', 'reason', 'sent_at'])
    logger.info('message %s kind %s outcome %s%s', message.pk, message.kind, state,
                f' ({reason})' if reason else '')


def render_sms(message, work_order):
    """The SMS text in the recipient's language: "MyTreatPlan:", the patient code, the
    stage and the patient page link; no name."""
    patient = work_order.patient
    with translation.override(pdf.language_of(message.recipient)):
        url = f"{settings.SITE_URL}{reverse('patient', args=[patient.code])}"
        context = {'code': patient.code, 'stage': stage_label(STAGES[message.kind]), 'url': url}
        return render_to_string(SMS_TEMPLATES[message.kind], context).strip()


def _send_email(message):
    if not message.recipient.email:
        _outcome(message, Message.FAILED, NO_ADDRESS)
        return Message.FAILED
    work_order = WorkOrder.objects.select_related('patient').get(pk=message.work_order_id)
    subject, body = render(message, work_order)
    EmailMessage(subject, body, to=[message.recipient.email]).send()
    _outcome(message, Message.SENT)
    return Message.SENT


def _send_sms(message):
    if not message.recipient.phone:
        _outcome(message, Message.FAILED, NO_MOBILE)
        return Message.FAILED
    work_order = WorkOrder.objects.select_related('patient').get(pk=message.work_order_id)
    body = render_sms(message, work_order)
    sms.get_sender().send(message.recipient.phone, body, reference=str(message.pk))
    _outcome(message, Message.SENT)
    return Message.SENT


def _queue_sms(email):
    """The email ended `failed`: write the SMS row of the same work order, version and kind
    (once: a replay finds it) and queue its send after commit. Never fails the caller."""
    from .tasks import send_message
    try:
        with transaction.atomic():
            message, created = Message.objects.get_or_create(
                work_order_id=email.work_order_id, work_order_version=email.work_order_version,
                channel=Message.SMS,
                defaults={'kind': email.kind, 'recipient_id': email.recipient_id})
            if created:
                enqueue_on_commit(send_message, str(message.pk))
    except Exception as failure:  # noqa: BLE001 - logged by UUID and type only
        logger.error('sms message not written for message %s (%s)', email.pk,
                     type(failure).__name__)


def send(message_id):
    """Send the row's email or SMS once, and keep the outcome on it: `sent` with the time, or
    `failed` with the reason (`no_address`, `no_mobile`, `twilio <code>` or the exception
    type). One try, no retry. A `failed` email queues the SMS row. Returns the state it was
    set to, or None when the row was not `pending`."""
    with transaction.atomic():
        message = (Message.objects.select_for_update().select_related('recipient')
                   .get(pk=uuid.UUID(str(message_id))))
        if message.state != Message.PENDING:
            return None
        try:
            # A savepoint, so a database error here leaves the outer transaction usable
            # for the outcome below.
            with transaction.atomic():
                state = (_send_sms if message.channel == Message.SMS else _send_email)(message)
        except sms.SmsFailed as failure:
            _outcome(message, Message.FAILED, failure.detail)
            state = Message.FAILED
        except Exception as failure:  # noqa: BLE001 - kept on the row by type only
            _outcome(message, Message.FAILED, type(failure).__name__)
            state = Message.FAILED
        if state == Message.FAILED and message.channel == Message.EMAIL:
            _queue_sms(message)
        return state
