# AD-5 (story 1.8), step 1 of 3: add `uid` to contact messages, nullable and not unique
# yet, so existing rows can be filled one by one in the next migration. Its database
# default (UUID4: gen_random_uuid() on PostgreSQL) applies from here on, so rows the
# previous release inserts while the deploy migrates (or after a rollback) each get their
# own uid; on PostgreSQL it also fills the existing rows.

from django.db import migrations, models
from django.db.models.functions import UUID4


class Migration(migrations.Migration):

    dependencies = [
        ('mytp_publicsite', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='contactmessage',
            name='uid',
            field=models.UUIDField(db_default=UUID4(), editable=False, null=True),
        ),
    ]
