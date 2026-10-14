"""Story 2.12: the KMS key provider. One test (or group) per row of the plan's I/O matrix,
plus "a plain key never reaches a log, an error or a repr".

The KMS is a fake (`KMSClient.from_settings` returns it), so no network is used. The
wrapped and plain keys here are fixed test values, never real ones.
"""
import base64
import hashlib
import hmac
import io
import logging
import runpy
import threading
from pathlib import Path

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.core.checks import run_checks
from django.core.management import CommandError, call_command

from accounts import crypto
from accounts.management.commands.reencrypt_fields import values_not_on_key
from conftest import TEST_BLIND_INDEX_KEY, TEST_FIELD_ENCRYPTION_KEYS
from mytp_publicsite import kms

User = get_user_model()
PASSWORD = 'violet-orbit-canyon-42'
SERVICE_KEY_ID = '0d9e8f7a-2222-4c6b-8a5d-112233445566'
KMS_PROVIDER = 'accounts.crypto.KMSKeyProvider'

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


# --- Matrix: encrypt, decrypt an older key, transition, unknown id ---------------------------

def test_encrypt_uses_the_kms_key_and_unwraps_once(kms_keys):
    first = crypto.encrypt('Ana', 'accounts.user.first_name')
    second = crypto.encrypt('Bea', 'accounts.user.first_name')

    assert key_id_of_value(first) == key_id_of_value(second) == 'k1'
    assert crypto.decrypt(first, 'accounts.user.first_name') == 'Ana'
    assert kms_keys.unwraps == [(SERVICE_KEY_ID, WRAPPED['W-k1'])]
    assert isinstance(crypto.get_key_provider(), crypto.KMSKeyProvider)


def test_building_the_provider_calls_no_kms(kms_keys):
    crypto.get_key_provider()
    assert kms_keys.unwraps == []


def test_decrypt_value_on_an_older_kms_key(kms_keys, settings):
    stored = crypto.encrypt('Ana', 'a.b.c')
    settings.FIELD_KMS_KEYS = f'k2:{WRAPPED["W-k2"]},k1:{WRAPPED["W-k1"]}'

    assert kms_keys.unwraps == [(SERVICE_KEY_ID, WRAPPED['W-k1'])]
    kms_keys.unwraps.clear()
    assert crypto.encrypt('x', 'a.b.c').startswith('enc:v1:k2:')
    assert kms_keys.unwrapped == [WRAPPED['W-k2']]
    assert crypto.decrypt(stored, 'a.b.c') == 'Ana'  # k1 unwrapped on first use
    assert crypto.decrypt(stored, 'a.b.c') == 'Ana'
    assert kms_keys.unwrapped == [WRAPPED['W-k2'], WRAPPED['W-k1']]


def test_transition_from_plain_keys(kms_keys, settings):
    settings.FIELD_KEY_PROVIDER = 'accounts.crypto.EnvKeyProvider'
    settings.FIELD_ENCRYPTION_KEYS = TEST_FIELD_ENCRYPTION_KEYS  # env key test1
    stored = crypto.encrypt('Ana', 'a.b.c')
    assert key_id_of_value(stored) == 'test1'

    settings.FIELD_KEY_PROVIDER = KMS_PROVIDER
    settings.FIELD_KMS_KEYS = f'k2:{WRAPPED["W-k2"]}'

    assert crypto.decrypt(stored, 'a.b.c') == 'Ana'  # the plain key decrypts
    assert kms_keys.unwraps == []
    assert key_id_of_value(crypto.encrypt('x', 'a.b.c')) == 'k2'  # a KMS key is current


def test_transition_key_id_also_in_kms_list_is_refused(kms_keys, settings):
    settings.FIELD_ENCRYPTION_KEYS = f'k1:{TEST_FIELD_ENCRYPTION_KEYS.split(":", 1)[1]}'

    with pytest.raises(crypto.KeyConfigurationError) as error:
        crypto.get_key_provider()
    assert 'FIELD_ENCRYPTION_KEYS' in str(error.value) and "'k1'" in str(error.value)
    assert_no_secrets(error.value)


def test_unknown_key_id(kms_keys, settings):
    settings.FIELD_KEY_PROVIDER = 'accounts.crypto.EnvKeyProvider'
    settings.FIELD_ENCRYPTION_KEYS = TEST_FIELD_ENCRYPTION_KEYS
    stored = crypto.encrypt('Ana García', 'a.b.c')
    settings.FIELD_KEY_PROVIDER = KMS_PROVIDER
    settings.FIELD_ENCRYPTION_KEYS = ''

    with pytest.raises(crypto.DecryptionError) as error:
        crypto.decrypt(stored, 'a.b.c')
    message = str(error.value)
    assert "'test1'" in message and 'FIELD_KMS_KEYS' in message
    assert 'Ana' not in message and stored.rsplit(':', 1)[1] not in message
    assert kms_keys.unwraps == []


# --- Matrix: KMS down ------------------------------------------------------------------------

def test_kms_failure_names_step_and_key_and_is_retried(kms_keys):
    kms_keys.fail = kms.KMSError('connect', 'timed out')

    with pytest.raises(crypto.KeyUnavailableError) as error:
        crypto.encrypt('Ana', 'a.b.c')
    message = str(error.value)
    assert 'step connect failed' in message and "'k1'" in message
    assert error.value.__cause__ is None and error.value.__suppress_context__
    assert_no_secrets(message)

    with pytest.raises(crypto.KeyUnavailableError):  # not cached: called again
        crypto.encrypt('Ana', 'a.b.c')
    assert len(kms_keys.unwraps) == 2

    kms_keys.fail = None
    assert key_id_of_value(crypto.encrypt('Ana', 'a.b.c')) == 'k1'
    assert len(kms_keys.unwraps) == 3


def test_kms_answer_of_the_wrong_size_is_refused(kms_keys, monkeypatch):
    monkeypatch.setitem(PLAIN, 'W-k1', b'short')

    with pytest.raises(crypto.KeyUnavailableError) as error:
        crypto.encrypt('Ana', 'a.b.c')
    assert "'k1'" in str(error.value)


def test_kms_settings_missing_is_a_kms_error_naming_the_setting(settings):
    """Without the fake: KMSClient.from_settings itself refuses, before any network call."""
    settings.FIELD_KEY_PROVIDER = KMS_PROVIDER
    settings.FIELD_KMS_KEYS = f'k1:{WRAPPED["W-k1"]}'
    settings.KMS_ENDPOINT = ''

    with pytest.raises(crypto.KeyUnavailableError) as error:
        crypto.encrypt('Ana', 'a.b.c')
    assert 'step settings failed' in str(error.value) and 'KMS_ENDPOINT' in str(error.value)
    assert_no_secrets(error.value)


def test_concurrent_first_use_unwraps_once(kms_keys):
    provider = crypto.get_key_provider()
    gate = threading.Barrier(8)
    results = []

    def use():
        gate.wait()
        results.append(provider.current_key())

    threads = [threading.Thread(target=use) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 8 and len(set(results)) == 1
    assert len(kms_keys.unwraps) == 1


# --- Matrix: bad settings ------------------------------------------------------------------

def _key_errors():
    return [e for e in run_checks() if e.id in ('accounts.E001', 'accounts.E002')]


def test_valid_kms_settings_pass_the_check_without_calling_the_kms(kms_keys):
    assert _key_errors() == []
    assert kms_keys.unwraps == []


@pytest.mark.parametrize('setting, value, error_id', [
    ('FIELD_KMS_KEYS', '', 'accounts.E001'),
    ('FIELD_KMS_KEYS', 'no-colon-here', 'accounts.E001'),
    ('FIELD_KMS_KEYS', f'bad id:{WRAPPED["W-k1"]}', 'accounts.E001'),
    ('FIELD_KMS_KEYS', 'k1:', 'accounts.E001'),
    ('FIELD_KMS_KEYS', f'k1:{WRAPPED["W-k1"]} {WRAPPED["W-k2"]}', 'accounts.E001'),
    ('FIELD_KMS_KEYS', f'k1:{WRAPPED["W-k1"]},k1:{WRAPPED["W-k2"]}', 'accounts.E001'),
    ('FIELD_KMS_KEYS', f'k1:{WRAPPED["W-k1"]},', 'accounts.E001'),
    ('FIELD_ENCRYPTION_KEYS', 'k0:not base64!', 'accounts.E001'),
    ('BLIND_INDEX_KMS_KEY', '', 'accounts.E002'),
    ('BLIND_INDEX_KMS_KEY', f'{WRAPPED["W-bi"]} x', 'accounts.E002'),
    ('BLIND_INDEX_KMS_KEY', WRAPPED['W-k1'], 'accounts.E002'),
])
def test_malformed_kms_setting_is_a_check_error(kms_keys, settings, setting, value, error_id):
    setattr(settings, setting, value)

    errors = _key_errors()

    assert [e.id for e in errors] == [error_id]
    assert setting in errors[0].msg
    assert_no_secrets(errors[0].msg)
    assert kms_keys.unwraps == []


def test_malformed_setting_raises_key_configuration_error(kms_keys, settings):
    settings.FIELD_KMS_KEYS = 'k1'
    with pytest.raises(crypto.KeyConfigurationError) as error:
        crypto.encrypt('Ana', 'a.b.c')
    assert 'FIELD_KMS_KEYS' in str(error.value)
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


# --- Matrix: blind index through the KMS -----------------------------------------------------

def test_blind_index_uses_the_unwrapped_kms_key_once(kms_keys):
    expected = hmac.new(PLAIN['W-bi'], b'ana@example.com', hashlib.sha256).hexdigest()

    assert crypto.email_index('ANA@example.com ') == expected
    assert crypto.email_index('ana@example.com') == expected
    crypto.phone_index('+34 600 000 000')
    assert kms_keys.unwrapped == [WRAPPED['W-bi']]


def test_blind_index_kms_failure_is_retried(kms_keys):
    kms_keys.fail = kms.KMSError('auth', 'HTTP 403 (unwrap)', 403)
    with pytest.raises(crypto.KeyUnavailableError) as error:
        crypto.email_index('ana@example.com')
    assert 'BLIND_INDEX_KMS_KEY' in str(error.value) and 'step auth failed' in str(error.value)
    assert_no_secrets(error.value)

    kms_keys.fail = None
    crypto.email_index('ana@example.com')
    assert len(kms_keys.unwraps) == 2


def test_kms_provider_ignores_the_plain_blind_index_key(kms_keys, settings):
    settings.BLIND_INDEX_KEY = TEST_BLIND_INDEX_KEY
    expected = hmac.new(PLAIN['W-bi'], b'ana@example.com', hashlib.sha256).hexdigest()
    assert crypto.email_index('ana@example.com') == expected


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
    settings.FIELD_KEY_PROVIDER = 'accounts.crypto.EnvKeyProvider'
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


# --- Matrix: generate ----------------------------------------------------------------------

def test_generate_prints_only_the_entry(fake_kms, settings):
    settings.FIELD_KMS_KEYS = ''
    out, err = io.StringIO(), io.StringIO()
    call_command('generate_kms_field_key', 'k2', stdout=out, stderr=err)

    assert out.getvalue() == f'k2:{WRAPPED["W-new"]}\n'
    assert err.getvalue() == ''
    assert fake_kms.generated == [(SERVICE_KEY_ID, 'mytp-field-k2', 256)]
    assert_no_secrets(out.getvalue(), wrapped_too=False)
    assert crypto.parse_wrapped_key_list(out.getvalue().strip()) == [('k2', WRAPPED['W-new'])]


def test_generate_blind_index_prints_the_wrapped_value_only(fake_kms):
    out = io.StringIO()
    call_command('generate_kms_field_key', '--blind-index', stdout=out)

    assert out.getvalue() == f'{WRAPPED["W-new"]}\n'
    assert fake_kms.generated == [(SERVICE_KEY_ID, 'mytp-blind-index', 256)]


@pytest.mark.parametrize('args, message', [
    ([], 'Give the new key id'),
    (['bad id'], 'The key id may hold'),
    (['k1'], 'already in FIELD_KMS_KEYS'),
    (['test1'], 'already in FIELD_KMS_KEYS or FIELD_ENCRYPTION_KEYS'),
    (['k9', '--blind-index'], 'takes no key id'),
])
def test_generate_refuses_bad_arguments(kms_keys, settings, args, message):
    settings.FIELD_ENCRYPTION_KEYS = TEST_FIELD_ENCRYPTION_KEYS
    with pytest.raises(CommandError, match=message):
        call_command('generate_kms_field_key', *args, stdout=io.StringIO())
    assert kms_keys.generated == []


def test_generate_kms_failure_is_safe(fake_kms):
    fake_kms.fail = kms.KMSError('auth', 'HTTP 403 (generate)', 403)
    out = io.StringIO()
    with pytest.raises(CommandError) as error:
        call_command('generate_kms_field_key', 'k2', stdout=out)
    assert 'step auth failed' in str(error.value)
    assert error.value.__cause__ is None
    assert out.getvalue() == ''


def test_generate_refuses_a_wrong_size_key(fake_kms):
    fake_kms.next_plain = b'x' * 16
    out = io.StringIO()
    with pytest.raises(CommandError, match='16-byte'):
        call_command('generate_kms_field_key', 'k2', stdout=out)
    assert out.getvalue() == ''


# --- A plain key never reaches a log, an error or a repr -------------------------------------

@pytest.mark.django_db
def test_plain_key_never_in_logs_or_repr(kms_keys, settings, caplog):
    caplog.set_level(logging.DEBUG)
    settings.FIELD_KMS_KEYS = f'k2:{WRAPPED["W-k2"]},k1:{WRAPPED["W-k1"]}'
    User.objects.create_user('ana@example.com', PASSWORD, first_name='Ana')
    User.objects.by_email('ana@example.com').get()
    provider = crypto.get_key_provider()
    provider.get_key('k1')
    kms_keys.fail = kms.KMSError('unwrap', 'HTTP 400', 400)
    settings.FIELD_KMS_KEYS = f'k3:{WRAPPED["W-k2"]}'
    with pytest.raises(crypto.KeyUnavailableError) as error:
        crypto.encrypt('x', 'a.b.c')

    texts = [repr(provider), str(provider), repr(crypto.get_key_provider()), str(error.value),
             caplog.text, *(record.getMessage() for record in caplog.records)]
    assert_no_secrets(*texts)
    assert 'k1' in repr(provider)


# --- Settings and examples -----------------------------------------------------------------

def _env_values(path):
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if line.strip() and not line.startswith('#'))


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
