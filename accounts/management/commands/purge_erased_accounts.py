"""Delete the erased accounts whose retention has passed (accounts.erasure).

    python manage.py purge_erased_accounts

Run daily by the `purgeerased` job in deploy/jobs.conf (through `manage.py runjob`). It
deletes each account erased in the admin whose `purge_after` has passed, with its legal
choices. It prints the count only, never personal values.
"""
from django.core.management.base import BaseCommand

from accounts.erasure import purge_erased


class Command(BaseCommand):
    help = 'Delete the erased accounts, with their legal choices, once their retention has passed.'

    def handle(self, **options):
        self.stdout.write(str(purge_erased()))
