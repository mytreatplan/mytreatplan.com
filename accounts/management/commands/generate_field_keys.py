"""Print new field keys for a person to store (story 2.2). Never stores or logs them."""
import base64
import os

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.crypto import KEY_ID_RE, KEY_SIZE


def new_key():
    return base64.b64encode(os.urandom(KEY_SIZE)).decode('ascii')


class Command(BaseCommand):
    help = ('Print a new FIELD_ENCRYPTION_KEYS entry and BLIND_INDEX_KEY (or, with --rotate, '
            'only a new encryption key entry). Store them in settings.env and the team '
            'password manager; nothing is saved by this command.')
    # Must run before any key is configured, so no system checks.
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--key-id', help='Id of the new encryption key (default: k<UTC date and time>, e.g. k20261013T0812).')
        parser.add_argument('--rotate', action='store_true',
                            help='Print only a new encryption key entry, to put first in '
                                 'FIELD_ENCRYPTION_KEYS before running reencrypt_fields.')

    def handle(self, *args, key_id=None, rotate=False, **options):
        key_id = key_id or timezone.now().strftime('k%Y%m%dT%H%M')
        if not KEY_ID_RE.match(key_id):
            raise CommandError('The key id may hold letters, digits, "-" and "_" '
                               '(at most 32 characters).')
        entry = f'{key_id}:{new_key()}'
        out = self.stdout.write
        out('# New field keys. Store them in /srv/mytp/shared/settings.env (or your local')
        out('# settings.env) and in the team password manager. Never commit, log or paste them')
        out('# in a chat or ticket. A lost key cannot be reissued: its data stays unreadable.')
        if rotate:
            out('# Put this entry FIRST in FIELD_ENCRYPTION_KEYS, keep the current entries after')
            out('# it, restart, then run `manage.py reencrypt_fields`.')
            out(entry)
        else:
            out(f'FIELD_ENCRYPTION_KEYS={entry}')
            out(f'BLIND_INDEX_KEY={new_key()}')
