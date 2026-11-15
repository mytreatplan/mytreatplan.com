"""The code-confirmed contact changes (epic-doctor-profile entry 3): the pending-change
table and the time before which remembered devices are forgotten. The unique mobile
follows in 0017."""

import django.db.models.deletion
import mytp_core.crypto
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0015_profile'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='PendingContactChange',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('mobile', 'mobile phone number'), ('email', 'contact email')], max_length=10)),
                ('value', mytp_core.crypto.EncryptedCharField(max_length=254)),
                ('value_index', models.CharField(editable=False, max_length=64)),
                ('created_at', models.DateTimeField()),
            ],
        ),
        migrations.AddField(
            model_name='user',
            name='devices_valid_after',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='pendingcontactchange',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pending_contact_changes', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddConstraint(
            model_name='pendingcontactchange',
            constraint=models.UniqueConstraint(fields=('user', 'kind'), name='accounts_pending_contact_change_one_per_kind'),
        ),
    ]
