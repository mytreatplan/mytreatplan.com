"""Remind the doctors of unpaid bank transfers (epic-payments entry 5): one email on day 7
of each open transfer attempt, through payments.transfers.remind_due.

    python manage.py remind_transfers

Prints how many reminders were sent. Running it again sends none more. No address,
reference, amount or bank detail is printed.
"""
from django.core.management.base import BaseCommand

from payments import transfers


class Command(BaseCommand):
    help = 'Send the day-7 reminder of the unpaid bank transfer orders.'

    def handle(self, *args, **options):
        self.stdout.write(f'remind_transfers: {transfers.remind_due()} reminder(s) sent')
