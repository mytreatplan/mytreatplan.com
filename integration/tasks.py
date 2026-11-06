"""The interface's tasks.

- `dispatch_outbox_task` (entry 3): the outbox's nudge task, enqueued once when a
  transaction that recorded events commits (`integration.outbox.record`); it runs the
  same idempotent sweep as the `dispatchoutbox` job, so an event usually leaves within
  seconds instead of at the next minute. The sweep is short (NUDGE_SECONDS), so the
  site's single worker is never held long; the job sends the rest. It takes no arguments
  and holds no state.
- `pull_config_task` (entry 4): the configuration snapshot pull, enqueued by the admin
  site's nudge (`integration.config.request_pull`).
- `resync_client_task` (entry 8): emits one account's client event again
  (accounts.client_sync.resync), enqueued by the admin site's re-sync.
"""
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


@task
def pull_config_task():
    """Pull the configuration snapshot now (entry 4): enqueued by the admin site's nudge
    (`POST /api/v1/config/nudge`, coalesced: at most one queued), the same pull as the
    `pullconfig` job. A refusal or failure is recorded and logged, not raised: the job
    tries again within 5 minutes. Returns the outcome and version, or the error code."""
    from django.core.cache import cache

    from . import config
    cache.delete(config.PULL_PENDING_KEY)
    try:
        outcome, version = config.pull()
    except config.PullFailed as exc:
        return {'error': exc.code}
    return {'outcome': outcome, 'version': version}


@task
def resync_client_task(uid):
    """Emit the account `uid`'s current state again (entry 8): enqueued by the admin
    site's re-sync (`POST /api/v1/clients/resync`). Returns the event type recorded
    (`client.updated` / `client.erased`), None for a staff account never sent, or
    'unknown' for a uid that is no longer here (purged meanwhile)."""
    from django.contrib.auth import get_user_model

    from accounts import client_sync
    try:
        event_type = client_sync.resync(uid)
    except get_user_model().DoesNotExist:
        logger.warning('client resync: %s is not here', uid)
        return 'unknown'
    logger.info('client resync: %s -> %s', uid, event_type or 'nothing (staff)')
    return event_type
