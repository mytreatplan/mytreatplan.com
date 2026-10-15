"""Application-level encryption of personal fields, and the blind index (signup story 2.2).

Encryption policy: personal fields are encrypted in the app with AES-256-GCM before they
reach PostgreSQL; email and phone are found through a blind index (HMAC-SHA256 with its
own key); no encryption happens in SQL.

Stored text: ``enc:v1:<key_id>:<base64(nonce | ciphertext | tag)>``. The nonce is 96 random
bits per value. The associated data is ``<app_label>.<model>.<field>``, so a value copied
into another field (or another model) fails to decrypt.

Keys come from a key provider, chosen by the setting FIELD_KEY_PROVIDER (a dotted path to
a class with ``current_key() -> (key_id, key)`` and ``get_key(key_id) -> key``; the class
also supplies the blind index key). ``EnvKeyProvider`` (development, CI) reads the plain
keys FIELD_ENCRYPTION_KEYS and BLIND_INDEX_KEY. ``KMSKeyProvider`` (story 2.12; staging,
production) reads FIELD_KMS_KEYS and BLIND_INDEX_KMS_KEY, data keys wrapped by the OVHcloud
KMS service key KMS_SERVICE_KEY_ID, and unwraps each through the KMS once per process,
keeping the plain key in memory only.

No message raised from here ever contains a key, a plaintext or a ciphertext.
"""
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import threading
from functools import cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.core.serializers.json import DjangoJSONEncoder
from django.core.signals import setting_changed
from django.db import models
from django.dispatch import receiver
from django.utils.module_loading import import_string

from mytp_publicsite import kms

FORMAT_PREFIX = 'enc'
FORMAT_VERSION = 'v1'
KEY_SIZE = 32  # AES-256 / HMAC-SHA256 key, bytes
NONCE_SIZE = 12  # 96 bits, the GCM standard
KEY_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,32}$')

DEFAULT_KEY_PROVIDER = 'accounts.crypto.EnvKeyProvider'
KMS_PROVIDER = 'accounts.crypto.KMSKeyProvider'
KEY_SETTINGS = ('FIELD_KEY_PROVIDER', 'FIELD_ENCRYPTION_KEYS', 'BLIND_INDEX_KEY',
                'FIELD_KMS_KEYS', 'BLIND_INDEX_KMS_KEY', *kms.REQUIRED_SETTINGS, 'KMS_CA_FILE')
# A KMS-wrapped data key is the KMS's opaque string: anything printable without spaces
# or commas (the list separator).
WRAPPED_KEY_RE = re.compile(r'^[!-+\--~]{1,4096}$')


class KeyConfigurationError(ImproperlyConfigured):
    """A key setting is missing or malformed. The message names the setting, never a value."""


class KeyUnavailableError(Exception):
    """A KMS-wrapped key could not be unwrapped (KMS unreachable, refused, ...). Names the
    setting, the key id and the failed KMS step, never a key. Not cached: the next use
    retries."""


class DecryptionError(Exception):
    """A stored value could not be decrypted (unknown key id, tampered, wrong field, not
    encrypted). Never carries the plaintext or the ciphertext."""


class EncryptedFieldLookupError(FieldError):
    """A query filtered by an encrypted field (only `isnull` is meaningful)."""


# --- Keys ---------------------------------------------------------------------------------

def decode_key(text, setting, what='the key'):
    """A base64 32-byte key, or KeyConfigurationError naming `setting`."""
    try:
        key = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise KeyConfigurationError(
            f'{setting}: {what} is not valid base64.') from None
    if len(key) != KEY_SIZE:
        raise KeyConfigurationError(
            f'{setting}: {what} must be {KEY_SIZE} bytes (base64), not {len(key)}.')
    return key


def parse_key_list(value, setting='FIELD_ENCRYPTION_KEYS'):
    """Parse ``<key_id>:<base64 key>,...`` into an ordered list of (key_id, key)."""
    return [(key_id, decode_key(encoded, setting, f'the key of entry {position}'))
            for position, key_id, encoded in _split_key_list(
                value, setting, 'base64 key', 'generate_field_keys')]


def parse_wrapped_key_list(value, setting='FIELD_KMS_KEYS'):
    """Parse ``<key_id>:<KMS-wrapped key>,...`` into an ordered list of (key_id, wrapped).
    The wrapped keys are opaque here; only the KMS can unwrap them."""
    keys = []
    for position, key_id, wrapped in _split_key_list(
            value, setting, 'wrapped key', 'generate_kms_field_key'):
        keys.append((key_id, check_wrapped_key(wrapped, setting,
                                               f'the wrapped key of entry {position}')))
    return keys


def check_wrapped_key(text, setting, what='the wrapped key'):
    """A KMS-wrapped key as stored in a setting, or KeyConfigurationError naming `setting`."""
    text = (text or '').strip()
    if not WRAPPED_KEY_RE.match(text):
        raise KeyConfigurationError(
            f'{setting}: {what} is empty or not a KMS-wrapped key (printable, no spaces '
            f'or commas).')
    return text


def _split_key_list(value, setting, what, command):
    """(position, key_id, value text) for each ``<key_id>:<value>`` entry, ids checked."""
    if not (value or '').strip():
        raise KeyConfigurationError(
            f'{setting} is empty. Generate keys with `manage.py {command}` '
            f'and set {setting}.')
    entries = []
    seen = set()
    for position, entry in enumerate(value.split(','), start=1):
        entry = entry.strip()
        if not entry:
            raise KeyConfigurationError(f'{setting}: entry {position} is empty.')
        key_id, sep, encoded = entry.partition(':')
        if not sep:
            raise KeyConfigurationError(
                f'{setting}: entry {position} is not in the form <key_id>:<{what}>.')
        if not KEY_ID_RE.match(key_id):
            raise KeyConfigurationError(
                f'{setting}: entry {position} has an invalid key id (letters, digits, '
                f'"-" and "_", at most 32 characters).')
        if key_id in seen:
            raise KeyConfigurationError(f'{setting}: key id {key_id!r} appears twice.')
        seen.add(key_id)
        entries.append((position, key_id, encoded))
    return entries


def unwrap_data_key(setting, key_id, wrapped):
    """The plain 32-byte key of a data key wrapped by the KMS service key
    KMS_SERVICE_KEY_ID. KeyUnavailableError (setting, key id and KMS step only) if the KMS
    fails; the KMS client's own exception is not chained."""
    try:
        client = kms.KMSClient.from_settings(settings)
        key = client.decrypt_data_key(settings.KMS_SERVICE_KEY_ID.strip(), wrapped)
    except kms.KMSError as error:
        raise KeyUnavailableError(
            f'{setting}: cannot unwrap key {key_id!r} through the KMS: {error}') from None
    if len(key) != KEY_SIZE:
        raise KeyUnavailableError(
            f'{setting}: key {key_id!r} unwrapped to {len(key)} bytes, not {KEY_SIZE}.')
    return key


class KeyProvider:
    """Supplies the field encryption keys. The first key encrypts; every key decrypts.

    The class also supplies the blind index key (class methods, so a broken encryption
    key list does not hide a broken blind index key in the checks). By default it is the
    plain BLIND_INDEX_KEY."""

    blind_index_setting = 'BLIND_INDEX_KEY'

    @classmethod
    def check_blind_index_key(cls):
        """Parse the blind index setting without any network call; KeyConfigurationError
        naming it if it is missing or malformed. Returns the parsed value."""
        return cls.load_blind_index_key()

    @classmethod
    def load_blind_index_key(cls):
        """The 32-byte blind index key."""
        value = getattr(settings, 'BLIND_INDEX_KEY', '') or ''
        if not value.strip():
            raise KeyConfigurationError(
                'BLIND_INDEX_KEY is empty. Generate it with `manage.py generate_field_keys` '
                'and set BLIND_INDEX_KEY.')
        return decode_key(value, 'BLIND_INDEX_KEY')

    def check_keys(self):
        """Validate the key settings without any network call (system checks, startup)."""
        self.current_key()

    def current_key(self):
        """(key_id, 32-byte key) used to encrypt new values."""
        raise NotImplementedError

    def get_key(self, key_id):
        """The 32-byte key for `key_id`; DecryptionError if this provider does not have it."""
        raise NotImplementedError


class EnvKeyProvider(KeyProvider):
    """Keys from the setting FIELD_ENCRYPTION_KEYS: ``<key_id>:<base64 32-byte key>``,
    comma-separated. The first is current; the others only decrypt (rotation)."""

    setting = 'FIELD_ENCRYPTION_KEYS'

    def __init__(self):
        self._keys = parse_key_list(getattr(settings, self.setting, ''), self.setting)
        self._by_id = dict(self._keys)

    def __repr__(self):
        return f'<{type(self).__name__} key ids={[key_id for key_id, _ in self._keys]}>'

    def current_key(self):
        return self._keys[0]

    def get_key(self, key_id):
        try:
            return self._by_id[key_id]
        except KeyError:
            raise DecryptionError(
                f'Unknown field encryption key id {key_id!r}: it is not in '
                f'{self.setting}.') from None


class KMSKeyProvider(KeyProvider):
    """Keys wrapped by the OVHcloud KMS (envelope encryption, story 2.12).

    FIELD_KMS_KEYS: ``<key_id>:<wrapped key>``, comma-separated, as the KMS returned them
    (`manage.py generate_kms_field_key`); the first is current, the others only decrypt.
    Each is unwrapped with the service key KMS_SERVICE_KEY_ID on first use and kept in
    memory for the life of the process; a failed unwrap is not kept, so the next use
    retries. During a rotation from plain keys, FIELD_ENCRYPTION_KEYS may also be set:
    those keys only decrypt (a KMS key is always current). The blind index key is
    BLIND_INDEX_KMS_KEY, one wrapped key, unwrapped the same way.

    Building the provider parses the settings only; the KMS is called on first use.
    """

    setting = 'FIELD_KMS_KEYS'
    transition_setting = 'FIELD_ENCRYPTION_KEYS'
    blind_index_setting = 'BLIND_INDEX_KMS_KEY'
    blind_index_key_id = 'blind-index'

    def __init__(self):
        self._wrapped = parse_wrapped_key_list(getattr(settings, self.setting, ''), self.setting)
        self._wrapped_by_id = dict(self._wrapped)
        transition = getattr(settings, self.transition_setting, '') or ''
        self._transition = (dict(parse_key_list(transition, self.transition_setting))
                            if transition.strip() else {})
        for key_id in self._transition:
            if key_id in self._wrapped_by_id:
                raise KeyConfigurationError(
                    f'{self.transition_setting}: key id {key_id!r} is also in {self.setting}.')
        self._plain = {}
        self._lock = threading.Lock()

    def __repr__(self):
        return (f'<{type(self).__name__} key ids={[key_id for key_id, _ in self._wrapped]} '
                f'transition key ids={list(self._transition)}>')

    def check_keys(self):
        """Parsed in __init__; the KMS itself is checked by `manage.py kmscheck`."""

    def current_key(self):
        key_id = self._wrapped[0][0]
        return key_id, self._unwrap(key_id)

    def get_key(self, key_id):
        if key_id in self._wrapped_by_id:
            return self._unwrap(key_id)
        if key_id in self._transition:
            return self._transition[key_id]
        raise DecryptionError(
            f'Unknown field encryption key id {key_id!r}: it is not in {self.setting} '
            f'or {self.transition_setting}.')

    def _unwrap(self, key_id):
        key = self._plain.get(key_id)
        if key is None:
            with self._lock:
                key = self._plain.get(key_id)
                if key is None:
                    key = unwrap_data_key(self.setting, key_id, self._wrapped_by_id[key_id])
                    self._plain[key_id] = key
        return key

    @classmethod
    def check_blind_index_key(cls):
        value = getattr(settings, cls.blind_index_setting, '') or ''
        if not value.strip():
            raise KeyConfigurationError(
                f'{cls.blind_index_setting} is empty. Generate it with `manage.py '
                f'generate_kms_field_key --blind-index` and set {cls.blind_index_setting}.')
        return check_wrapped_key(value, cls.blind_index_setting)

    @classmethod
    def load_blind_index_key(cls):
        return unwrap_data_key(cls.blind_index_setting, cls.blind_index_key_id,
                               cls.check_blind_index_key())


def get_key_provider_class():
    path = getattr(settings, 'FIELD_KEY_PROVIDER', '') or DEFAULT_KEY_PROVIDER
    try:
        return import_string(path)
    except ImportError:
        raise KeyConfigurationError(
            f'FIELD_KEY_PROVIDER: cannot import {path!r}.') from None


@cache
def get_key_provider():
    return get_key_provider_class()()


_blind_index_key = {}
_blind_index_lock = threading.Lock()


def get_blind_index_key():
    """The blind index key from the provider class, loaded once per process (a failure is
    not kept, so the next call retries)."""
    key = _blind_index_key.get('key')
    if key is None:
        with _blind_index_lock:
            key = _blind_index_key.get('key')
            if key is None:
                key = get_key_provider_class().load_blind_index_key()
                _blind_index_key['key'] = key
    return key


get_blind_index_key.cache_clear = _blind_index_key.clear


def validate_field_keys():
    """Check both key settings now, without calling the KMS; KeyConfigurationError (naming
    the setting) if either is bad."""
    get_key_provider.cache_clear()
    get_blind_index_key.cache_clear()
    get_key_provider().check_keys()
    get_key_provider_class().check_blind_index_key()


@receiver(setting_changed)
def _reset_keys(*, setting, **kwargs):
    if setting in KEY_SETTINGS:
        get_key_provider.cache_clear()
        get_blind_index_key.cache_clear()


# --- Encryption -----------------------------------------------------------------------------

def encrypt(plaintext, associated_data):
    """Encrypt a str with the provider's current key."""
    key_id, key = get_key_provider().current_key()
    nonce = os.urandom(NONCE_SIZE)
    sealed = AESGCM(key).encrypt(nonce, plaintext.encode(), associated_data.encode())
    token = base64.b64encode(nonce + sealed).decode('ascii')
    return f'{FORMAT_PREFIX}:{FORMAT_VERSION}:{key_id}:{token}'


def key_id_of(stored):
    """The key id of a stored value, or None if it is not in the encrypted format."""
    parts = stored.split(':', 3) if isinstance(stored, str) else []
    if len(parts) == 4 and parts[0] == FORMAT_PREFIX and parts[1] == FORMAT_VERSION:
        return parts[2]
    return None


def decrypt(stored, associated_data):
    """Decrypt a stored value; DecryptionError on any failure (never the stored text)."""
    where = f'Cannot decrypt {associated_data}'
    if not isinstance(stored, str) or not stored.startswith(f'{FORMAT_PREFIX}:'):
        raise DecryptionError(f'{where}: the stored value is not encrypted.')
    parts = stored.split(':', 3)
    if len(parts) != 4 or parts[1] != FORMAT_VERSION:
        raise DecryptionError(f'{where}: unknown format version.')
    _prefix, _version, key_id, token = parts
    key = get_key_provider().get_key(key_id)
    try:
        blob = base64.b64decode(token, validate=True)
    except (binascii.Error, ValueError):
        raise DecryptionError(f'{where}: the stored value is not valid base64.') from None
    if len(blob) < NONCE_SIZE + 16:
        raise DecryptionError(f'{where}: the stored value is truncated.')
    try:
        plaintext = AESGCM(key).decrypt(blob[:NONCE_SIZE], blob[NONCE_SIZE:],
                                        associated_data.encode())
    except InvalidTag:
        raise DecryptionError(
            f'{where} with key {key_id!r}: authentication failed (the value was altered, '
            f'belongs to another field, or the key is wrong).') from None
    return plaintext.decode()


# --- Blind index ----------------------------------------------------------------------------

def normalize_email_for_index(email):
    """Case-insensitive identity (2.1): surrounding spaces removed, all of it lowercased."""
    return (email or '').strip().lower()


def normalize_phone_for_index(phone):
    """``+`` followed by the digits only (signup entry 4)."""
    return '+' + re.sub(r'[^0-9]', '', phone or '')


def blind_index(normalized_value):
    """HMAC-SHA256(BLIND_INDEX_KEY, value), hex. Callers normalize first."""
    return hmac.new(get_blind_index_key(), normalized_value.encode(), hashlib.sha256).hexdigest()


def email_index(email):
    return blind_index(normalize_email_for_index(email))


def phone_index(phone):
    return blind_index(normalize_phone_for_index(phone))


BLIND_INDEX_LENGTH = 64  # hex SHA-256


# --- Model fields ---------------------------------------------------------------------------

class EncryptedFieldMixin:
    """Stores the value encrypted (a text column); reads it back decrypted.

    Validation (max_length, email format, ...) applies to the plaintext. The column is
    `text`, since ciphertext is longer than the plaintext. Querying by the field raises
    EncryptedFieldLookupError (except `isnull`): look it up by its blind index instead.
    """

    allowed_lookups = ('isnull',)

    def get_internal_type(self):
        return 'TextField'

    @property
    def associated_data(self):
        return f'{self.model._meta.label_lower}.{self.name}'

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        return decrypt(value, self.associated_data)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None:
            return None
        return encrypt(str(value), self.associated_data)

    def _refuse(self, name):
        raise EncryptedFieldLookupError(
            f'{self.model._meta.label}.{self.name} is encrypted and cannot be queried with '
            f'"{name}"; look it up by its blind index.')

    def get_lookup(self, lookup_name):
        if lookup_name in self.allowed_lookups:
            return super().get_lookup(lookup_name)
        self._refuse(lookup_name)

    def get_transform(self, lookup_name):
        self._refuse(lookup_name)


class EncryptedCharField(EncryptedFieldMixin, models.CharField):
    pass


class EncryptedEmailField(EncryptedFieldMixin, models.EmailField):
    pass


class EncryptedJSONField(EncryptedFieldMixin, models.TextField):
    """A JSON value (dict, list, str, number, bool), stored encrypted as one text value.

    The whole JSON document is encrypted, so the database cannot query inside it. SQL
    NULL (Python None) stays NULL, so clearing the field anonymizes the row.
    """

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        return json.loads(decrypt(value, self.associated_data))

    def get_prep_value(self, value):
        if value is None:
            return None
        return encrypt(json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True),
                       self.associated_data)

    def to_python(self, value):
        """JSON text (as value_to_string writes it, e.g. for loaddata) becomes its value."""
        if isinstance(value, str):
            return json.loads(value)
        return value

    def value_to_string(self, obj):
        return json.dumps(self.value_from_object(obj), cls=DjangoJSONEncoder, sort_keys=True)


def encrypted_fields(model):
    """The model's encrypted concrete fields."""
    return [f for f in model._meta.concrete_fields if isinstance(f, EncryptedFieldMixin)]


# --- System checks --------------------------------------------------------------------------

def check_field_keys(app_configs=None, **kwargs):
    """Refuse to run without valid keys: accounts.E001 (encryption keys / provider),
    accounts.E002 (blind index key). Parses the settings only: the KMS provider's keys are
    not unwrapped here (so `check` and `migrate` work without network); `kmscheck` does."""
    from django.core.checks import Error

    errors = []
    encryption_keys = []
    wrapped_keys = []
    get_key_provider.cache_clear()
    get_blind_index_key.cache_clear()
    try:
        provider = get_key_provider()
        provider.check_keys()
        if isinstance(provider, EnvKeyProvider):
            encryption_keys = [key for _id, key in provider._keys]
        elif isinstance(provider, KMSKeyProvider):
            encryption_keys = list(provider._transition.values())
            wrapped_keys = [wrapped for _id, wrapped in provider._wrapped]
    except KeyConfigurationError as error:
        errors.append(Error(str(error), hint='See deploy/README.md, step 12.',
                            id='accounts.E001'))
    try:
        provider_class = get_key_provider_class()
    except KeyConfigurationError:
        provider_class = KeyProvider  # already E001; still check BLIND_INDEX_KEY
    try:
        index_key = provider_class.check_blind_index_key()
    except KeyConfigurationError as error:
        errors.append(Error(str(error), hint='See deploy/README.md, step 12.',
                            id='accounts.E002'))
    else:
        if isinstance(index_key, bytes) and any(
                hmac.compare_digest(index_key, key) for key in encryption_keys):
            errors.append(Error(
                'BLIND_INDEX_KEY must be its own key, not one of FIELD_ENCRYPTION_KEYS.',
                hint='Generate a separate key with `manage.py generate_field_keys`.',
                id='accounts.E002'))
        elif isinstance(index_key, str) and index_key in wrapped_keys:
            errors.append(Error(
                'BLIND_INDEX_KMS_KEY must be its own key, not one of FIELD_KMS_KEYS.',
                hint='Generate a separate key with '
                     '`manage.py generate_kms_field_key --blind-index`.',
                id='accounts.E002'))
    return errors


# --- Data migrations ------------------------------------------------------------------------

def _rewrite_rows(schema_editor, model, fields, transform, index_field=None, index_source=None):
    """Rewrite `fields` of every row of `model` with transform(value, associated_data),
    in raw SQL (the historical model's fields would try to decrypt plaintext). With
    `index_field` (when encrypting), also set it to the email index of the plaintext
    `index_source`."""
    connection = schema_editor.connection
    quote = connection.ops.quote_name
    table = quote(model._meta.db_table)
    pk = model._meta.pk.column
    columns = [model._meta.get_field(name).column for name in fields]
    label = model._meta.label_lower
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {quote(pk)}, {", ".join(quote(c) for c in columns)} FROM {table}')
        rows = cursor.fetchall()
    assignments = [f'{quote(c)} = %s' for c in columns]
    if index_field:
        assignments.append(f'{quote(model._meta.get_field(index_field).column)} = %s')
    sql = f'UPDATE {table} SET {", ".join(assignments)} WHERE {quote(pk)} = %s'
    with connection.cursor() as cursor:
        for row_pk, *values in rows:
            new_values = [None if value is None else transform(value, f'{label}.{name}')
                          for name, value in zip(fields, values)]
            if index_field:  # forward only: `values` are the plaintexts
                new_values.append(email_index(dict(zip(fields, values))[index_source]))
            cursor.execute(sql, [*new_values, row_pk])


def encrypt_existing_rows(app_label, model_name, fields, index_field=None, index_source=None):
    """RunPython pair: encrypt plaintext rows in place (forward), decrypt them (reverse)."""
    def forward(apps, schema_editor):
        model = apps.get_model(app_label, model_name)
        _rewrite_rows(schema_editor, model, fields, encrypt, index_field, index_source)

    def backward(apps, schema_editor):
        model = apps.get_model(app_label, model_name)
        _rewrite_rows(schema_editor, model, fields, decrypt)

    return forward, backward
