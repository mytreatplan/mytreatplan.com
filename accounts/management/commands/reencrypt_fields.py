"""Rewrite every encrypted value with the current key (story 2.2, key rotation).

Rotation: put a new key first in the provider's key list (FIELD_KMS_KEYS with the KMS
provider, FIELD_ENCRYPTION_KEYS with the environment one), keeping the old ones after it
(with the KMS provider, plain keys being retired may stay in FIELD_ENCRYPTION_KEYS, for
decryption only), restart, then run this. Afterwards no value uses an older key, which may
then be removed. Each row is saved through its model, so its blind indexes are recomputed
as well (with the current blind index key: BLIND_INDEX_KMS_KEY with the KMS provider,
BLIND_INDEX_KEY with the environment one). Prints counts and key ids only, never values
or keys.
"""
from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from accounts.crypto import encrypted_fields, get_key_provider, key_id_of


def values_not_on_key(model, fields, key_id):
    """How many stored values of `fields` are not encrypted with `key_id` (raw SQL)."""
    quote = connection.ops.quote_name
    columns = ', '.join(quote(field.column) for field in fields)
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {columns} FROM {quote(model._meta.db_table)}')
        return sum(1 for row in cursor.fetchall() for value in row
                   if value is not None and key_id_of(value) != key_id)


class Command(BaseCommand):
    help = 'Rewrite every encrypted field value with the current key (key rotation).'

    def handle(self, *args, **options):
        key_id, _key = get_key_provider().current_key()
        total = 0
        for model in apps.get_models():
            fields = encrypted_fields(model)
            if not fields:
                continue
            names = [field.name for field in fields]
            count = 0
            with transaction.atomic():
                rows = (model._default_manager.select_for_update()
                        .only(model._meta.pk.name, *names).order_by('pk'))
                for obj in rows.iterator(chunk_size=500):
                    obj.save(update_fields=names)
                    count += 1
            left = values_not_on_key(model, fields, key_id)
            if left:
                raise CommandError(f'{model._meta.label_lower}: {left} values are still not on '
                                   f'key {key_id!r}.')
            total += count
            self.stdout.write(f'reencrypt_fields: {model._meta.label_lower}: {count} rows on '
                              f'key {key_id}')
        self.stdout.write(f'reencrypt_fields: done, {total} rows on key {key_id}')
