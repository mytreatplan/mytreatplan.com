"""Write this region's staging-only sample record (epic-admin-interface entry 5), which the
admin site reads remotely.

    python manage.py seed_sample_record [--assignee <admin user UUID>]
        [--patient-name TEXT] [--amount 123.45] [--currency EUR] [--note TEXT]

Only with INTERFACE_SAMPLE_RECORDS=True. Replaces any earlier sample record, so the region
has exactly one; prints its UUID only (never the patient name). `--assignee` is the admin
user (a Clinical User, to check the `assigned` scope) the record is assigned to.
"""
import uuid
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from integration import sample
from integration.models import SampleRecord
from mytp_core.money import CURRENCIES, Money


class Command(BaseCommand):
    help = 'Write the staging-only sample record the admin site reads (replaces any other).'

    def add_arguments(self, parser):
        parser.add_argument('--assignee', default='',
                            help='The UUID of the admin user the record is assigned to.')
        parser.add_argument('--patient-name', default='Sample Patient',
                            help='A made-up name (never a real patient).')
        parser.add_argument('--amount', default='120.00')
        parser.add_argument('--currency', default='EUR', choices=CURRENCIES)
        parser.add_argument('--note', default='Staging sample record (entry 5).')

    def handle(self, *args, assignee='', patient_name='', amount='', currency='EUR',
               note='', **options):
        if not sample.enabled():
            raise CommandError('INTERFACE_SAMPLE_RECORDS is off: the sample record is for '
                               'staging only.')
        try:
            assignee_uuid = uuid.UUID(assignee) if assignee else None
        except ValueError:
            raise CommandError('--assignee must be a UUID.') from None
        try:
            money = Money(Decimal(amount), currency)
        except (InvalidOperation, ValueError, TypeError):
            raise CommandError('--amount must be a decimal amount such as 120.00.') from None
        if not patient_name.strip() or len(patient_name) > 150 or len(note) > 500:
            raise CommandError('--patient-name: 1 to 150 characters; --note: at most 500.')
        with transaction.atomic():
            SampleRecord.objects.all().delete()
            record = SampleRecord.objects.create(
                patient_name=patient_name, amount_value=money.amount, currency=money.currency,
                assignee_uuid=assignee_uuid, note=note)
        self.stdout.write(f'sample record {record.uuid}'
                          + (f' assigned to {assignee_uuid}' if assignee_uuid else ''))
