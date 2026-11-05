"""Send the due outbox events to the admin site (epic-admin-interface entry 3).

    python manage.py dispatch_outbox

Run every minute by the `dispatchoutbox` job in deploy/jobs.conf (and, as a task, after
each transaction that records an event). An idempotent sweep: it can run twice at once,
or be killed, without an event being applied twice (integration.outbox). Prints the
counts only. With pending events but no admin site or signing key configured, it fails
with one line (the job's Sentry monitor then shows it).
"""
from django.core.management.base import BaseCommand, CommandError

from integration.outbox import dispatch
from mytp_core import interface


class Command(BaseCommand):
    help = 'Send the due outbox events to the admin site.'

    def handle(self, **options):
        try:
            counts = dispatch()
        except interface.InterfaceError as exc:
            raise CommandError(f'dispatch_outbox: {exc}') from None
        self.stdout.write('dispatch_outbox: {delivered} delivered, {rejected} rejected, '
                          '{retry} to retry'.format(**counts))
