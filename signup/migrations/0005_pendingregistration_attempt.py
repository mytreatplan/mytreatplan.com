import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('eventlog', '0001_initial'),
        ('signup', '0004_email_index_required'),
    ]

    operations = [
        migrations.AddField(
            model_name='pendingregistration',
            name='attempt',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='eventlog.attempt'),
        ),
    ]
