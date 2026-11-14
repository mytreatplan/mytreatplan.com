"""Give Basic to every doctor without a subscription record (epic-subscriptions entry 1):
after a deploy, for the doctors the previous release created between the migration
(subscriptions 0002) and the switch.

    python manage.py assign_basic [--dry-run]

Each doctor (neither staff nor a superuser) without a record, in its own transaction,
goes through subscriptions.service.ensure_basic: Basic, started on the day they joined
(Madrid date), with a `backfill` change row by the system. Prints how many were given a
record (`--dry-run`: how many would be, nothing written). Running it again gives none.
No email or name is printed.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from catalogue import resolver
from subscriptions import service


class Command(BaseCommand):
    help = 'Give Basic to every doctor without a subscription record.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Count the doctors without a record; write nothing.')

    def handle(self, *args, dry_run, **options):
        User = get_user_model()
        pks = service.doctors_without_a_record()
        if dry_run:
            self.stdout.write(f'assign_basic: {len(pks)} doctor(s) without a record '
                              '(dry run: nothing written)')
            return
        given = 0
        for pk in pks:
            with transaction.atomic():
                user = User.objects.filter(pk=pk).first()
                if user is None:
                    continue
                if service.ensure_basic(user, service.SYSTEM_ACTOR, reason=service.BACKFILL,
                                        started_on=resolver.today(user.date_joined)):
                    given += 1
        self.stdout.write(f'assign_basic: {given} doctor(s) given Basic')
