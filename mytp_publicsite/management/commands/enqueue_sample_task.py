"""Enqueue the sample task (mytp_publicsite.tasks.sample_task) to check the worker.

    python manage.py enqueue_sample_task [--record <uuid>]

Enqueues it the way a view does: inside a transaction, with enqueue_on_commit. The
worker (journalctl -u mytp-<site>-worker) logs `sample task effect for <uuid>` once;
enqueuing again with the same --record runs the task again but logs
`effect sample.logged for <uuid> already done; skipped` instead.
"""
import uuid

from django.core.management.base import BaseCommand
from django.db import transaction

from mytp_core.tasks import enqueue_on_commit
from mytp_publicsite.tasks import sample_task


class Command(BaseCommand):
    help = 'Enqueue the sample background task for a record UUID (a new one by default).'

    def add_arguments(self, parser):
        parser.add_argument('--record', type=uuid.UUID, default=None,
                            help='the record UUID (default: a new one)')

    def handle(self, *, record, **options):
        record = str(record or uuid.uuid4())
        with transaction.atomic():
            enqueue_on_commit(sample_task, record)
        self.stdout.write(f'sample task enqueued for {record}')
