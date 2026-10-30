# AD-5 (story 1.8), step 3 of 3: `uid` of users becomes unique and required, with uuid4 as
# the default for new rows. Never editable.

import uuid

from django.db import migrations, models
from django.db.models.functions import UUID4


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_uid_fill'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='uid',
            field=models.UUIDField(db_default=UUID4(), default=uuid.uuid4, editable=False,
                                   unique=True),
        ),
    ]
