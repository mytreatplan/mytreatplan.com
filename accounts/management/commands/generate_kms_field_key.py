"""Print a new KMS-wrapped field key for a person to store (story 2.12).

    python manage.py generate_kms_field_key <key_id>      # prints <key_id>:<wrapped key>
    python manage.py generate_kms_field_key --blind-index  # prints the wrapped key only

Asks the OVHcloud KMS for a new 256-bit data key wrapped by the service key
KMS_SERVICE_KEY_ID. The plain key the KMS also returns is discarded at once: it is never
printed, logged or stored. Only the wrapped key is printed; it is useless without the
KMS service key, and goes in settings.env (FIELD_KMS_KEYS, or BLIND_INDEX_KMS_KEY with
--blind-index). See deploy/README.md, step 12.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.crypto import KEY_ID_RE, KEY_SIZE, KeyConfigurationError, check_wrapped_key
from mytp_publicsite import kms

DATA_KEY_BITS = KEY_SIZE * 8


def configured_key_ids():
    """Key ids already in FIELD_KMS_KEYS or FIELD_ENCRYPTION_KEYS (ids only, unparsed)."""
    ids = set()
    for name in ('FIELD_KMS_KEYS', 'FIELD_ENCRYPTION_KEYS'):
        for entry in (getattr(settings, name, '') or '').split(','):
            key_id, sep, _value = entry.strip().partition(':')
            if sep:
                ids.add(key_id)
    return ids


class Command(BaseCommand):
    help = ('Print a new FIELD_KMS_KEYS entry (<key_id>:<wrapped key>), or with --blind-index '
            'a new BLIND_INDEX_KMS_KEY value: a 256-bit data key wrapped by the KMS service '
            'key. The plain key is discarded; nothing is saved by this command.')
    # Runs before the KMS keys are configured, so no system checks.
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('key_id', nargs='?',
                            help='Id of the new field key (letters, digits, "-" and "_", at '
                                 'most 32 characters), e.g. kms20261014.')
        parser.add_argument('--blind-index', action='store_true',
                            help='Make the blind index key instead (BLIND_INDEX_KMS_KEY); '
                                 'prints the wrapped value only.')

    def handle(self, *args, key_id=None, blind_index=False, **options):
        if blind_index:
            if key_id:
                raise CommandError('--blind-index takes no key id.')
            name = 'mytp-blind-index'
        else:
            if not key_id:
                raise CommandError('Give the new key id, e.g. '
                                   '`manage.py generate_kms_field_key kms20261014`.')
            if not KEY_ID_RE.match(key_id):
                raise CommandError('The key id may hold letters, digits, "-" and "_" '
                                   '(at most 32 characters).')
            if key_id in configured_key_ids():
                raise CommandError(f'Key id {key_id!r} is already in FIELD_KMS_KEYS or '
                                   f'FIELD_ENCRYPTION_KEYS; choose another.')
            name = f'mytp-field-{key_id}'
        try:
            client = kms.KMSClient.from_settings(settings)
            wrapped, plaintext = client.generate_data_key(
                settings.KMS_SERVICE_KEY_ID.strip(), name, DATA_KEY_BITS)
        except kms.KMSError as exc:
            raise CommandError(f'generate_kms_field_key: {exc}') from None
        size = len(plaintext)
        del plaintext  # the plain key is never kept
        if size != KEY_SIZE:
            raise CommandError(f'generate_kms_field_key: the KMS returned a {size}-byte key, '
                               f'not {KEY_SIZE}.')
        try:
            wrapped = check_wrapped_key(wrapped, 'the KMS answer')
        except KeyConfigurationError:
            raise CommandError('generate_kms_field_key: the wrapped key the KMS returned '
                               'cannot be stored in a key list (spaces or commas).') from None
        self.stdout.write(wrapped if blind_index else f'{key_id}:{wrapped}')
