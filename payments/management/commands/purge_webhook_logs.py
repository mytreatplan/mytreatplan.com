"""Delete the payment webhook log rows older than 180 days (epic-payments entry 2; the
`purgewebhooklogs` job, daily).

    python manage.py purge_webhook_logs

Prints how many were deleted.
"""
from django.core.management.base import BaseCommand

from payments import webhooks


class Command(BaseCommand):
    help = 'Delete the payment webhook log rows older than 180 days.'

    def handle(self, *args, **options):
        self.stdout.write(f'purge_webhook_logs: {webhooks.purge_logs()} row(s) deleted')
