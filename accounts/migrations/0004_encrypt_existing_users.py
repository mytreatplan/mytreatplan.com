# Story 2.2: encrypt the existing users' email and names in place and set email_index.
# Reversible: going back decrypts them (the keys must still be configured).

from django.db import migrations

from accounts.crypto import encrypt_existing_rows

forward, backward = encrypt_existing_rows(
    'accounts', 'user', ['email', 'first_name', 'last_name'],
    index_field='email_index', index_source='email')


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_encrypted_personal_fields'),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
