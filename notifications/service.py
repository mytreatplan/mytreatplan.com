"""Case messages (epic-case-lifecycle 10.2). `case_message` runs in the move's transaction
(get-or-create of the row, one per work order, version and channel, then the send queued on
commit); `send` runs in the task, once per row (the state is the once-only mark; a crash
between sending and marking can still send twice, as the order emails)."""
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

from .models import Message

logger = logging.getLogger(__name__)

NO_ADDRESS = 'no_address'
# The stage each kind announces: the label never follows the work order's live stage.
STAGES = {Message.PENDING_APPOINTMENT: stages.PENDING_APPOINTMENT,
          Message.PENDING_REVIEW: stages.PENDING_REVIEW,
          Message.PENDING_APPROVAL: stages.PENDING_APPROVAL}
TEMPLATES = {kind: f'notifications/email/{kind}' for kind, _label in Message.KINDS}


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


def send(message_id):
    """Send the email of the row once, and keep the outcome on it: `sent` with the time, or
    `failed` with `no_address` or the exception type. One try, no retry. Returns the state
    it was set to, or None when the row was not `pending`."""
    with transaction.atomic():
        message = (Message.objects.select_for_update().select_related('recipient')
                   .get(pk=uuid.UUID(str(message_id))))
        if message.state != Message.PENDING:
            return None
        try:
            # A savepoint, so a database error here leaves the outer transaction usable
            # for the outcome below.
            with transaction.atomic():
                if not message.recipient.email:
                    _outcome(message, Message.FAILED, NO_ADDRESS)
                    return Message.FAILED
                work_order = (WorkOrder.objects.select_related('patient')
                              .get(pk=message.work_order_id))
                subject, body = render(message, work_order)
                EmailMessage(subject, body, to=[message.recipient.email]).send()
        except Exception as failure:  # noqa: BLE001 - kept on the row by type only
            _outcome(message, Message.FAILED, type(failure).__name__)
            return Message.FAILED
        _outcome(message, Message.SENT)
        return Message.SENT
