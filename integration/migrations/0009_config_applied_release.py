"""The mark of the release that applied the configuration snapshot
(`ConfigState.applied_release`, `applied_release_version`; integration.config).

Both columns are filled by the database when a release from before them creates the row
(a database default, a nullable column), and that release saves the row with
`update_fields=['version', 'applied_at']`, so it keeps working during the deploy and
after a rollback. The existing row gets no mark: the first pull of this release applies
the admin site's current snapshot again."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integration', '0008_interfacealert'),
    ]

    operations = [
        migrations.AddField(
            model_name='configstate',
            name='applied_release',
            field=models.CharField(blank=True, db_default='', default='', max_length=64),
        ),
        migrations.AddField(
            model_name='configstate',
            name='applied_release_version',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
    ]
