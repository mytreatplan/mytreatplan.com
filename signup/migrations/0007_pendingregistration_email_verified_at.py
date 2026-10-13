# Signup entry 5, email-first resume (story 2.5b): when the email was verified, so a
# resumed signup skips the email code step.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('signup', '0006_step1_personal_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='pendingregistration',
            name='email_verified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
