"""Delete the outbox events delivered more than 30 days ago (epic-admin-interface entry 3)
and the remote write idempotency keys older than 7 days (entry 6).

    python manage.py purge_outbox

Run daily by the `purgeoutbox` job in deploy/jobs.conf. Every event delivered more than
30 days ago goes, payload included; rejected and pending events are kept (an aggregate's
sequence goes on from its OutboxAggregate counter). Every RemoteWrite key older than 7 days
goes too (decision 2026-11-09): a retry after that is a new write, which the record's
version still guards. Prints the counts only.
"""
from django.core.management.base import BaseCommand

from integration.models import OUTBOX_RETENTION_DAYS, REMOTE_WRITE_RETENTION_DAYS
from integration.outbox import purge
from integration.remote import purge_writes


class Command(BaseCommand):
    help = (f'Delete outbox events delivered more than {OUTBOX_RETENTION_DAYS} days ago and '
            f'remote write keys older than {REMOTE_WRITE_RETENTION_DAYS} days.')

    def handle(self, **options):
        events = purge()
        keys = purge_writes()
        self.stdout.write(f'purge_outbox: {events} events deleted, {keys} remote write keys '
                          f'deleted')
