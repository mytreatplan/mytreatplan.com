"""Story 2.12: the KMS key provider. One test (or group) per row of the plan's I/O matrix,
plus "a plain key never reaches a log, an error or a repr". The provider moved to mytp-core
(epic-platform entry 4) with its unit tests; these are the portal's integration tests (its
models, reencrypt_fields, wsgi.py, examples and runbook) and the provider paths.

The KMS is a fake (`KMSClient.from_settings` returns it), so no network is used. The
wrapped and plain keys here are fixed test values, never real ones.
"""
import base64
import io
import runpy
from pathlib import Path

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.core.checks import run_checks
from django.core.management import call_command

import mytp_core.crypto
import mytp_core.kms
from accounts import crypto
from accounts.management.commands.reencrypt_fields import values_not_on_key
from conftest import TEST_BLIND_INDEX_KEY, TEST_FIELD_ENCRYPTION_KEYS, _env_values
from mytp_publicsite import kms
from mytp_publicsite.checks import check_field_key_provider

User = get_user_model()
PASSWORD = 'violet-orbit-canyon-42'
SERVICE_KEY_ID = '0d9e8f7a-2222-4c6b-8a5d-112233445566'
KMS_PROVIDER = crypto.KMS_PROVIDER

PLAIN = {
    'W-k1': b'test-only-kms-plain-k1-32-bytes!',
    'W-k2': b'test-only-kms-plain-k2-32-bytes!',
    'W-bi': b'test-only-kms-plain-bi-32-bytes!',
    'W-new': b'test-only-kms-plain-new-32bytes!',
}
WRAPPED = {name: f'eyJ.test-only-wrapped-{name}-marker.Qx9' for name in PLAIN}
assert all(len(key) == 32 for key in PLAIN.values())
SECRETS = [text for key in PLAIN.values()
           for text in (key.decode(), base64.b64encode(key).decode(), key.hex())]
SECRETS += [TEST_FIELD_ENCRYPTION_KEYS.split(':', 1)[1], TEST_BLIND_INDEX_KEY]
WRAPPED_SECRETS = list(WRAPPED.values())


def assert_no_secrets(*texts, wrapped_too=True):
    for text in texts:
        for secret in SECRETS + (WRAPPED_SECRETS if wrapped_too else []):
            assert secret not in str(text)


class FakeKMSClient:
    """Unwraps the wrapped keys above; `fail` makes every call raise that KMSError."""

    def __init__(self):
        self.unwraps = []
        self.generated = []
        self.fail = None
        self.next_plain = PLAIN['W-new']

    def decrypt_data_key(self, key_id, wrapped):
        self.unwraps.append((key_id, wrapped))
        if self.fail:
            raise self.fail
        name = {v: k for k, v in WRAPPED.items()}.get(wrapped)
        if name is None:
            raise kms.KMSError('unwrap', 'HTTP 400', 400)
        return PLAIN[name]

    def generate_data_key(self, key_id, name, size=256):
        self.generated.append((key_id, name, size))
        if self.fail:
            raise self.fail
        return WRAPPED['W-new'], self.next_plain

    @property
    def unwrapped(self):
        return [wrapped for _id, wrapped in self.unwraps]


@pytest.fixture
def fake_kms(monkeypatch, settings):
    fake = FakeKMSClient()
    settings.KMS_SERVICE_KEY_ID = SERVICE_KEY_ID
    monkeypatch.setattr(kms.KMSClient, 'from_settings', classmethod(lambda cls, s=None: fake))
    return fake


@pytest.fixture
def kms_keys(settings, fake_kms):
    """The KMS provider with one field key (k1) and the blind index key, no transition keys."""
    settings.FIELD_KEY_PROVIDER = KMS_PROVIDER
    settings.FIELD_KMS_KEYS = f'k1:{WRAPPED["W-k1"]}'
    settings.BLIND_INDEX_KMS_KEY = WRAPPED['W-bi']
    settings.FIELD_ENCRYPTION_KEYS = ''
    settings.BLIND_INDEX_KEY = ''
    return fake_kms


def key_id_of_value(stored):
    return crypto.key_id_of(stored)


# --- Matrix: bad settings ------------------------------------------------------------------

def _key_errors():
    return [e for e in run_checks() if e.id in ('mytp_core.E001', 'mytp_core.E002')]


def test_valid_kms_settings_pass_the_check_without_calling_the_kms(kms_keys):
    assert _key_errors() == []
    assert kms_keys.unwraps == []


WSGI_FILE = str(Path(__file__).resolve().parents[2] / 'mytreatplan' / 'wsgi.py')


def test_wsgi_starts_without_calling_the_kms(kms_keys):
    assert callable(runpy.run_path(WSGI_FILE)['application'])
    assert kms_keys.unwraps == []


def test_wsgi_refuses_a_malformed_kms_setting(kms_keys, settings):
    settings.BLIND_INDEX_KMS_KEY = ''
    with pytest.raises(crypto.KeyConfigurationError) as error:
        runpy.run_path(WSGI_FILE)
    assert 'BLIND_INDEX_KMS_KEY' in str(error.value)


# --- Matrix: rotation (database) -------------------------------------------------------------

def _all_on(key_id):
    from signup.models import PendingRegistration
    return all(values_not_on_key(model, crypto.encrypted_fields(model), key_id) == 0
               for model in (User, PendingRegistration))


@pytest.mark.django_db
def test_rotation_between_kms_keys(kms_keys, settings):
    ana = User.objects.create_user('ana@example.com', PASSWORD, first_name='Ana',
                                   phone='+34600000001')
    settings.FIELD_KMS_KEYS = f'k2:{WRAPPED["W-k2"]},k1:{WRAPPED["W-k1"]}'
    assert User.objects.get(pk=ana.pk).first_name == 'Ana'  # still readable

    out = io.StringIO()
    call_command('reencrypt_fields', stdout=out)

    assert _all_on('k2')
    assert 'accounts.user: 1 rows on key k2' in out.getvalue()
    assert_no_secrets(out.getvalue())
    settings.FIELD_KMS_KEYS = f'k2:{WRAPPED["W-k2"]}'  # the old key removed
    assert User.objects.by_email('ANA@example.com').get().first_name == 'Ana'
    assert User.objects.get(phone_index=crypto.phone_index('+34 600 000 001')) == ana
    assert authenticate(username='ana@example.com', password=PASSWORD) == ana


@pytest.mark.django_db
def test_staging_move_from_plain_keys_to_the_kms(kms_keys, settings):
    """The README's staging steps: plain keys -> KMS keys first, plain ones as transition
    keys -> restart -> reencrypt_fields -> plain keys removed."""
    settings.FIELD_KEY_PROVIDER = crypto.DEFAULT_KEY_PROVIDER
    settings.FIELD_ENCRYPTION_KEYS = TEST_FIELD_ENCRYPTION_KEYS
    settings.BLIND_INDEX_KEY = TEST_BLIND_INDEX_KEY
    ana = User.objects.create_user('ana@example.com', PASSWORD, first_name='Ana')

    settings.FIELD_KEY_PROVIDER = KMS_PROVIDER  # FIELD_KMS_KEYS and BLIND_INDEX_KMS_KEY set
    assert User.objects.get(pk=ana.pk).first_name == 'Ana'  # transition key decrypts
    assert not User.objects.by_email('ana@example.com').exists()  # accepted until re-encrypt

    call_command('reencrypt_fields', stdout=io.StringIO())

    settings.FIELD_ENCRYPTION_KEYS = ''
    settings.BLIND_INDEX_KEY = ''
    assert _all_on('k1')
    assert User.objects.by_email('ana@example.com').get() == ana
    assert authenticate(username='ana@example.com', password=PASSWORD) == ana


# --- Provider paths (mytp-core move) ----------------------------------------------------------

@pytest.mark.parametrize('path', ['accounts.crypto.KMSKeyProvider',
                                  'mytp_core.crypto.KMSKeyProvider'])
def test_both_provider_paths_are_the_kms_provider(kms_keys, settings, path):
    """Matrix: the live setting (accounts.crypto) and the new one (mytp_core.crypto) give the
    same provider; the startup check and the deploy check (E004) treat both as KMS."""
    settings.FIELD_KEY_PROVIDER = path
    settings.DEBUG = False

    assert crypto.get_key_provider_class() is mytp_core.crypto.KMSKeyProvider
    assert crypto.uses_kms_provider()
    assert isinstance(crypto.get_key_provider(), crypto.KMSKeyProvider)
    assert _key_errors() == []
    assert check_field_key_provider(None) == []
    stored = crypto.encrypt('Ana', 'accounts.user.first_name')
    assert crypto.key_id_of(stored) == 'k1'
    assert crypto.decrypt(stored, 'accounts.user.first_name') == 'Ana'


@pytest.mark.parametrize('path', ['', 'accounts.crypto.EnvKeyProvider',
                                  'mytp_core.crypto.EnvKeyProvider'])
def test_both_env_provider_paths_are_not_kms(settings, path):
    settings.FIELD_KEY_PROVIDER = path
    assert crypto.get_key_provider_class() is mytp_core.crypto.EnvKeyProvider
    assert not crypto.uses_kms_provider()


def test_migrations_import_the_package_classes():
    """Old migrations name accounts.crypto.*: the same classes as the package's."""
    for name in ('EncryptedCharField', 'EncryptedEmailField', 'EncryptedJSONField',
                 'encrypt_existing_rows'):
        assert getattr(crypto, name) is getattr(mytp_core.crypto, name)
    assert kms.KMSClient is mytp_core.kms.KMSClient


# --- Settings and examples -----------------------------------------------------------------

def test_kms_key_settings_default_empty_and_examples_commented(settings):
    base = settings.BASE_DIR
    text = (base / 'mytreatplan' / 'settings.py').read_text()
    for key in ('FIELD_KMS_KEYS', 'BLIND_INDEX_KMS_KEY'):
        assert f"{key} = config('{key}', default='')" in text
        for example in (base / 'settings.env.example', base / 'deploy' / 'staging.env.example'):
            assert f'# {key}=\n' in example.read_text()
            assert key not in _env_values(example)
    assert ("# FIELD_KEY_PROVIDER=accounts.crypto.KMSKeyProvider\n"
            in (base / 'deploy' / 'staging.env.example').read_text())


def test_runbook_covers_the_kms_provider(settings):
    text = (settings.BASE_DIR / 'deploy' / 'README.md').read_text()
    step = text.split('### 12. Field encryption keys', 1)[1].split('\n## ', 1)[0]
    for phrase in ('Move to the KMS provider', 'generate_kms_field_key', '--blind-index',
                   'reencrypt_fields', 'Retire the plain keys', 'mytp_publicsite.E004',
                   'Email and phone lookups', 'MyTreatplan staging field keys'):
        assert phrase in step
    keys = text.split('## Keys (KMS)', 1)[1].split('\n## ', 1)[0]
    for phrase in ('Rotate a field key', 'Disaster recovery rests on the service key',
                   'must never be deleted', 'makes all personal data and its backups\n'
                   '  unrecoverable', 'restrict key deletion', 'the wrapped values',
                   'wrapped only, never a\n  plain key', '30 days after a rotation'):
        assert phrase in keys
    assert 'until the last `backupdb` dump taken with them expires' in step
    assert 'can no longer be read (accepted on staging)' not in step
