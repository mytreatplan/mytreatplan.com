"""Story 1.15: OVHcloud KMS. `manage.py kmscheck` through the portal's job (runjob,
Sentry), the provider paths it accepts, and the deploy files, with the KMS HTTP calls
replaced by a fake. The client and the command moved to mytp-core (epic-platform entry 4)
with their unit tests (the kmscheck matrix, the client, the certificate expiry). No
database and no network are needed."""
import base64
import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sentry_sdk
from django.conf import settings as django_settings
from django.core.management import CommandError, call_command

from conftest import _assert_no_secrets, _runjob_with
# The package's module: kmscheck calls its functions, so the fixture patches them there.
from mytp_core import kms
from mytp_core import jobs
from mytp_publicsite.tests.test_monitoring import _FakeSentry

REPO = Path(django_settings.BASE_DIR)
DEPLOY = REPO / 'deploy'

ENDPOINT = 'https://eu-west-rbx.okms.ovh.net'
OKMS_ID = '6f1c2a0e-1111-4b8e-9c3d-0a1b2c3d4e5f'
KEY_ID = '0d9e8f7a-2222-4c6b-8a5d-112233445566'
PLAINTEXT = bytes(range(32))
PLAINTEXT_B64 = base64.b64encode(PLAINTEXT).decode()
WRAPPED = 'eyJhbGciOiJBMjU2S1ciLCJraWQiOiJ3cmFwcGVkLWtleS1tYXJrZXIifQ.WRAPPED-MARKER-7Qx'
CERT_MARKER = 'CERT-MATERIAL-MARKER-91c'
KEY_MARKER = 'PRIVATE-KEY-MARKER-4fd'
# Field keys (story 2.12): wrapped values in FIELD_KMS_KEYS / BLIND_INDEX_KMS_KEY and the
# plain keys the KMS unwraps them to.
FIELD_PLAIN = {
    'FIELD-WRAPPED-K1-MARKER': b'test-only-field-plain-k1-32byte!',
    'FIELD-WRAPPED-K2-MARKER': b'test-only-field-plain-k2-32byte!',
    'FIELD-WRAPPED-BI-MARKER': b'test-only-field-plain-bi-32byte!',
}
SECRETS = (PLAINTEXT_B64, WRAPPED, 'WRAPPED-MARKER', CERT_MARKER, KEY_MARKER,
           *FIELD_PLAIN, *(base64.b64encode(v).decode() for v in FIELD_PLAIN.values()),
           *(v.decode() for v in FIELD_PLAIN.values()))
FAR_EXPIRY = datetime(2099, 1, 31, 12, 0, tzinfo=timezone.utc)

class _Response:
    def __init__(self, body):
        self._body = json.dumps(body).encode() if not isinstance(body, bytes) else body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeKMS:
    """Answers the three data-plane calls like OKMS; `fail` maps an operation (get,
    generate, unwrap) to an HTTP status or an exception to raise instead."""

    def __init__(self):
        self.calls = []
        self.fail = {}
        self.field_keys = dict(FIELD_PLAIN)  # wrapped -> plain, or an HTTP status
        self.state = 'active'
        self.unwrapped = PLAINTEXT
        self.error_body = {'error_code': 17125377, 'request_id': 'req-abc.123',
                           'errors': [f'bad key {WRAPPED} {PLAINTEXT_B64}']}

    def open(self, client, request):
        url = request.full_url
        base = f'{ENDPOINT}/api/{OKMS_ID}/v1/servicekey/{KEY_ID}'
        body = json.loads(request.data) if request.data else None
        if request.get_method() == 'GET' and url == base:
            op = 'get'
        elif request.get_method() == 'POST' and url == f'{base}/datakey':
            op = 'generate'
        elif request.get_method() == 'POST' and url == f'{base}/datakey/decrypt':
            op = 'unwrap'
        else:  # pragma: no cover - a wrong request is a test failure
            raise AssertionError(f'unexpected request {request.get_method()} {url}')
        self.calls.append((op, body))
        failure = self.fail.get(op)
        if isinstance(failure, int):
            raise urllib.error.HTTPError(url, failure, 'error', {},
                                         io.BytesIO(json.dumps(self.error_body).encode()))
        if failure is not None:
            raise failure
        if op == 'get':
            return _Response({'id': KEY_ID, 'name': 'mytp-staging', 'type': 'oct',
                              'size': 256, 'attributes': {'state': self.state}})
        if op == 'generate':
            return _Response({'key': WRAPPED, 'plaintext': PLAINTEXT_B64})
        if body['key'] in self.field_keys:
            plain = self.field_keys[body['key']]
            if isinstance(plain, int):
                raise urllib.error.HTTPError(url, plain, 'error', {},
                                             io.BytesIO(json.dumps(self.error_body).encode()))
            return _Response({'plaintext': base64.b64encode(plain).decode()})
        assert body == {'key': WRAPPED}
        return _Response({'plaintext': base64.b64encode(self.unwrapped).decode()})

    @property
    def ops(self):
        return [op for op, _ in self.calls]


@pytest.fixture
def kms_env(tmp_path, monkeypatch, settings):
    """KMS settings, credential files (0600) and the fake KMS; expiry far away."""
    cert = tmp_path / 'kms-client.crt'
    key = tmp_path / 'kms-client.key'
    cert.write_text(f'-----BEGIN CERTIFICATE-----\n{CERT_MARKER}\n-----END CERTIFICATE-----\n')
    key.write_text(f'-----BEGIN PRIVATE KEY-----\n{KEY_MARKER}\n-----END PRIVATE KEY-----\n')
    cert.chmod(0o600)
    key.chmod(0o600)
    settings.KMS_ENDPOINT = ENDPOINT
    settings.KMS_OKMS_ID = OKMS_ID
    settings.KMS_SERVICE_KEY_ID = KEY_ID
    settings.KMS_CLIENT_CERT = str(cert)
    settings.KMS_CLIENT_KEY = str(key)
    settings.KMS_CA_FILE = ''

    fake = FakeKMS()
    monkeypatch.setattr(kms.KMSClient, '_open', lambda self, request: fake.open(self, request))
    expiry = {'value': FAR_EXPIRY}
    monkeypatch.setattr(kms, 'certificate_expiry', lambda path: expiry['value'])
    fake.expiry = expiry
    return fake


def _run(capsys):
    """kmscheck; returns (stdout, error or None)."""
    error = None
    try:
        call_command('kmscheck')
    except CommandError as exc:
        error = exc
        assert exc.__suppress_context__ or exc.__cause__ is None and exc.__context__ is None
    out = capsys.readouterr()
    _assert_no_secrets(SECRETS, out.out, out.err, error)
    return out.out, error


# --- kmscheck: field keys (story 2.12) ---------------------------------------------------------

FIELD_KMS_KEYS = 'k2:FIELD-WRAPPED-K2-MARKER,k1:FIELD-WRAPPED-K1-MARKER'


@pytest.fixture(params=['accounts.crypto.KMSKeyProvider', 'mytp_core.crypto.KMSKeyProvider'])
def field_keys(settings, request):
    """The KMS provider under the live path (accounts.crypto) and the new one (mytp_core)."""
    settings.FIELD_KEY_PROVIDER = request.param
    settings.FIELD_KMS_KEYS = FIELD_KMS_KEYS
    settings.BLIND_INDEX_KMS_KEY = 'FIELD-WRAPPED-BI-MARKER'


def test_field_keys_each_unwrapped(kms_env, field_keys, capsys):
    """Matrix: kmscheck treats both provider paths as the KMS provider."""
    out, error = _run(capsys)

    assert error is None
    assert kms_env.ops == ['get', 'generate', 'unwrap', 'unwrap', 'unwrap', 'unwrap']
    assert [body for op, body in kms_env.calls[3:]] == [
        {'key': 'FIELD-WRAPPED-K2-MARKER'}, {'key': 'FIELD-WRAPPED-K1-MARKER'},
        {'key': 'FIELD-WRAPPED-BI-MARKER'}]
    assert 'kmscheck: field key k2 unwrap ok' in out
    assert 'kmscheck: field key k1 unwrap ok' in out
    assert 'kmscheck: blind index key blind-index unwrap ok' in out


@pytest.mark.parametrize('case', ['ok', 'failed'])
def test_runjob_field_key_failure_reaches_sentry_without_secrets(
        kms_env, field_keys, monkeypatch, sentry_capture, case):
    if case == 'failed':
        kms_env.field_keys['FIELD-WRAPPED-K2-MARKER'] = 400
    checkins = _runjob_with(monkeypatch, sentry_sdk)

    if case == 'failed':
        with pytest.raises(CommandError):
            call_command('runjob', 'kmscheck')
    else:
        call_command('runjob', 'kmscheck')
    sentry_sdk.flush()

    assert checkins[-1]['status'] == ('error' if case == 'failed' else 'ok')
    _assert_no_secrets(SECRETS, json.dumps(sentry_capture.events))


# --- Through runjob: Sentry -------------------------------------------------------------------

def test_kmscheck_job_declared_daily():
    job = jobs.load_jobs(jobs.release_jobs_file())['kmscheck']
    assert (job.command, job.args) == ('kmscheck', ())
    assert job.schedule == '*-*-* 04:00:00 UTC'
    assert job.crontab == '0 4 * * *'


def test_runjob_kmscheck_ok_checks_in(kms_env, monkeypatch, settings):
    settings.SENTRY_ENVIRONMENT = 'staging'
    settings.SITE = 'dev'  # the slug names the site, not the environment
    fake = _FakeSentry(active=True)
    checkins = _runjob_with(monkeypatch, fake)

    call_command('runjob', 'kmscheck')

    assert [c['status'] for c in checkins] == ['in_progress', 'ok']
    assert checkins[0]['monitor_slug'] == 'mytp-dev-kmscheck'


@pytest.mark.parametrize('case', ['mismatch', 'auth', 'expiring'])
def test_runjob_kmscheck_failure_reaches_sentry_without_secrets(
        kms_env, monkeypatch, sentry_capture, case):
    if case == 'mismatch':
        kms_env.unwrapped = bytes(32)
    elif case == 'auth':
        kms_env.fail['generate'] = 403
    else:
        kms_env.expiry['value'] = datetime.now(timezone.utc) + timedelta(days=5)
    checkins = _runjob_with(monkeypatch, sentry_sdk)

    with pytest.raises(CommandError):
        call_command('runjob', 'kmscheck')
    sentry_sdk.flush()

    assert [c['status'] for c in checkins] == ['in_progress', 'error']
    [event] = [e for e in sentry_capture.events if e.get('exception')]
    values = event['exception']['values']
    assert len(values) == 1  # nothing chained
    for frame in values[0].get('stacktrace', {}).get('frames', []):
        assert 'vars' not in frame
    _assert_no_secrets(SECRETS, json.dumps(sentry_capture.events))


# --- Deploy files -------------------------------------------------------------------------------

def test_kms_settings_default_empty():
    text = (REPO / 'mytreatplan' / 'settings.py').read_text()
    for key in (*kms.REQUIRED_SETTINGS, 'KMS_CA_FILE'):
        assert f"{key} = config('{key}', default='')" in text


def test_staging_env_example_lists_kms_settings_without_values():
    values = dict(
        line.split('=', 1) for line in (DEPLOY / 'staging.env.example').read_text().splitlines()
        if line.strip() and not line.startswith('#')
    )
    for key in (*kms.REQUIRED_SETTINGS, 'KMS_CA_FILE'):
        assert values[key] == ''


def test_runbook_covers_kms_setup_rotation_and_renewal():
    text = (DEPLOY / 'README.md').read_text()
    assert '### 11. KMS' in text
    assert '## Keys (KMS)' in text
    for phrase in ('okms:apikms:serviceKey/get', 'okms:apikms:serviceKey/dataKey/create',
                   'okms:apikms:serviceKey/dataKey/decrypt', 'manage.py kmscheck',
                   'No backup copy of the private key', 'Renew the access certificate', 'Rotate the service key'):
        assert phrase in text


def test_kms_provider_without_field_keys_fails_under_either_path(kms_env, field_keys,
                                                                  settings, capsys):
    """Both paths make the field keys required (the old string comparison knew only one)."""
    settings.BLIND_INDEX_KMS_KEY = ''

    out, error = _run(capsys)

    assert 'step settings failed' in str(error) and 'BLIND_INDEX_KMS_KEY' in str(error)


def test_kms_reexport_is_the_package_module():
    from mytp_publicsite import kms as portal_kms
    assert portal_kms.KMSClient is kms.KMSClient
    assert portal_kms.REQUIRED_SETTINGS == kms.REQUIRED_SETTINGS
