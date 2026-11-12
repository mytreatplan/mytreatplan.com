"""Billing companies and billing rules (epic-catalogue entry 9, mytp-core 0.23.0): two new
read-only tables, filled by the snapshot apply.

A rule's `country_group` has no database constraint: the previous release replaces the
country groups without knowing these tables, and must still be able to apply a snapshot
while this one is being deployed (or after a rollback).

Nothing clears the applied snapshot version here: every release applies the admin site's
current snapshot again at its first pull (integration.config), so the first pull after the
deploy stores the companies and rules of the version the previous release had already
applied without them."""

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalogue', '0004_subscriptions_in_full'),
    ]

    operations = [
        migrations.CreateModel(
            name='BillingCompany',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('code', models.CharField(max_length=16)),
                ('legal_name', models.CharField(max_length=200)),
                ('address', models.TextField()),
                ('country', models.CharField(max_length=2)),
                ('time_zone', models.CharField(max_length=64)),
                ('company_number', models.CharField(default='', max_length=64)),
                ('vat_number', models.CharField(default='', max_length=64)),
                ('tax_number', models.CharField(default='', max_length=64)),
                ('active', models.BooleanField()),
            ],
            options={
                'ordering': ['code', 'id'],
            },
        ),
        migrations.CreateModel(
            name='BillingRule',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('category', models.CharField(max_length=32)),
                ('vat_percent', models.DecimalField(decimal_places=2, max_digits=5)),
                ('exemption_text', models.TextField(default='')),
                ('reverse_charge_text', models.TextField(default='')),
                ('gateways', models.JSONField(default=list)),
                ('currency', models.CharField(max_length=3)),
                ('available', models.BooleanField()),
                ('active', models.BooleanField()),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rules', to='catalogue.billingcompany')),
                ('country_group', models.ForeignKey(db_constraint=False, on_delete=django.db.models.deletion.CASCADE, related_name='billing_rules', to='catalogue.countrygroup')),
            ],
            options={
                'ordering': ['category', 'id'],
            },
        ),
    ]
