from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('signup', '0003_encrypt_existing_registrations'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pendingregistration',
            name='email_index',
            field=models.CharField(db_index=True, editable=False, max_length=64),
        ),
    ]
