"""Give the included-case grant of their current term to every doctor who bought Advanced or
Premium before included cases existed (epic-subscriptions 13): run once after the deploy that
adds them.

    python manage.py grant_included_cases [--dry-run]

A doctor whose record counts today as a standard paid tier, bought by an order that has no
grant, gets one through `subscriptions.service.grant_missing_term` (each doctor in its own
transaction): today's catalogue number of included TPS branded cases of the held row, the
record's billing period and term. Prints how many were given a grant (`--dry-run`: how many
would be, nothing written). Running it again gives none. No email or name is printed.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from subscriptions import service


class Command(BaseCommand):
    help = 'Grant the included cases of the current term to paid doctors without a grant.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Count the doctors without a grant; write nothing.')

    def handle(self, *args, dry_run, **options):
        pks = service.records_missing_a_grant()
        if dry_run:
            self.stdout.write(f'grant_included_cases: {len(pks)} doctor(s) without a grant '
                              '(dry run: nothing written)')
            return
        given = 0
        for pk in pks:
            with transaction.atomic():
                if service.grant_missing_term(pk):
                    given += 1
        self.stdout.write(f'grant_included_cases: {given} doctor(s) given their included '
                          'cases')
