"""Delete the outbox events delivered more than 30 days ago (epic-admin-interface entry 3).

    python manage.py purge_outbox

Run daily by the `purgeoutbox` job in deploy/jobs.conf. Every event delivered more than
30 days ago goes, payload included; rejected and pending events are kept (an aggregate's
sequence goes on from its OutboxAggregate counter). Prints the count only.
"""
from django.core.management.base import BaseCommand

from integration.models import OUTBOX_RETENTION_DAYS
from integration.outbox import purge


class Command(BaseCommand):
    help = f'Delete outbox events delivered more than {OUTBOX_RETENTION_DAYS} days ago.'

    def handle(self, **options):
        self.stdout.write(f'purge_outbox: {purge()} events deleted')
