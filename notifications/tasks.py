"""The message task (epic-case-lifecycle 10.2). The argument is the row's UUID only (AD-23);
the outcome lives on the row, one try."""
import logging

from django.tasks import task

from . import service

logger = logging.getLogger(__name__)


@task
def send_message(message_id):
    """Send the row's email unless it was already handled. Returns the new state, or None
    when nothing was sent. Failures are stored on the row; any other is logged by UUID."""
    try:
        return service.send(message_id)
    except Exception as failure:  # noqa: BLE001 - logged by UUID and type only
        logger.error('message %s not sent (%s)', message_id, type(failure).__name__)
        return None
