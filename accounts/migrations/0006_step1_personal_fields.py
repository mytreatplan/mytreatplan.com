# Signup step 1 (story 2.4): title, middle name, country, mobile (encrypted, E.164) with its
# blind index, and how-found-us. Existing rows get blank values.

import accounts.crypto
import django_countries.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_email_index_required'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='country',
            field=django_countries.fields.CountryField(blank=True, max_length=2, verbose_name='country / region'),
        ),
        migrations.AddField(
            model_name='user',
            name='how_found',
            field=models.CharField(blank=True, choices=[('search_engine', 'Search Engine (e.g. Google)'), ('social_media', 'Social Media (Instagram, Facebook)'), ('linkedin', 'LinkedIn'), ('whatsapp', 'WhatsApp'), ('email', 'E-mail'), ('phone_call', 'Telephone Call'), ('conference', 'Conference or Congress'), ('others', 'Others')], max_length=30, verbose_name='how they found out about us'),
        ),
        migrations.AddField(
            model_name='user',
            name='how_found_other',
            field=accounts.crypto.EncryptedCharField(blank=True, max_length=200, verbose_name='how they found out about us (others)'),
        ),
        migrations.AddField(
            model_name='user',
            name='middle_name',
            field=accounts.crypto.EncryptedCharField(blank=True, max_length=150, verbose_name='middle name or initial'),
        ),
        migrations.AddField(
            model_name='user',
            name='phone',
            field=accounts.crypto.EncryptedCharField(blank=True, max_length=16, verbose_name='mobile phone number'),
        ),
        migrations.AddField(
            model_name='user',
            name='phone_index',
            field=models.CharField(blank=True, db_index=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name='user',
            name='title',
            field=models.CharField(blank=True, choices=[('dr', 'Dr.'), ('prof', 'Prof.'), ('mr', 'Mr'), ('mrs', 'Mrs'), ('ms', 'Ms'), ('eng', 'Eng.')], max_length=10, verbose_name='title'),
        ),
    ]
