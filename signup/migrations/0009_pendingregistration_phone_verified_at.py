# Signup entry 7 (story 2.7): when the mobile was verified, so the account is created only
# once both contacts are.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('signup', '0008_pending_rows_back_to_legal_step'),
    ]

    operations = [
        migrations.AddField(
            model_name='pendingregistration',
            name='phone_verified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
