"""The receivers of 10.1's stage moves (`patients.moves`): each writes the message row inside
the move's transaction and queues the send after commit; none sends, renders or reads a
doctor's email."""
from patients import moves

from . import service
from .models import Message

NAME = 'notifications_case'
KINDS = {'request_appointment': Message.PENDING_APPOINTMENT,
         'send_for_review': Message.PENDING_REVIEW,
         'send_for_approval': Message.PENDING_APPROVAL}


def _receiver(kind):
    def receiver(work_order):
        service.case_message(work_order, kind)
    return receiver


def register_all():
    for move, kind in KINDS.items():
        moves.register(move, NAME, _receiver(kind))
