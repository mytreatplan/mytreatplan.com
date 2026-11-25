"""Epic-ordering 9.17: a bank transfer attempt has no session end. The open transfer attempts
opened before (with the 14-day end) lose it, so the orders waiting for a transfer are never
expired automatically."""
from django.db import migrations


def drop_transfer_deadlines(apps, schema_editor):
    Payment = apps.get_model('payments', 'Payment')
    Payment.objects.filter(gateway='bank_transfer', state='open').exclude(
        session_expires_at=None).update(session_expires_at=None)


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0007_orderline_maker_shipping'),
    ]

    operations = [
        migrations.RunPython(drop_transfer_deadlines, migrations.RunPython.noop),
    ]
