"""The outbox's nudge task (epic-admin-interface entry 3): enqueued once when a
transaction that recorded events commits (`integration.outbox.record`), it runs the same
idempotent sweep as the `dispatchoutbox` job, so an event usually leaves within seconds
instead of at the next minute. The sweep is short (NUDGE_SECONDS), so the site's single
worker is never held long; the job sends the rest. It takes no arguments and holds no
state."""
import logging

from django.tasks import task

from mytp_core import interface

logger = logging.getLogger('integration.outbox')

NUDGE_SECONDS = 10


@task
def dispatch_outbox_task():
    """Send the due outbox events for at most NUDGE_SECONDS. A missing configuration is
    logged, not raised: the `dispatchoutbox` job reports it (its Sentry monitor), and the
    rows wait."""
    from .outbox import dispatch
    try:
        counts = dispatch(max_seconds=NUDGE_SECONDS)
    except interface.InterfaceError as exc:
        logger.warning('outbox nudge: not sent (%s)', exc)
        return None
    return counts
