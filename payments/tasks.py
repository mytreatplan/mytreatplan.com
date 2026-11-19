"""The payments' tasks (AD-8): the retries are tracked on the business row
(`payments.models.WebhookLog`), not in the task backend."""
import logging

from django.tasks import task
from django.utils import timezone

logger = logging.getLogger('payments.webhooks')


@task
def confirm_webhook(log_id):
    """Re-read the payment of an accepted webhook from its gateway and confirm it
    (`payments.webhooks.process`). Enqueued by the webhook handler; when the re-read failed,
    queued again to run at the row's `next_try_at`. Its one argument is the log row's id:
    nothing of the webhook goes into the task."""
    from . import webhooks
    from .models import WebhookLog
    outcome = webhooks.process(log_id)
    if outcome == 'retry':
        row = WebhookLog.objects.get(pk=log_id)
        run_after = max(row.next_try_at or timezone.now(), timezone.now())
        confirm_webhook.using(run_after=run_after).enqueue(log_id)
    return outcome
