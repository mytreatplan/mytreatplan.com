"""Add-ons, quantities, calls and costs (epic-catalogue entry 6, mytp-core 0.21.0): the
add-ons table; a product's size, per-unit quantity range, calls and cost links; and the
provider products with their dated costs (the snapshot's `provider_costs` section, which
no release applied before).

The new product columns are nullable or have database defaults, so the previous release
can still apply a snapshot while this one is being deployed; it does not touch the new
tables.

The applied snapshot version is cleared (`integration.ConfigState.version` to None), as
migration 0002 did and for the same reason: the admin site is deployed first, so the
previous release may already have applied the admin site's current version without the
add-ons, the new product fields and the provider costs, and would answer "unchanged" for
it. The first pull after the deploy (at most 5 minutes: the `pullconfig` job) applies
that snapshot again, whatever its version; until then `show_config` shows no applied
version and the stored rows stay. Nothing is done on the way back.

Two exceptions, in which the previous release records the current version again without
the new parts and this release then answers "unchanged" for it:

- the previous release pulls between `migrate` and the switch to this release (the
  `pullconfig` timer and the worker still run the previous release then);
- a deploy that fails after `migrate` switches back to the previous release, which
  pulls, and the deploy is retried: the migration is already applied and does not run
  again.

So check after the deploy: `price_catalogue` (or the row counts of `catalogue_addon` and
`catalogue_providerproduct`) must show the add-ons and the provider costs. If it does
not, any write on the admin site publishes a new version, which this release applies in
full."""
import django.db.models.deletion
import uuid
from django.db import migrations, models


def forwards(apps, schema_editor):
    # Apply the current snapshot again on the first pull (see the module docstring).
    ConfigState = apps.get_model('integration', 'ConfigState')
    ConfigState.objects.using(schema_editor.connection.alias).update(version=None)


class Migration(migrations.Migration):

    dependencies = [
        ('catalogue', '0002_products_core'),
        # ConfigState, whose applied version `forwards` clears.
        ('integration', '0004_config_snapshot'),
    ]

    operations = [
        migrations.CreateModel(
            name='AddOn',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('code', models.CharField(max_length=64)),
                ('texts', models.JSONField(default=dict)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=17, null=True)),
                ('currency', models.CharField(default='', max_length=3)),
                ('percent', models.DecimalField(decimal_places=2, max_digits=5, null=True)),
                ('categories', models.JSONField(default=list)),
                ('sizes', models.JSONField(null=True)),
                ('active', models.BooleanField()),
            ],
            options={
                'ordering': ['code', 'id'],
            },
        ),
        migrations.CreateModel(
            name='ProviderProduct',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('code', models.CharField(max_length=64)),
                ('currency', models.CharField(max_length=3)),
                ('active', models.BooleanField()),
            ],
            options={
                'ordering': ['code', 'id'],
            },
        ),
        migrations.AddField(
            model_name='product',
            name='call_minutes',
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='calls',
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='cost_links',
            field=models.JSONField(db_default=models.Value([], models.JSONField()), default=list),
        ),
        migrations.AddField(
            model_name='product',
            name='max_quantity',
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='min_quantity',
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='size',
            field=models.CharField(db_default='', default='', max_length=32),
        ),
        migrations.CreateModel(
            name='ProviderCost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('valid_from', models.DateField()),
                ('valid_to', models.DateField(null=True)),
                ('face', models.DecimalField(decimal_places=2, max_digits=17)),
                ('discounted', models.DecimalField(decimal_places=2, max_digits=17)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='costs', to='catalogue.providerproduct')),
            ],
            options={
                'ordering': ['product', 'valid_from'],
            },
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
