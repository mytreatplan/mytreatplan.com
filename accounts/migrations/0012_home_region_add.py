# R2 / AD-14 (epic-regions 2.2), step 1 of 2: add `home_region` to users. Its database
# default is region-neutral (''), so this migration is the same on every instance; the
# existing rows, and the rows the previous release inserts while the deploy migrates, get
# '' and the next migration fills them. New rows get the instance region from the model's
# Python default (accounts.models.instance_region).

import accounts.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0011_uid_unique'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='home_region',
            field=models.CharField(choices=[('eu', 'eu'), ('ae', 'ae')], db_default='',
                                   default=accounts.models.instance_region, editable=False,
                                   max_length=2, verbose_name='home region'),
        ),
    ]
