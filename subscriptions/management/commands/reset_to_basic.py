"""Put a test doctor back on Basic on a staging site, so the same account can buy a paid
subscription again (the purchase checks of epic-subscriptions 5 and 13).

    python manage.py reset_to_basic <uid or email>

Runs only where `SENTRY_ENVIRONMENT` names a staging site (dev, dev-ae), or on a
developer's machine (`development`, its default, with DEBUG on); anywhere else it refuses
and writes nothing (a production site without the line reads `development` but runs with
DEBUG off). The record (standard or manual,
paid or not) becomes a standard Basic through subscriptions.service.reset_to_basic: a
version-checked change by the system with reason `test_reset`, kept in the doctor's
history. Orders, payments, invoices and saved cards are left as they are. A staff or
superuser account has no subscription: the command refuses it. The email is never
printed.
"""
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from subscriptions import service

# The sites where a doctor may be reset (SENTRY_ENVIRONMENT; production is never listed).
STAGING = frozenset({'dev', 'dev-ae'})
LOCAL = 'development'


class Command(BaseCommand):
    help = 'Put a test doctor back on Basic (staging sites only).'

    def add_arguments(self, parser):
        parser.add_argument('doctor', help="The doctor's uid or email.")

    def handle(self, *args, doctor, **options):
        site = getattr(settings, 'SENTRY_ENVIRONMENT', '')
        if not (site in STAGING or (site == LOCAL and settings.DEBUG)):
            raise CommandError(f'reset_to_basic runs on a staging site only, not on '
                               f'"{site}".')
        user = self.found_user(doctor)
        before = service.record_of(user)
        try:
            record = service.reset_to_basic(user)
        except service.RecordGone:
            raise CommandError('A staff or superuser account has no subscription: use a '
                               'doctor account.') from None
        was = f'{before.tier}{" (manual)" if before.manual else ""}' if before else 'no record'
        self.stdout.write(f'reset_to_basic: {user.uid} was {was}, now basic '
                          f'(version {record.version})')

    @staticmethod
    def found_user(text):
        User = get_user_model()
        try:
            users = User.objects.filter(uid=uuid.UUID(text.strip()))
        except ValueError:
            users = User.objects.by_email(text.strip())
        user = users.first()
        if user is None:
            raise CommandError('No such user.')
        return user
