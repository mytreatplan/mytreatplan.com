"""Products in full (epic-catalogue entry 5, mytp-core 0.20.0): a product's public texts
per language, code and validity dates, and a subscription's TPS in-house discount (0 for
the rows already here).

The rows already here (the last snapshot of an admin site before entry 5) get the same
mapping as the admin site's own migration and as catalogue.applier: `tps` becomes
`tps_branded`, `other` becomes `uncategorised`, and the name becomes the English text.
The next snapshot replaces every row anyway. The new columns have database defaults, so
the previous release can still apply a snapshot while this one is being deployed.

The applied snapshot version is cleared (`integration.ConfigState.version` to None): when
this migration was written, that was how the first pull after the deploy applied the
admin site's current snapshot again (the admin site is deployed first; a catalogue write
there between the two deploys publishes the new category codes, and the previous release
applies that snapshot and skips every product of a category it does not know). It is no
longer the mechanism and no later migration does it: every release applies the current
snapshot again at its first pull, because the state row records which release applied
it (integration.config, `ConfigState.applied_release`). The step stays as it was applied;
on a new database it clears nothing. Nothing is done on the way back."""
from decimal import Decimal

from django.db import migrations, models

CATEGORIES = {'tps': 'tps_branded', 'other': 'uncategorised'}


def forwards(apps, schema_editor):
    Product = apps.get_model('catalogue', 'Product')
    products = Product.objects.using(schema_editor.connection.alias)
    for product in products.all():
        products.filter(pk=product.pk).update(
            category=CATEGORIES.get(product.category, product.category),
            texts={'en': {'name': product.name, 'description': ''}})
    # Kept as applied; no longer the mechanism (see the module docstring).
    ConfigState = apps.get_model('integration', 'ConfigState')
    ConfigState.objects.using(schema_editor.connection.alias).update(version=None)


def backwards(apps, schema_editor):
    """Back to the two former categories: only TPS branded was plain TPS."""
    Product = apps.get_model('catalogue', 'Product')
    products = Product.objects.using(schema_editor.connection.alias)
    products.exclude(category='tps_branded').update(category='other')
    products.filter(category='tps_branded').update(category='tps')


class Migration(migrations.Migration):

    dependencies = [
        ('catalogue', '0001_initial'),
        # ConfigState, whose applied version `forwards` clears.
        ('integration', '0004_config_snapshot'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='code',
            field=models.CharField(max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='texts',
            field=models.JSONField(db_default=models.Value({}, models.JSONField()), default=dict),
        ),
        migrations.AddField(
            model_name='product',
            name='valid_from',
            field=models.DateField(null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='valid_to',
            field=models.DateField(null=True),
        ),
        migrations.AddField(
            model_name='subscription',
            name='tps_in_house_discount_percent',
            field=models.DecimalField(db_default=Decimal('0.00'), decimal_places=2, default=Decimal('0.00'), max_digits=5),
        ),
        migrations.RunPython(forwards, backwards),
    ]
