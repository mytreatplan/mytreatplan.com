# R2 / AD-14 (epic-regions 2.2), step 2 of 2: every user without a home region (the rows
# that existed before step 1, and those the previous release inserted since) gets the
# instance region (settings.REGION_PROFILE at migrate time: eu on dev and production, ae
# on dev-ae). A migration of its own: PostgreSQL must not alter the table in the same
# transaction as this data change. Going back needs nothing (step 1 drops the column).
#
# A row the previous release inserts after this has run keeps '' (see User.home_region);
# `fill_home_regions` can be run again for it.

from django.conf import settings
from django.db import migrations

UNSET = ''


def fill_home_regions(apps, schema_editor):
    """Set the instance region on every user whose home region is unset. Only that column
    is written (no save(), no other field touched)."""
    user = apps.get_model('accounts', 'User')
    user._base_manager.using(schema_editor.connection.alias).filter(
        home_region=UNSET).update(home_region=settings.REGION_PROFILE.code)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_home_region_add'),
    ]

    operations = [
        migrations.RunPython(fill_home_regions, migrations.RunPython.noop),
    ]
