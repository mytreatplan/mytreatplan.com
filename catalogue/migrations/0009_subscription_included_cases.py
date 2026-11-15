"""A subscription's included cases per product category (epic-catalogue story 5.17,
mytp-core 0.27.0): one nullable, read-only column, filled by the snapshot apply.

Nullable, so the previous release can still apply a snapshot while this one is being
deployed (it leaves the column empty: no included cases, which the resolver reads as 0).
Nothing clears the applied snapshot version here: every release applies the admin site's
current snapshot again at its first pull (integration.config), which fills the column."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalogue', '0008_catalogue_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscription',
            name='included_cases',
            field=models.JSONField(null=True),
        ),
    ]
