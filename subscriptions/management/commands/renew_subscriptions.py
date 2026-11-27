"""The daily annual renewals (epic-subscriptions 8.6; `renewsubscriptions` in deploy/jobs.conf,
daily 05:00 UTC), through `subscriptions.service`:

1. `due_reminders`: queue the reminder of each annual subscription with auto-renew on that
   ends within 30 days (once per record and term);
2. `renew_due`: on (or after) `ends_on`, make the renewal order and charge the saved card;
   no card or a decline emails a payment link (once per record and term);
3. `drop_unrenewed`: the day after `ends_on`, drop a term not renewed to Basic.

    python manage.py renew_subscriptions

Prints the counts only (no email, name or card). Running it again the same day sends,
orders, charges and drops nothing more. A failure for one record is logged by its UUID and
never stops the run.
"""
from django.core.management.base import BaseCommand

from catalogue import resolver
from subscriptions import service


class Command(BaseCommand):
    help = 'Remind, renew and end the annual subscriptions that are due.'

    def handle(self, *args, **options):
        day = resolver.today()
        reminders = service.due_reminders(day)
        renewed = service.renew_due(day)
        dropped = service.drop_unrenewed(day)
        outcomes = ', '.join(f'{name} {count}' for name, count in sorted(renewed.items()))
        self.stdout.write(f'renew_subscriptions: {reminders} reminder(s) queued; '
                          f'renewals: {outcomes or "none"}; {dropped} dropped to Basic')
