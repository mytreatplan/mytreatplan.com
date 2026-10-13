# Signup step 1 (story 2.4): the personal and contact fields on the pending registration.
# Existing rows get blank values.

import accounts.crypto
import django_countries.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('signup', '0005_pendingregistration_attempt'),
    ]

    operations = [
        migrations.AddField(
            model_name='pendingregistration',
            name='country',
            field=django_countries.fields.CountryField(blank=True, max_length=2),
        ),
        migrations.AddField(
            model_name='pendingregistration',
            name='first_name',
            field=accounts.crypto.EncryptedCharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name='pendingregistration',
            name='how_found',
            field=models.CharField(blank=True, choices=[('search_engine', 'Search Engine (e.g. Google)'), ('social_media', 'Social Media (Instagram, Facebook)'), ('linkedin', 'LinkedIn'), ('whatsapp', 'WhatsApp'), ('email', 'E-mail'), ('phone_call', 'Telephone Call'), ('conference', 'Conference or Congress'), ('others', 'Others')], max_length=30),
        ),
        migrations.AddField(
            model_name='pendingregistration',
            name='how_found_other',
            field=accounts.crypto.EncryptedCharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='pendingregistration',
            name='last_name',
            field=accounts.crypto.EncryptedCharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name='pendingregistration',
            name='middle_name',
            field=accounts.crypto.EncryptedCharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name='pendingregistration',
            name='phone',
            field=accounts.crypto.EncryptedCharField(blank=True, max_length=16),
        ),
        migrations.AddField(
            model_name='pendingregistration',
            name='phone_index',
            field=models.CharField(blank=True, db_index=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name='pendingregistration',
            name='phone_region',
            field=models.CharField(blank=True, max_length=2),
        ),
        migrations.AddField(
            model_name='pendingregistration',
            name='title',
            field=models.CharField(blank=True, choices=[('dr', 'Dr.'), ('prof', 'Prof.'), ('mr', 'Mr'), ('mrs', 'Mrs'), ('ms', 'Ms'), ('eng', 'Eng.')], max_length=10),
        ),
    ]
