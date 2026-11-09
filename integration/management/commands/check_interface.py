"""Check the interface's health (epic-admin-interface entry 10): a stuck outbox and a
stale configuration snapshot.

    python manage.py check_interface

Run every 5 minutes by the `checkinterface` job in deploy/jobs.conf. Sends one Sentry
warning per outage per kind (integration.alerts): the oldest pending outbox event older
than OUTBOX_ALERT_MINUTES (15), or no good configuration pull for CONFIG_ALERT_MINUTES
(60). Prints one line per kind with counts and ages only, and exits 0 either way (the
warning is the alert). Never sends, retries or changes an event, and never pulls.
"""
import sentry_sdk
from django.core.management.base import BaseCommand

from integration import alerts


class Command(BaseCommand):
    help = 'Warn (once per outage) about a stuck outbox or a stale configuration snapshot.'

    def handle(self, **options):
        for finding in alerts.check():
            limit = alerts.threshold(finding.kind)
            self.stdout.write(f'check_interface: {finding.kind} {finding.outcome} '
                              f'({finding.summary}; threshold {limit} minutes)')
        # A job process ends right after this: send the queued warning first.
        sentry_sdk.flush(timeout=5)
