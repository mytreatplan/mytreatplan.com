# R5 (epic-regions 2.6), step 2 of 2: every legal document without a region (the rows
# that existed before step 1, and those the previous release inserted since) gets the
# instance region (settings.REGION_PROFILE at migrate time: eu on dev and production, ae
# on dev-ae, whose existing rows become its placeholders). A migration of its own:
# PostgreSQL must not alter the table in the same transaction as this data change.
#
# Only `region` is written: the titles and bodies stay as they are, even on a site whose
# company differs from the one the seeded drafts name (they are flagged provisional by
# step 1 until the lawyer's texts replace them in the admin). The acceptances keep
# pointing at the same rows. Going back needs nothing (step 1 drops the column).
#
# A row the previous release inserts after this has run keeps '' and is read as the
# site's own (offered, listed and numbered with the others: see LegalDocument.region);
# saving it through the model, or running `fill_regions` again, gives it the region.

from django.conf import settings
from django.db import migrations

UNSET = ''


def fill_regions(apps, schema_editor):
    """Set the instance region on every document whose region is unset. Only that column
    is written (no save(), no other field touched)."""
    document = apps.get_model('legal', 'LegalDocument')
    document._base_manager.using(schema_editor.connection.alias).filter(
        region=UNSET).update(region=settings.REGION_PROFILE.code)


class Migration(migrations.Migration):

    dependencies = [
        ('legal', '0006_region_provisional_add'),
    ]

    operations = [
        migrations.RunPython(fill_regions, migrations.RunPython.noop),
    ]
