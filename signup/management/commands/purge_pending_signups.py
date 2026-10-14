"""Purge unfinished signups (signup entry 10).

    python manage.py purge_pending_signups

Run hourly by the `purgepending` job in deploy/jobs.conf (through `manage.py runjob`).
It deletes the pending registrations created 24 h ago or more (ending their attempts as
`timeout`), ends the open signup attempts of that age no registration points to, and
deletes the expired rows of the database cache. It prints counts only, never personal
values. See signup.retention.
"""
from django.core.management.base import BaseCommand

from signup import retention


class Command(BaseCommand):
    help = 'Delete signups left unfinished for 24 h, end their attempts, clean the cache.'

    def handle(self, **options):
        purged = retention.purge_pending()
        orphans = retention.end_orphan_attempts()
        cache_rows = retention.clear_expired_cache()
        self.stdout.write(f'purge_pending_signups: {purged} pending registrations purged, '
                          f'{orphans} orphan attempts ended, '
                          f'{cache_rows} expired cache rows deleted')
