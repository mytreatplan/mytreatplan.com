"""Apply the event log's retention (signup entry 10, CAP-10; epic-login entry 8).

    python manage.py eventlog_retention

Run daily by the `eventlogretention` job in deploy/jobs.conf (through `manage.py
runjob`). Signup's rules first: it clears the personal values (`data`) of the events of
attempts that ended 30 days ago or more, then deletes the attempts that ended 2 years ago
or more with their events. Then the activity rule: login and page records are never
anonymized and are deleted 5 calendar years after they end. It prints counts only, never
personal values. See eventlog.retention.
"""
from django.core.management.base import BaseCommand

from eventlog import retention


class Command(BaseCommand):
    help = ('Anonymize the event log 30 days after an attempt ends; delete it after 2 years. '
            'Delete login and page records 5 years after they end.')

    def handle(self, **options):
        anonymized = retention.anonymize()
        attempts, events = retention.delete_old()
        activity_attempts, activity_events = retention.delete_old_activity()
        self.stdout.write(f'eventlog_retention: {anonymized} events anonymized, '
                          f'{attempts} attempts deleted ({events} events), '
                          f'{activity_attempts} activity attempts deleted '
                          f'({activity_events} events)')
