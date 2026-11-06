"""The outbox payload is stored encrypted with the portal's field keys (epic-admin-interface
entry 8, decision 2026-11-06 after review): a client payload holds email, names and
mobile. Existing rows are encrypted in place (and decrypted back on reverse)."""
import mytp_core.crypto
from django.db import migrations, models


def encrypt_rows(apps, schema_editor):
    OutboxEvent = apps.get_model('integration', 'OutboxEvent')
    db = schema_editor.connection.alias
    for event in OutboxEvent.objects.using(db).order_by('pk').iterator(chunk_size=500):
        event.payload = event.payload_plain
        event.save(update_fields=['payload'])


def decrypt_rows(apps, schema_editor):
    OutboxEvent = apps.get_model('integration', 'OutboxEvent')
    db = schema_editor.connection.alias
    for event in OutboxEvent.objects.using(db).order_by('pk').iterator(chunk_size=500):
        event.payload_plain = event.payload
        event.save(update_fields=['payload_plain'])


class Migration(migrations.Migration):

    dependencies = [
        ('integration', '0004_config_snapshot'),
    ]

    operations = [
        migrations.RenameField(model_name='outboxevent', old_name='payload',
                               new_name='payload_plain'),
        # Nullable while both exist, so the reverse can add it back before filling it.
        migrations.AlterField(model_name='outboxevent', name='payload_plain',
                              field=models.JSONField(null=True)),
        migrations.AddField(
            model_name='outboxevent', name='payload',
            field=mytp_core.crypto.EncryptedJSONField(null=True),
        ),
        migrations.RunPython(encrypt_rows, decrypt_rows),
        migrations.RemoveField(model_name='outboxevent', name='payload_plain'),
        migrations.AlterField(
            model_name='outboxevent', name='payload',
            field=mytp_core.crypto.EncryptedJSONField(),
        ),
    ]
