# Story 2.2: the pending registration's email encrypted in the app, with a blind index.
# Schema only; 0003 encrypts the existing rows, 0004 makes email_index required.

import accounts.crypto
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('signup', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='pendingregistration',
            name='email_index',
            field=models.CharField(db_index=True, editable=False, max_length=64, null=True),
        ),
        migrations.AlterField(
            model_name='pendingregistration',
            name='email',
            field=accounts.crypto.EncryptedEmailField(max_length=254),
        ),
    ]
