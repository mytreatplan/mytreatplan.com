# epic-subscriptions entry 1: every doctor (a user who is neither staff nor a superuser)
# without a record gets Basic (active, no subscription id, no end date, no period,
# auto-renew off, not manual, version 1), started on the day they joined (Madrid date),
# and its `backfill` change row by the system. Historical models only; no catalogue table
# is read. Going back needs nothing (0001 drops the tables).
#
# A doctor the previous release creates after this has run has no record and counts as
# Basic (subscriptions.service.effective); `manage.py assign_basic` gives them one.
import uuid
import zoneinfo

from django.db import migrations
from django.utils import timezone

MADRID = zoneinfo.ZoneInfo('Europe/Madrid')  # catalogue.resolver.VALIDITY_TIME_ZONE
BUSINESS_FIELDS = ('tier', 'subscription_id', 'started_on', 'ends_on', 'billing_period',
                   'auto_renew', 'status', 'manual')
BATCH_SIZE = 500


def basic_for_existing_doctors(apps, schema_editor):
    alias = schema_editor.connection.alias
    user_model = apps.get_model('accounts', 'User')
    record_model = apps.get_model('subscriptions', 'DoctorSubscription')
    change_model = apps.get_model('subscriptions', 'SubscriptionChange')
    users = (user_model._base_manager.using(alias)
             .filter(is_staff=False, is_superuser=False, subscription_record__isnull=True)
             .order_by('pk'))
    now = timezone.now()
    while True:
        batch = list(users.values_list('pk', 'date_joined')[:BATCH_SIZE])
        if not batch:
            return
        records, changes = [], []
        for pk, joined in batch:
            record = record_model(
                id=uuid.uuid4(), doctor_id=pk, tier='basic', subscription_id=None,
                started_on=joined.astimezone(MADRID).date(), ends_on=None,
                billing_period='', auto_renew=False, status='active', manual=False,
                version=1, actor_type='system', actor_uuid=None, created_at=now,
                updated_at=now)
            records.append(record)
            after = {name: getattr(record, name) for name in BUSINESS_FIELDS}
            after['started_on'] = after['started_on'].isoformat()
            changes.append(change_model(
                id=uuid.uuid4(), record_id=record.id, at=now, actor_type='system',
                actor_uuid=None, reason='backfill', before={}, after=after, version=1))
        record_model._base_manager.using(alias).bulk_create(records)
        change_model._base_manager.using(alias).bulk_create(changes)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0014_sync_version'),
        ('subscriptions', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(basic_for_existing_doctors, migrations.RunPython.noop),
    ]
