"""The sample background task (AD-8, epic-platform entry 9): no business effect, it only
proves the site's worker on a server (`manage.py enqueue_sample_task`, deploy/README.md
"Background tasks"). Written as every task is: django.tasks and the mytp-core helpers
only, never the backend; its effect goes through run_once, keyed by the record's UUID.
"""
import logging

from django.tasks import task

from mytp_core.tasks import run_once

logger = logging.getLogger(__name__)

SAMPLE_EFFECT = 'sample.logged'


def _log_effect(record_uuid):
    logger.info('sample task effect for %s', record_uuid)


@task
def sample_task(record_uuid):
    """Log one line for `record_uuid`, once however often the task runs. Returns whether
    the effect happened in this run."""
    return run_once(SAMPLE_EFFECT, record_uuid, lambda: _log_effect(record_uuid))
