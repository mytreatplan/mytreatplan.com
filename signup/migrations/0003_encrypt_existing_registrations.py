# Story 2.2: encrypt the existing pending registrations' email in place and set
# email_index. Reversible: going back decrypts them (the keys must still be configured).

from django.db import migrations

from accounts.crypto import encrypt_existing_rows

forward, backward = encrypt_existing_rows(
    'signup', 'pendingregistration', ['email'],
    index_field='email_index', index_source='email')


class Migration(migrations.Migration):

    dependencies = [
        ('signup', '0002_encrypted_email'),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
