# Story 2.2: email and names encrypted in the app; email unique through its blind index.
# Schema only; 0004 encrypts the existing rows, 0005 makes email_index required.

import accounts.crypto
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_email_username'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='user',
            name='accounts_user_email_ci_unique',
        ),
        migrations.AddField(
            model_name='user',
            name='email_index',
            field=models.CharField(editable=False, max_length=64, null=True, unique=True),
        ),
        migrations.AlterField(
            model_name='user',
            name='email',
            field=accounts.crypto.EncryptedEmailField(max_length=254, verbose_name='email address'),
        ),
        migrations.AlterField(
            model_name='user',
            name='first_name',
            field=accounts.crypto.EncryptedCharField(blank=True, max_length=150, verbose_name='first name'),
        ),
        migrations.AlterField(
            model_name='user',
            name='last_name',
            field=accounts.crypto.EncryptedCharField(blank=True, max_length=150, verbose_name='last name'),
        ),
    ]
