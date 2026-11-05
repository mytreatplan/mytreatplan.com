"""Delete the interface call log older than 90 days (epic-admin-interface entry 1).

    python manage.py purge_interface_calls

Run daily by the `purgeinterfacecalls` job in deploy/jobs.conf. Prints the count only.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from integration.models import RETENTION_DAYS, InterfaceCall


class Command(BaseCommand):
    help = f'Delete interface call log rows older than {RETENTION_DAYS} days.'

    def handle(self, **options):
        cutoff = timezone.now() - timedelta(days=RETENTION_DAYS)
        deleted, _ = InterfaceCall.objects.filter(received_at__lt=cutoff).delete()
        self.stdout.write(f'purge_interface_calls: {deleted} calls deleted')
