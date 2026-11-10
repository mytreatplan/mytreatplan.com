"""Subscriptions in full (epic-catalogue entry 8, mytp-core 0.22.0): a subscription's
manual mark, code, public texts, linked products, discounts per product category, own
price and call extras per product category.

The new columns are nullable or have database defaults, so the previous release can still
apply a snapshot while this one is being deployed (it leaves them at their defaults: no
links and no map, which read as "every product" and "the two TPS discounts", as before).

Nothing clears the applied snapshot version here, unlike migrations 0002 and 0003: every
release applies the admin site's current snapshot again at its first pull (the state row
records which release applied it: integration.config, `ConfigState.applied_release`), so
the first pull after the deploy stores the new parts of the version the previous release
had already applied without them."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalogue', '0003_add_ons_quantities_calls_costs'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscription',
            name='call_extras',
            field=models.JSONField(db_default=models.Value({}, models.JSONField()), default=dict),
        ),
        migrations.AddField(
            model_name='subscription',
            name='code',
            field=models.CharField(max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='subscription',
            name='discounts',
            field=models.JSONField(null=True),
        ),
        migrations.AddField(
            model_name='subscription',
            name='manual',
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name='subscription',
            name='monthly_amount',
            field=models.DecimalField(decimal_places=2, max_digits=17, null=True),
        ),
        migrations.AddField(
            model_name='subscription',
            name='price_currency',
            field=models.CharField(db_default='', default='', max_length=3),
        ),
        migrations.AddField(
            model_name='subscription',
            name='products',
            field=models.JSONField(null=True),
        ),
        migrations.AddField(
            model_name='subscription',
            name='texts',
            field=models.JSONField(db_default=models.Value({}, models.JSONField()), default=dict),
        ),
        migrations.AddField(
            model_name='subscription',
            name='upfront_amount',
            field=models.DecimalField(decimal_places=2, max_digits=17, null=True),
        ),
    ]
