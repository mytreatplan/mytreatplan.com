from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_encrypt_existing_users'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='email_index',
            field=models.CharField(editable=False, max_length=64, unique=True),
        ),
    ]
