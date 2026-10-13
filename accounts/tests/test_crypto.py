"""Story 2.2: encrypted personal fields, blind index, key provider, Argon2id, data migration.

One test (or group) per row of the plan's I/O matrix, plus "no key material in any output".
The keys here are fixed test values, never real ones.
"""
import base64
import io
import re
import runpy
from pathlib import Path

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.hashers import make_password
from django.core.checks import run_checks
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor

from accounts import crypto
from conftest import TEST_BLIND_INDEX_KEY, TEST_FIELD_ENCRYPTION_KEYS
from signup.forms import email_is_registered
from signup.models import PendingRegistration

User = get_user_model()
PASSWORD = 'violet-orbit-canyon-42'

ROTATED_KEY = base64.b64encode(b'test-only-rotated-key-not-secret').decode()
ROTATED_KEYS = f'test2:{ROTATED_KEY},{TEST_FIELD_ENCRYPTION_KEYS}'
KEY_MATERIAL = [TEST_FIELD_ENCRYPTION_KEYS.split(':', 1)[1], TEST_BLIND_INDEX_KEY, ROTATED_KEY,
                'test-only-field-key-not-secret', 'test-only-blind-index-key',
                'test-only-rotated-key']
ENC_RE = re.compile(r'^enc:v1:(?P<key_id>[\w-]+):[A-Za-z0-9+/=]+$')


def raw_row(table, pk, *columns):
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {", ".join(quote(c) for c in columns)} FROM {quote(table)} '
                       f'WHERE id = %s', [pk])
        return cursor.fetchone()


def assert_no_key_material(text):
    for secret in KEY_MATERIAL:
        assert secret not in text


# --- Encryption primitives ------------------------------------------------------------------

def test_round_trip_and_format():
    stored = crypto.encrypt('Ana García', 'accounts.user.first_name')

    match = ENC_RE.match(stored)
    assert match and match['key_id'] == 'test1'
    blob = base64.b64decode(stored.rsplit(':', 1)[1])
    assert len(blob) == 12 + len('Ana García'.encode()) + 16  # nonce | ciphertext | tag
    assert crypto.decrypt(stored, 'accounts.user.first_name') == 'Ana García'


def test_random_nonce_per_value():
    assert crypto.encrypt('ana@example.com', 'a.b.c') != crypto.encrypt('ana@example.com', 'a.b.c')


def test_value_copied_to_another_field_fails():
    stored = crypto.encrypt('Ana', 'accounts.user.first_name')
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(stored, 'accounts.user.last_name')


def test_tampered_value_fails():
    """Matrix: one byte changed -> decryption error."""
    stored = crypto.encrypt('ana@example.com', 'accounts.user.email')
    prefix, token = stored.rsplit(':', 1)
    blob = bytearray(base64.b64decode(token))
    blob[20] ^= 0x01
    tampered = f'{prefix}:{base64.b64encode(bytes(blob)).decode()}'

    with pytest.raises(crypto.DecryptionError) as error:
        crypto.decrypt(tampered, 'accounts.user.email')
    message = str(error.value)
    assert 'ana@example.com' not in message and token not in message and tampered not in message


def test_unknown_key_id_fails_clearly():
    """Matrix: a key id the provider does not have -> clear error, no plaintext/ciphertext."""
    stored = crypto.encrypt('ana@example.com', 'accounts.user.email')
    unknown = stored.replace(':test1:', ':gone:')

    with pytest.raises(crypto.DecryptionError) as error:
        crypto.decrypt(unknown, 'accounts.user.email')
    message = str(error.value)
    assert "'gone'" in message and 'FIELD_ENCRYPTION_KEYS' in message
    assert 'ana@example.com' not in message and unknown.rsplit(':', 1)[1] not in message
    assert_no_key_material(message)


@pytest.mark.parametrize('stored', ['ana@example.com', '', 'enc:v2:test1:AAAA', 'enc:v1:test1:!!',
                                    'enc:v1:test1:QUFB'])
def test_malformed_stored_value_fails(stored):
    """Never returns the stored text or an empty value."""
    with pytest.raises(crypto.DecryptionError) as error:
        crypto.decrypt(stored, 'accounts.user.email')
    assert 'ana@example.com' not in str(error.value)


# --- Blind index ----------------------------------------------------------------------------

def test_blind_index_is_hmac_sha256_hex_of_the_normalized_value():
    import hashlib
    import hmac

    key = base64.b64decode(TEST_BLIND_INDEX_KEY)
    expected = hmac.new(key, b'ana.garcia@example.com', hashlib.sha256).hexdigest()
    assert crypto.email_index(' Ana.Garcia@EXAMPLE.com ') == expected
    assert crypto.email_index('ana.garcia@example.com') == expected
    assert re.fullmatch(r'[0-9a-f]{64}', expected)


def test_blind_index_uses_its_own_key(settings):
    before = crypto.email_index('ana@example.com')
    settings.BLIND_INDEX_KEY = ROTATED_KEY
    assert crypto.email_index('ana@example.com') != before


@pytest.mark.parametrize('raw, normalized', [
    ('+34 600 11 22 33', '+34600112233'),
    ('(+34) 600-112-233', '+34600112233'),
    ('34600112233', '+34600112233'),
])
def test_phone_normalization(raw, normalized):
    assert crypto.normalize_phone_for_index(raw) == normalized
    assert crypto.phone_index(raw) == crypto.blind_index(normalized)


# --- Models ---------------------------------------------------------------------------------

@pytest.mark.django_db
def test_user_row_holds_only_ciphertext_and_index():
    user = User.objects.create_user('Ana@Example.com', PASSWORD, first_name='Ana',
                                    last_name='García')

    email, first, last, index, password = raw_row(
        'accounts_user', user.pk, 'email', 'first_name', 'last_name', 'email_index', 'password')
    for stored in (email, first, last):
        assert ENC_RE.match(stored)
    assert 'Ana' not in email + first + last and 'García' not in last
    assert index == crypto.email_index('ana@example.com')
    assert password.startswith('argon2$argon2id$')

    user = User.objects.get(pk=user.pk)
    assert (user.email, user.first_name, user.last_name) == ('Ana@example.com', 'Ana', 'García')


@pytest.mark.django_db
def test_index_follows_an_email_change():
    user = User.objects.create_user('ana@example.com', PASSWORD)
    user.email = 'bea@example.com'
    user.save(update_fields=['email'])

    assert raw_row('accounts_user', user.pk, 'email_index')[0] == crypto.email_index('bea@example.com')
    assert User.objects.get_by_natural_key('BEA@example.com') == user


@pytest.mark.django_db
@pytest.mark.parametrize('lookup', [{'email': 'ana@example.com'},
                                    {'email__iexact': 'ana@example.com'},
                                    {'first_name__icontains': 'an'},
                                    {'last_name__in': ['x']},
                                    {'email__lower': 'ana@example.com'}])
def test_query_by_encrypted_field_raises(lookup):
    with pytest.raises(crypto.EncryptedFieldLookupError) as error:
        list(User.objects.filter(**lookup))
    assert 'blind index' in str(error.value)
    with pytest.raises(crypto.EncryptedFieldLookupError):
        list(PendingRegistration.objects.filter(email='ana@example.com'))


@pytest.mark.django_db
def test_isnull_lookup_is_allowed():
    User.objects.create_user('ana@example.com', PASSWORD)
    assert User.objects.filter(email__isnull=False).count() == 1


@pytest.mark.django_db
def test_pending_registration_row():
    pending = PendingRegistration.objects.create(
        email='Ana@Example.com', password=make_password(PASSWORD), step='verify-email')

    email, index, password = raw_row('signup_pendingregistration', pending.pk,
                                     'email', 'email_index', 'password')
    assert ENC_RE.match(email) and 'Ana' not in email
    assert index == crypto.email_index('ana@example.com')
    assert password.startswith('argon2$argon2id$')
    assert PendingRegistration.objects.get().email == 'Ana@Example.com'
    assert not PendingRegistration._meta.get_field('email_index').unique
    assert PendingRegistration._meta.get_field('email_index').db_index


@pytest.mark.django_db
def test_row_tampered_in_the_database_fails_to_load():
    user = User.objects.create_user('ana@example.com', PASSWORD)
    other = User.objects.create_user('bea@example.com', PASSWORD)
    # Copy another user's (valid) email ciphertext into the first name: wrong field.
    stored_email = raw_row('accounts_user', other.pk, 'email')[0]
    with connection.cursor() as cursor:
        cursor.execute('UPDATE accounts_user SET first_name = %s WHERE id = %s',
                       [stored_email, user.pk])

    with pytest.raises(crypto.DecryptionError):
        User.objects.get(pk=user.pk)


# --- Lookups by the index -------------------------------------------------------------------

@pytest.mark.django_db
def test_lookup_through_the_index(client, admin_client):
    """Matrix: Ana@Example.com registered; ana@example.com finds it everywhere."""
    user = User.objects.create_user('Ana@Example.com', PASSWORD)

    assert User.objects.get_by_natural_key('ana@example.com') == user
    assert authenticate(username='ana@example.com', password=PASSWORD) == user
    assert email_is_registered('ana@example.com')
    page = admin_client.get('/admin/accounts/user/', {'q': 'ana@example.com'})
    assert f'/admin/accounts/user/{user.pk}/change/' in page.content.decode()


@pytest.mark.django_db
def test_unknown_email_is_not_found():
    User.objects.create_user('ana@example.com', PASSWORD)
    assert authenticate(username='bea@example.com', password=PASSWORD) is None
    assert authenticate(username='', password=PASSWORD) is None
    assert not email_is_registered('bea@example.com')


@pytest.mark.django_db
def test_case_variant_duplicate_is_refused():
    """Matrix: a second user with a case variant -> IntegrityError (unique email_index)."""
    User.objects.create_user('Ana@Example.com', PASSWORD)
    with pytest.raises(IntegrityError):
        User.objects.create_user('ANA@example.com', PASSWORD)


@pytest.mark.django_db
def test_admin_change_form_refuses_a_taken_email(admin_client):
    User.objects.create_user('ana@example.com', PASSWORD)
    bea = User.objects.create_user('bea@example.com', PASSWORD)
    url = f'/admin/accounts/user/{bea.pk}/change/'
    data = {'email': 'ANA@example.com', 'is_active': 'on',
            'date_joined_0': '2026-10-13', 'date_joined_1': '10:00:00'}

    response = admin_client.post(url, data)
    assert response.status_code == 200
    assert response.context['adminform'].form.errors['email']

    response = admin_client.post(url, {**data, 'email': 'Bea@example.com', 'first_name': 'Bea'})
    assert response.status_code == 302
    bea.refresh_from_db()
    assert (bea.email, bea.first_name) == ('Bea@example.com', 'Bea')


@pytest.mark.django_db
def test_admin_does_not_sort_or_log_plaintext(admin_client):
    response = admin_client.post('/admin/accounts/user/add/', {
        'email': 'bea@example.com', 'usable_password': 'true',
        'password1': PASSWORD, 'password2': PASSWORD,
    })
    assert response.status_code == 302
    bea = User.objects.get_by_natural_key('bea@example.com')
    admin_client.post(f'/admin/accounts/user/{bea.pk}/delete/', {'post': 'yes'})

    entries = list(LogEntry.objects.values_list('object_repr', flat=True))
    assert entries and all('bea@example.com' not in repr_ for repr_ in entries)
    page = admin_client.get('/admin/accounts/user/', {'o': '1'})  # "sort by email"
    assert page.status_code == 200


# --- Passwords ------------------------------------------------------------------------------

@pytest.mark.django_db
def test_old_pbkdf2_hash_is_upgraded_at_login(settings):
    """Matrix: a user with a PBKDF2 hash logs in; the hash becomes Argon2id."""
    old_hash = make_password(PASSWORD, hasher='pbkdf2_sha256')
    user = User.objects.create_user('ana@example.com')
    User.objects.filter(pk=user.pk).update(password=old_hash)

    assert authenticate(username='ana@example.com', password=PASSWORD) == user
    assert raw_row('accounts_user', user.pk, 'password')[0].startswith('argon2$argon2id$')


def test_argon2_is_the_default_hasher(settings):
    assert settings.PASSWORD_HASHERS[0] == 'django.contrib.auth.hashers.Argon2PasswordHasher'
    assert make_password(PASSWORD).startswith('argon2$argon2id$')


# --- Rotation -------------------------------------------------------------------------------

@pytest.mark.django_db
def test_rotation_with_reencrypt_fields(settings):
    """Matrix: a new key first; old values readable before, all on the new key after."""
    ana = User.objects.create_user('ana@example.com', PASSWORD, first_name='Ana')
    pending = PendingRegistration.objects.create(email='bea@example.com', password='x',
                                                 step='verify-email')

    settings.FIELD_ENCRYPTION_KEYS = ROTATED_KEYS
    assert User.objects.get(pk=ana.pk).first_name == 'Ana'  # old key still decrypts
    assert crypto.encrypt('x', 'a.b.c').startswith('enc:v1:test2:')

    out = io.StringIO()
    call_command('reencrypt_fields', stdout=out)

    for table, pk, columns in [('accounts_user', ana.pk, ('email', 'first_name', 'last_name')),
                               ('signup_pendingregistration', pending.pk, ('email',))]:
        for stored in raw_row(table, pk, *columns):
            assert ENC_RE.match(stored)['key_id'] == 'test2'
    assert 'accounts.user: 1 rows on key test2' in out.getvalue()
    assert 'signup.pendingregistration: 1 rows on key test2' in out.getvalue()
    assert_no_key_material(out.getvalue())
    assert 'ana@example.com' not in out.getvalue()

    settings.FIELD_ENCRYPTION_KEYS = f'test2:{ROTATED_KEY}'  # the old key removed
    assert User.objects.get_by_natural_key('ANA@example.com').first_name == 'Ana'
    assert PendingRegistration.objects.get().email == 'bea@example.com'
    assert authenticate(username='ana@example.com', password=PASSWORD) == ana


# --- Missing or malformed keys --------------------------------------------------------------

def _key_errors():
    return [e for e in run_checks() if e.id in ('accounts.E001', 'accounts.E002')]


def test_valid_keys_pass_the_check():
    assert _key_errors() == []


@pytest.mark.parametrize('setting, value, error_id', [
    ('FIELD_ENCRYPTION_KEYS', '', 'accounts.E001'),
    ('FIELD_ENCRYPTION_KEYS', 'no-colon-here', 'accounts.E001'),
    ('FIELD_ENCRYPTION_KEYS', 'k1:not base64!', 'accounts.E001'),
    ('FIELD_ENCRYPTION_KEYS', f'k1:{base64.b64encode(b"short").decode()}', 'accounts.E001'),
    ('FIELD_ENCRYPTION_KEYS', f'bad id:{ROTATED_KEY}', 'accounts.E001'),
    ('FIELD_ENCRYPTION_KEYS', f'k1:{ROTATED_KEY},k1:{ROTATED_KEY}', 'accounts.E001'),
    ('FIELD_ENCRYPTION_KEYS', f'k1:{ROTATED_KEY},', 'accounts.E001'),
    ('BLIND_INDEX_KEY', '', 'accounts.E002'),
    ('BLIND_INDEX_KEY', 'not base64!', 'accounts.E002'),
    ('BLIND_INDEX_KEY', base64.b64encode(b'x' * 16).decode(), 'accounts.E002'),
    ('BLIND_INDEX_KEY', TEST_FIELD_ENCRYPTION_KEYS.split(':', 1)[1], 'accounts.E002'),
    ('FIELD_KEY_PROVIDER', 'accounts.crypto.NoSuchProvider', 'accounts.E001'),
])
def test_missing_or_malformed_key_is_a_check_error(settings, setting, value, error_id):
    """Matrix: the app refuses to start; the message names the setting, never a value."""
    setattr(settings, setting, value)

    errors = _key_errors()
    assert [e.id for e in errors] == [error_id]
    message = errors[0].msg
    assert setting in message
    assert_no_key_material(message)
    if value and setting != 'FIELD_KEY_PROVIDER':  # a provider path is not secret
        assert value.split(':')[-1].rstrip(',') not in message


def test_missing_keys_stop_check_and_migrate(settings):
    settings.FIELD_ENCRYPTION_KEYS = ''
    settings.BLIND_INDEX_KEY = ''
    with pytest.raises(SystemCheckError) as error:
        call_command('check')
    assert 'FIELD_ENCRYPTION_KEYS' in str(error.value) and 'BLIND_INDEX_KEY' in str(error.value)
    with pytest.raises(SystemCheckError):
        call_command('migrate', skip_checks=False, verbosity=0)


def test_missing_key_never_falls_back(settings):
    settings.FIELD_ENCRYPTION_KEYS = ''
    with pytest.raises(crypto.KeyConfigurationError):
        crypto.encrypt('ana@example.com', 'accounts.user.email')
    settings.BLIND_INDEX_KEY = ''
    with pytest.raises(crypto.KeyConfigurationError):
        crypto.email_index('ana@example.com')


# --- Key generation and "no key material in any output" -------------------------------------

def test_generate_field_keys_prints_valid_new_keys(settings):
    settings.FIELD_ENCRYPTION_KEYS = ''  # runs before any key exists
    out = io.StringIO()
    call_command('generate_field_keys', stdout=out)
    text = out.getvalue()
    values = dict(line.split('=', 1) for line in text.splitlines() if not line.startswith('#'))

    keys = crypto.parse_key_list(values['FIELD_ENCRYPTION_KEYS'])
    assert len(keys) == 1 and re.fullmatch(r'k\d{8}T\d{4}', keys[0][0])
    assert len(crypto.decode_key(values['BLIND_INDEX_KEY'], 'BLIND_INDEX_KEY')) == 32
    assert keys[0][1] != crypto.decode_key(values['BLIND_INDEX_KEY'], 'BLIND_INDEX_KEY')
    assert_no_key_material(text)  # never echoes the configured keys

    out = io.StringIO()
    call_command('generate_field_keys', rotate=True, key_id='k2', stdout=out)
    entries = [line for line in out.getvalue().splitlines() if not line.startswith('#')]
    assert len(entries) == 1 and entries[0].startswith('k2:')
    crypto.parse_key_list(entries[0])


def test_no_key_material_in_reprs_and_errors():
    provider = crypto.get_key_provider()
    assert_no_key_material(repr(provider))
    broken = ROTATED_KEY[:-1]  # not valid base64
    with pytest.raises(crypto.KeyConfigurationError) as error:
        crypto.parse_key_list(f'k1:{broken}')
    assert broken not in str(error.value)
    # The decoder's own exception is not chained (tracebacks show no key either).
    assert error.value.__cause__ is None and error.value.__suppress_context__


# --- Data migration -------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_data_migration_encrypts_existing_rows_and_reverses():
    """Matrix: a plaintext user (and pending registration) before migrating; encrypted and
    indexed after; the reverse restores the plaintext."""
    before = [('accounts', '0002_email_username'), ('signup', '0001_initial')]
    after = [('accounts', '0005_email_index_required'), ('signup', '0004_email_index_required')]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO accounts_user (password, is_superuser, first_name, last_name, "
                "email, is_staff, is_active, date_joined) VALUES "
                "('x', false, 'Dev', '', 'devops@MyTreatPlan.com', true, true, now())")
            cursor.execute(
                "INSERT INTO signup_pendingregistration (id, email, password, step, created_at, "
                "updated_at) VALUES ('6f1c1d3e-0000-4000-8000-000000000001', "
                "'ana@example.com', 'x', 'verify-email', now(), now())")

        executor = MigrationExecutor(connection)
        executor.migrate(after)

        with connection.cursor() as cursor:
            cursor.execute('SELECT email, first_name, last_name, email_index FROM accounts_user')
            email, first, last, index = cursor.fetchone()
            cursor.execute('SELECT email, email_index FROM signup_pendingregistration')
            pending_email, pending_index = cursor.fetchone()
        assert all(ENC_RE.match(v) for v in (email, first, last, pending_email))
        assert index == crypto.email_index('devops@mytreatplan.com')
        assert pending_index == crypto.email_index('ana@example.com')
        # Only columns that exist at `after` (later migrations add more).
        user = User.objects.by_email('devops@mytreatplan.com').only('email', 'first_name').get()
        assert (user.email, user.first_name) == ('devops@MyTreatPlan.com', 'Dev')

        executor = MigrationExecutor(connection)
        executor.migrate(before)
        with connection.cursor() as cursor:
            cursor.execute('SELECT email, first_name, last_name FROM accounts_user')
            assert cursor.fetchone() == ('devops@MyTreatPlan.com', 'Dev', '')
            cursor.execute('SELECT email FROM signup_pendingregistration')
            assert cursor.fetchone() == ('ana@example.com',)
    finally:
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM signup_pendingregistration')
            cursor.execute('DELETE FROM accounts_user')
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())


# --- Settings, examples, CI and runbook -----------------------------------------------------

def _env_values(path):
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if line.strip() and not line.startswith('#'))


def test_key_settings_default_empty_and_examples_have_no_values(settings):
    base = settings.BASE_DIR
    text = (base / 'mytreatplan' / 'settings.py').read_text()
    for key in ('FIELD_ENCRYPTION_KEYS', 'BLIND_INDEX_KEY'):
        assert f"{key} = config('{key}', default='')" in text
        assert _env_values(base / 'settings.env.example')[key] == ''
        assert _env_values(base / 'deploy' / 'staging.env.example')[key] == ''


def test_ci_uses_the_fixed_test_keys(settings):
    ci = (settings.BASE_DIR / '.github' / 'workflows' / 'ci.yml').read_text()
    assert f'FIELD_ENCRYPTION_KEYS: {TEST_FIELD_ENCRYPTION_KEYS}' in ci
    assert f'BLIND_INDEX_KEY: {TEST_BLIND_INDEX_KEY}' in ci


def test_runbook_step_12(settings):
    text = (settings.BASE_DIR / 'deploy' / 'README.md').read_text()
    step = text.split('### 12. Field encryption keys', 1)[1].split('\n## ', 1)[0]
    for phrase in ('before the merge', 'MyTreatplan staging field keys', 'generate_field_keys',
                   'reencrypt_fields', 'Losing a key makes the data unreadable',
                   'Backups need the same keys', 'Rotate the encryption key'):
        assert phrase in step


# --- WSGI startup ---------------------------------------------------------------------------

WSGI_FILE = str(Path(__file__).resolve().parents[2] / 'mytreatplan' / 'wsgi.py')


def test_wsgi_starts_with_valid_keys():
    assert callable(runpy.run_path(WSGI_FILE)['application'])


@pytest.mark.parametrize('setting, value', [
    ('FIELD_ENCRYPTION_KEYS', ''),
    ('FIELD_ENCRYPTION_KEYS', f'k1:{ROTATED_KEY[:-1]}'),
    ('BLIND_INDEX_KEY', ''),
    ('BLIND_INDEX_KEY', 'not base64!'),
])
def test_wsgi_refuses_to_start_with_a_bad_key(settings, setting, value):
    """Matrix: missing keys -> the app (Gunicorn) refuses to start, naming the setting."""
    setattr(settings, setting, value)
    with pytest.raises(crypto.KeyConfigurationError) as error:
        runpy.run_path(WSGI_FILE)
    assert setting in str(error.value)
    assert_no_key_material(str(error.value))
