"""Show the applied configuration snapshot (epic-admin-interface entry 4).

    python manage.py show_config

Prints the applied version, when it was applied and the release that applied it (the
deployed git SHA; "unnamed" without one; "another release" when a release from before
the mark applied it: the next pull applies it again), the state of the pull (last pull,
last good pull, last error code) and the admin directory (id, display name, title,
active or inactive, time zone). Admin names are not patient data. Reads only (before the
first pull it prints "never pulled" and creates nothing).
"""
from django.core.management.base import BaseCommand

from integration.models import AdminDirectoryEntry, ConfigState
from mytp_core.contracts import utc_text


def _applied_by(row):
    if row.version is None:
        return '-'
    if row.applied_release_version != row.version:
        return 'another release (not recorded; the next pull applies it again)'
    return row.applied_release or 'unnamed (no REVISION file or RELEASE_SHA)'


def _time(value):
    return utc_text(value) if value else '-'


class Command(BaseCommand):
    help = 'Show the applied configuration snapshot and its admin directory.'

    def handle(self, **options):
        row = ConfigState.objects.filter(pk=ConfigState.SINGLETON).first()
        write = self.stdout.write
        if row is None:
            write('version: none (never pulled)')
        else:
            write(f'version: {row.version if row.version is not None else "none"}')
            write(f'applied at: {_time(row.applied_at)}')
            write(f'applied by release: {_applied_by(row)}')
            write(f'last pull: {_time(row.last_pull_at)}')
            write(f'last good pull: {_time(row.last_success_at)}')
            write(f'last error: {row.last_error or "-"}')
        entries = list(AdminDirectoryEntry.objects.all())
        write(f'admin directory: {len(entries)}')
        for entry in entries:
            write(f'  {entry.id}  {entry.display_name}  {entry.title or "-"}  '
                  f'{"active" if entry.active else "inactive"}  {entry.time_zone}')
