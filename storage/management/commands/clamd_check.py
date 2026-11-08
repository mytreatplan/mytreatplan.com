"""Check the virus scanner the scan worker uses (epic-platform entry 12): clamd answers
on STORAGE_CLAMD_SOCKET, and its signatures are current.

    python manage.py clamd_check

Prints clamd's version line (engine / signature version / signature date) and exits
non-zero when clamd cannot be reached or its signatures are older than MAX_AGE_DAYS
(freshclam not updating). Run it as the site user (deploy/README.md "Object storage" ->
"Scanning"): it also proves that user may open the socket.
"""
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from storage import clamd

# freshclam checks for new signatures every hour; daily ones are published every day.
MAX_AGE_DAYS = 3


class Command(BaseCommand):
    help = "Check that clamd answers on STORAGE_CLAMD_SOCKET and its signatures are current."

    def handle(self, **options):
        try:
            text = clamd.version(socket_path=settings.STORAGE_CLAMD_SOCKET)
        except clamd.ClamdUnavailable as exc:
            raise CommandError(f'clamd_check: clamd is not reachable ({exc})') from exc
        self.stdout.write(f'clamd: {text}')
        signed = clamd.signature_date(text)
        if signed is None:
            raise CommandError('clamd_check: no signature date in the answer (no database?)')
        age = datetime.now(timezone.utc).replace(tzinfo=None) - signed
        if age > timedelta(days=MAX_AGE_DAYS):
            raise CommandError(f'clamd_check: signatures are {age.days} days old '
                               f'(more than {MAX_AGE_DAYS}); check clamav-freshclam')
        self.stdout.write(f'signatures: current ({signed:%Y-%m-%d})')
