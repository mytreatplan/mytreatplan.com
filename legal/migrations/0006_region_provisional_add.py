# R5 (epic-regions 2.6), step 1 of 2: legal documents get a region and a `provisional`
# flag, and a version number becomes unique per region and kind.
#
# `region`'s database default is region-neutral ('', so this migration is the same on
# every instance): the existing rows, and the rows the previous release inserts while the
# deploy migrates, get '' and the next migration fills them. New rows get the instance
# region from the model (legal.models.site_region). `provisional` is true for every
# existing row (none is the lawyer's final text) and by default for new ones.
#
# Going back drops both columns and restores the kind + version constraint; it fails if
# the same kind and version exist in two regions. On one instance they don't: it holds
# only its own region's documents, and a row with an unset region (inserted by the
# previous release) counts as the site's in the version numbering, so it never shares a
# number with another of the site's. Acceptances (LegalChoice) are not touched either way.

import legal.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('legal', '0005_uid_unique'),
    ]

    operations = [
        migrations.AddField(
            model_name='legaldocument',
            name='region',
            field=models.CharField(choices=[('eu', 'eu'), ('ae', 'ae')], db_default='',
                                   default=legal.models.site_region, editable=False,
                                   max_length=2),
        ),
        migrations.AddField(
            model_name='legaldocument',
            name='provisional',
            field=models.BooleanField(
                db_default=True, default=True,
                help_text="Tick while this is not the lawyer's final text: signup shows a "
                          '"provisional text" notice beside it.'),
        ),
        migrations.RemoveConstraint(
            model_name='legaldocument',
            name='legal_document_kind_version',
        ),
        migrations.AddConstraint(
            model_name='legaldocument',
            constraint=models.UniqueConstraint(fields=('region', 'kind', 'version'),
                                               name='legal_document_region_kind_version'),
        ),
    ]
