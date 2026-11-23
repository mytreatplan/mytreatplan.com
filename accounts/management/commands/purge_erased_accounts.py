"""Archive and delete the erased accounts whose retention has passed (accounts.erasure).

    python manage.py purge_erased_accounts

Run daily by the `purgeerased` job in deploy/jobs.conf (through `manage.py runjob`). For
each account erased in the admin whose `purge_after` has passed it writes and verifies its
write-once archive object (archive.service), then deletes the live rows. It prints the
count deleted, and the count kept for the next run when an archive failed, never personal values. It refuses (non-zero exit) while an
ARCHIVE_* setting is unset.
"""
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from accounts.erasure import purge_erased


class Command(BaseCommand):
    help = 'Archive, then delete, the erased accounts once their retention has passed.'

    def handle(self, **options):
        stats = {}
        try:
            deleted = purge_erased(stats=stats)
        except ImproperlyConfigured as error:
            raise CommandError(f'purge refused: {error}') from None
        self.stdout.write(str(deleted))
        if stats.get('failed'):
            self.stdout.write(f"{stats['failed']} account(s) kept for the next run: "
                              'archive or record failed (see Sentry)')
            for message in stats.get('errors', []):
                self.stdout.write(f'  {message}')
