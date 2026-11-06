"""Sweep the file bucket (storage.sweep): forget expired pending uploads, delete
`quarantine/` objects with no StoredObject row (older than a day, or over their limit),
and enqueue again the deletions still pending.

    python manage.py sweep_storage

Run hourly by the `sweepstorage` job in deploy/jobs.conf. Prints counts only. On a site
without the STORAGE_* bucket settings the bucket step is skipped.
"""
from django.core.management.base import BaseCommand

from storage.sweep import sweep


class Command(BaseCommand):
    help = 'Delete row-less quarantine objects and expired pending uploads; retry pending deletions.'

    def handle(self, **options):
        counts = sweep()
        orphans = ('skipped (bucket not configured)' if counts['orphans'] is None
                   else f"{counts['orphans']} deleted")
        self.stdout.write(f"sweep_storage: {counts['pending']} pending uploads forgotten, "
                          f"orphan objects {orphans}, {counts['redeletes']} deletions enqueued again")
