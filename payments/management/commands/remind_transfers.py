"""Remind the doctors of unpaid bank transfers (epic-payments entry 5, epic-ordering 9.17): one
email every 7 days while an order's transfer attempt is open, through
payments.transfers.remind_due.

    python manage.py remind_transfers

Prints how many reminders were sent. Running it again within 7 days sends none more. No address,
reference, amount or bank detail is printed.
"""
from django.core.management.base import BaseCommand

from payments import transfers


class Command(BaseCommand):
    help = 'Send the 7-day reminders of the unpaid bank transfer orders.'

    def handle(self, *args, **options):
        self.stdout.write(f'remind_transfers: {transfers.remind_due()} reminder(s) sent')
