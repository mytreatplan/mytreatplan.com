"""Ask VIES again about the fiscal entities whose EU VAT check is pending
(epic-doctor-profile entry 6).

    python manage.py retry_vies

Run every 15 minutes by the `viesretry` job in deploy/jobs.conf. Each pending entity is due
15 minutes after its first failed check, 1 hour after the second, then every 6 hours, until
VIES answers (accounts.fiscal.retry_due). An entity still pending after 3 days gives one
error log (its id only) for the Sentry monitor. Prints the counts only, never a tax id.
"""
from django.core.management.base import BaseCommand

from accounts import fiscal


class Command(BaseCommand):
    help = 'Retry the pending VIES checks of the fiscal entities.'

    def handle(self, **options):
        counts = fiscal.retry_due()
        self.stdout.write('retry_vies: ' + ', '.join(f'{name} {count}'
                                                     for name, count in counts.items()))
