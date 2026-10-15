# Account erasure: when an account was erased, when it is purged, and its real email blind
# index once `email_index` is a tombstone (accounts.erasure). Existing rows get null / blank.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_step1_personal_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='erased_at',
            field=models.DateTimeField(blank=True, editable=False, null=True, verbose_name='erased at'),
        ),
        migrations.AddField(
            model_name='user',
            name='erased_email_index',
            field=models.CharField(blank=True, db_index=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name='user',
            name='purge_after',
            field=models.DateTimeField(blank=True, db_index=True, editable=False, null=True, verbose_name='purge after'),
        ),
    ]
