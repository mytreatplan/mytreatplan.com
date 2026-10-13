import accounts.crypto
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Attempt',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(max_length=30)),
                ('started_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('ended_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('outcome', models.CharField(blank=True, max_length=50)),
            ],
            options={
                'ordering': ['-started_at'],
            },
        ),
        migrations.CreateModel(
            name='Event',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('step', models.CharField(blank=True, max_length=50)),
                ('event', models.CharField(max_length=50)),
                ('field', models.CharField(blank=True, max_length=100)),
                ('reason', models.CharField(blank=True, max_length=100)),
                ('data', accounts.crypto.EncryptedJSONField(blank=True, null=True)),
                ('attempt', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='eventlog.attempt')),
            ],
            options={
                'ordering': ['created_at', 'id'],
            },
        ),
    ]
