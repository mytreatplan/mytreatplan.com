"""Story 1.15: OVHcloud KMS. The REST client (mytp_publicsite/kms.py) and `manage.py
kmscheck` with the KMS HTTP calls replaced by a fake, the certificate expiry check, the
job and the deploy files. No database and no network are needed."""
import base64
import errno
import io
import json
import shutil
import socket
import ssl
import subprocess
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sentry_sdk
from django.conf import settings as django_settings
from django.core.management import CommandError, call_command

from mytp_publicsite import jobs, kms
from mytp_publicsite.management.commands import kmscheck, runjob
from mytp_publicsite.tests.test_monitoring import _FakeSentry, sentry_capture  # noqa: F401

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

needs_openssl = pytest.mark.skipif(shutil.which('openssl') is None, reason='openssl not available')


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


def _assert_no_secrets(*texts):
    for text in texts:
        for secret in SECRETS:
            assert secret not in str(text)


def _run(capsys):
    """kmscheck; returns (stdout, error or None)."""
    error = None
    try:
        call_command('kmscheck')
    except CommandError as exc:
        error = exc
        assert exc.__suppress_context__ or exc.__cause__ is None and exc.__context__ is None
    out = capsys.readouterr()
    _assert_no_secrets(out.out, out.err, error)
    return out.out, error


# --- kmscheck: the matrix -----------------------------------------------------------------

def test_round_trip_ok(kms_env, capsys):
    out, error = _run(capsys)

    assert error is None
    assert kms_env.ops == ['get', 'generate', 'unwrap']
    assert kms_env.calls[1][1] == {'name': 'mytp-kmscheck', 'size': 256}
    assert kms_env.calls[2][1] == {'key': WRAPPED}
    assert f'service key {KEY_ID} state active' in out
    assert 'round trip ok' in out
    assert 'client certificate expires 2099-01-31 12:00 UTC' in out


def test_certificate_expiring_still_round_trips_then_fails(kms_env, capsys):
    soon = datetime.now(timezone.utc) + timedelta(days=12)
    kms_env.expiry['value'] = soon

    out, error = _run(capsys)

    assert kms_env.ops == ['get', 'generate', 'unwrap']
    assert 'round trip ok' in out
    assert 'certificate expires' in str(error)
    assert f'{soon:%Y-%m-%d}' in str(error)


def test_certificate_with_more_than_30_days_left_passes(kms_env, capsys):
    kms_env.expiry['value'] = datetime.now(timezone.utc) + timedelta(days=31)

    out, error = _run(capsys)

    assert error is None


@pytest.mark.parametrize('key', kms.REQUIRED_SETTINGS)
def test_missing_setting_names_key_and_calls_nothing(kms_env, settings, capsys, key):
    setattr(settings, key, '')

    out, error = _run(capsys)

    assert key in str(error)
    assert 'step settings' in str(error)
    assert kms_env.calls == []


@pytest.mark.parametrize('key,value,message', [
    ('KMS_ENDPOINT', 'http://eu-west-rbx.okms.ovh.net', 'KMS_ENDPOINT must be https'),
    ('KMS_ENDPOINT', 'https://eu-west-rbx.okms.ovh.net/api/x', 'KMS_ENDPOINT must be https'),
    ('KMS_ENDPOINT', 'https://eu-west-rbx.okms.ovh.net#x', 'KMS_ENDPOINT must be https'),
    ('KMS_ENDPOINT', 'https://u:pw@eu-west-rbx.okms.ovh.net', 'KMS_ENDPOINT must be https'),
    ('KMS_OKMS_ID', '../other', 'KMS_OKMS_ID must be a UUID'),
    ('KMS_SERVICE_KEY_ID', 'a/b', 'KMS_SERVICE_KEY_ID must be a UUID'),
    ('KMS_CLIENT_CERT', '/nonexistent/kms.crt', 'KMS_CLIENT_CERT file'),
    ('KMS_CA_FILE', '/nonexistent/ca.pem', 'KMS_CA_FILE file'),
])
def test_invalid_setting_calls_nothing(kms_env, settings, capsys, key, value, message):
    setattr(settings, key, value)

    out, error = _run(capsys)

    assert message in str(error)
    assert kms_env.calls == []


def test_private_key_readable_by_others_is_refused(kms_env, settings, capsys):
    Path(settings.KMS_CLIENT_KEY).chmod(0o644)

    out, error = _run(capsys)

    assert 'mode 600' in str(error)
    assert kms_env.calls == []


@pytest.mark.parametrize('key', ['KMS_CLIENT_CERT', 'KMS_CLIENT_KEY'])
def test_unreadable_credential_file_fails_at_settings(kms_env, settings, capsys, monkeypatch, key):
    unreadable = getattr(settings, key)
    real_access = kms.os.access
    monkeypatch.setattr(kms.os, 'access',
                        lambda path, mode: path != unreadable and real_access(path, mode))

    out, error = _run(capsys)

    assert 'step settings failed' in str(error)
    assert f'{key} file' in str(error) and 'not readable' in str(error)
    assert kms_env.calls == []


@pytest.mark.parametrize('status', [401, 403])
@pytest.mark.parametrize('op', ['get', 'generate', 'unwrap'])
def test_auth_refused(kms_env, capsys, status, op):
    kms_env.fail[op] = status

    out, error = _run(capsys)

    assert 'step auth failed' in str(error)
    assert f'HTTP {status}' in str(error)
    assert 'request_id req-abc.123' in str(error)
    assert 'round trip ok' not in out


@pytest.mark.parametrize('op,status', [('generate', 400), ('get', 404), ('unwrap', 400)])
def test_key_not_usable_names_step_and_status(kms_env, capsys, op, status):
    kms_env.fail[op] = status
    kms_env.state = 'deactivated'

    out, error = _run(capsys)

    assert f'step {op} failed: HTTP {status}' in str(error)


def test_mismatch_prints_neither_value(kms_env, capsys):
    kms_env.unwrapped = bytes(32)

    out, error = _run(capsys)

    assert 'step mismatch' in str(error)
    assert 'round trip ok' not in out
    assert base64.b64encode(bytes(32)).decode() not in str(error) + out


def test_short_data_key_is_a_mismatch(kms_env, capsys):
    kms_env.unwrapped = PLAINTEXT[:16]

    out, error = _run(capsys)

    assert 'step mismatch' in str(error)


@pytest.mark.parametrize('failure,detail', [
    (urllib.error.URLError(socket.gaierror(8, 'nodename nor servname provided')),
     'could not resolve'),
    (urllib.error.URLError(ssl.SSLCertVerificationError(1, 'certificate verify failed')),
     'TLS error'),
    (urllib.error.URLError(TimeoutError()), 'timed out'),
    (TimeoutError(), 'timed out'),
    (ConnectionResetError(errno.ECONNRESET, 'Connection reset by peer'), 'ECONNRESET'),
])
def test_unreachable_is_connect_step(kms_env, capsys, failure, detail):
    kms_env.fail['get'] = failure

    out, error = _run(capsys)

    assert 'step connect failed' in str(error)
    assert detail in str(error)


def _tls_alert(reason):
    error = ssl.SSLError(1, f'[SSL: {reason}] tlsv1 alert')
    error.reason = reason
    return error


@pytest.mark.parametrize('failure', [
    urllib.error.URLError(_tls_alert('TLSV1_ALERT_UNKNOWN_CA')),
    _tls_alert('TLSV13_ALERT_CERTIFICATE_REQUIRED'),
    urllib.error.URLError(_tls_alert('SSLV3_ALERT_CERTIFICATE_EXPIRED')),
    urllib.error.URLError(_tls_alert('SSLV3_ALERT_HANDSHAKE_FAILURE')),
    _tls_alert('TLSV1_ALERT_ACCESS_DENIED'),
])
def test_certificate_refused_in_handshake_is_auth_step(kms_env, capsys, failure):
    kms_env.fail['get'] = failure

    out, error = _run(capsys)

    assert 'step auth failed: TLS error' in str(error)


def test_unexpected_responses(kms_env, capsys, monkeypatch):
    real_open = kms_env.open

    def no_plaintext(client, request):
        response = real_open(client, request)
        if request.full_url.endswith('/datakey'):
            return _Response({'key': WRAPPED})
        return response
    monkeypatch.setattr(kms.KMSClient, '_open', lambda self, request: no_plaintext(self, request))

    out, error = _run(capsys)

    assert 'step generate failed: unexpected response' in str(error)


def test_certificate_read_failure_stops_before_kms(kms_env, capsys, monkeypatch):
    def unreadable(path):
        raise kms.KMSError('cert', 'could not read the expiry')
    monkeypatch.setattr(kms, 'certificate_expiry', unreadable)

    out, error = _run(capsys)

    assert 'step cert failed' in str(error)
    assert kms_env.calls == []


def test_error_body_free_text_is_not_echoed(kms_env, capsys):
    kms_env.fail['unwrap'] = 400
    kms_env.error_body = {'error_code': f'x {WRAPPED}', 'request_id': PLAINTEXT_B64,
                          'message': f'key {WRAPPED}'}

    out, error = _run(capsys)

    assert 'HTTP 400' in str(error)  # _run already checked nothing secret is in it


# --- kmscheck: field keys (story 2.12) ---------------------------------------------------------

FIELD_KMS_KEYS = 'k2:FIELD-WRAPPED-K2-MARKER,k1:FIELD-WRAPPED-K1-MARKER'


@pytest.fixture
def field_keys(settings):
    settings.FIELD_KEY_PROVIDER = 'accounts.crypto.KMSKeyProvider'
    settings.FIELD_KMS_KEYS = FIELD_KMS_KEYS
    settings.BLIND_INDEX_KMS_KEY = 'FIELD-WRAPPED-BI-MARKER'


def test_no_field_keys_configured_is_reported(kms_env, capsys):
    out, error = _run(capsys)

    assert error is None
    assert 'no KMS field keys configured' in out
    assert kms_env.ops == ['get', 'generate', 'unwrap']


def test_field_keys_each_unwrapped(kms_env, field_keys, capsys):
    out, error = _run(capsys)

    assert error is None
    assert kms_env.ops == ['get', 'generate', 'unwrap', 'unwrap', 'unwrap', 'unwrap']
    assert [body for op, body in kms_env.calls[3:]] == [
        {'key': 'FIELD-WRAPPED-K2-MARKER'}, {'key': 'FIELD-WRAPPED-K1-MARKER'},
        {'key': 'FIELD-WRAPPED-BI-MARKER'}]
    assert 'kmscheck: field key k2 unwrap ok' in out
    assert 'kmscheck: field key k1 unwrap ok' in out
    assert 'kmscheck: blind index key blind-index unwrap ok' in out


def test_field_keys_checked_when_set_with_the_env_provider(kms_env, settings, capsys):
    settings.FIELD_KMS_KEYS = 'k1:FIELD-WRAPPED-K1-MARKER'  # e.g. during the move

    out, error = _run(capsys)

    assert error is None
    assert 'field key k1 unwrap ok' in out and 'blind index' not in out


@pytest.mark.parametrize('failure, shown', [(400, 'step unwrap, HTTP 400'),
                                            (403, 'step auth, HTTP 403')])
def test_field_key_that_cannot_be_unwrapped_fails(kms_env, field_keys, capsys, failure, shown):
    kms_env.field_keys['FIELD-WRAPPED-K1-MARKER'] = failure

    out, error = _run(capsys)

    assert 'field key k2 unwrap ok' in out
    assert f'field key k1 unwrap failed ({shown})' in out
    assert 'blind index key blind-index unwrap ok' in out  # every key is still reported
    assert 'step field-keys failed: cannot unwrap k1' in str(error)


def test_field_key_of_the_wrong_size_fails(kms_env, field_keys, capsys):
    kms_env.field_keys['FIELD-WRAPPED-BI-MARKER'] = b'short'

    out, error = _run(capsys)

    assert 'blind index key blind-index unwrap failed (wrong size)' in out
    assert 'cannot unwrap blind-index' in str(error)


@pytest.mark.parametrize('bad', ['unwrap', 'settings'])
def test_field_key_failure_does_not_hide_certificate_expiry(kms_env, field_keys, settings,
                                                            capsys, bad):
    soon = datetime.now(timezone.utc) + timedelta(days=12)
    kms_env.expiry['value'] = soon
    if bad == 'unwrap':
        kms_env.field_keys['FIELD-WRAPPED-K1-MARKER'] = 400
    else:
        settings.FIELD_KMS_KEYS = ''

    out, error = _run(capsys)

    message = str(error)
    if bad == 'unwrap':
        assert 'step field-keys failed: cannot unwrap k1' in message
    else:
        assert 'step settings failed' in message and 'FIELD_KMS_KEYS' in message
    assert f'client certificate expires {soon:%Y-%m-%d}' in message
    assert 'renew it' in message


@pytest.mark.parametrize('setting', ['FIELD_KMS_KEYS', 'BLIND_INDEX_KMS_KEY'])
def test_kms_provider_without_field_keys_fails(kms_env, field_keys, settings, capsys, setting):
    setattr(settings, setting, '')

    out, error = _run(capsys)

    assert 'step settings failed' in str(error) and setting in str(error)


def test_malformed_field_keys_fail_without_echoing_them(kms_env, field_keys, settings, capsys):
    settings.FIELD_KMS_KEYS = 'k1 FIELD-WRAPPED-K1-MARKER'

    out, error = _run(capsys)  # checks no wrapped key is echoed

    assert 'FIELD_KMS_KEYS' in str(error)


@pytest.mark.parametrize('case', ['ok', 'failed'])
def test_runjob_field_key_failure_reaches_sentry_without_secrets(
        kms_env, field_keys, monkeypatch, sentry_capture, case):  # noqa: F811
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
    _assert_no_secrets(json.dumps(sentry_capture.events))


# --- The client itself -----------------------------------------------------------------

def test_client_loads_credentials_without_prompting(kms_env, settings):
    client = kms.KMSClient.from_settings(settings)

    with pytest.raises(kms.KMSError) as exc:
        client._ssl_context()  # the marker files are not real PEM

    assert exc.value.step == 'auth'
    _assert_no_secrets(exc.value)
    assert exc.value.__suppress_context__


@needs_openssl
def test_client_tls_context_with_real_certificate(tmp_path):
    cert, key = _make_cert(tmp_path, days=40)
    client = kms.KMSClient(ENDPOINT, OKMS_ID, str(cert), str(key))

    context = client._ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname


@needs_openssl
def test_client_refuses_encrypted_key_without_prompting(tmp_path):
    cert, key = _make_cert(tmp_path, days=40)
    encrypted = tmp_path / 'enc.key'
    subprocess.run(['openssl', 'pkey', '-in', str(key), '-aes256', '-passout', 'pass:pw',
                    '-out', str(encrypted)], check=True, capture_output=True)
    client = kms.KMSClient(ENDPOINT, OKMS_ID, str(cert), str(encrypted))

    with pytest.raises(kms.KMSError, match='step auth failed'):
        client._ssl_context()


def test_client_has_no_third_party_imports():
    source = (REPO / 'mytp_publicsite' / 'kms.py').read_text()
    imports = {line.split()[1].split('.')[0] for line in source.splitlines()
               if line.startswith(('import ', 'from '))}
    assert imports <= {'base64', 'binascii', 'errno', 'http', 'json', 'os', 're', 'socket',
                       'ssl', 'stat', 'subprocess', 'urllib', 'datetime'}


# --- Certificate expiry with openssl ---------------------------------------------------------

def _make_cert(tmp_path, days):
    cert, key = tmp_path / 'c.pem', tmp_path / 'k.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', str(days),
                    '-subj', '/CN=mytp-kmscheck-test', '-keyout', str(key), '-out', str(cert)],
                   check=True, capture_output=True)
    key.chmod(0o600)
    return cert, key


@needs_openssl
def test_certificate_expiry_reads_not_after(tmp_path):
    cert, key = _make_cert(tmp_path, days=40)

    expiry = kms.certificate_expiry(cert)

    left = expiry - datetime.now(timezone.utc)
    assert timedelta(days=39) < left <= timedelta(days=40)


def test_certificate_expiry_not_a_certificate(tmp_path):
    if shutil.which('openssl') is None:
        pytest.skip('openssl not available')
    path = tmp_path / 'x.pem'
    path.write_text(f'{KEY_MARKER}\n')

    with pytest.raises(kms.KMSError, match='step cert failed') as exc:
        kms.certificate_expiry(path)

    _assert_no_secrets(exc.value)


def test_certificate_expiry_without_openssl(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError('openssl')
    monkeypatch.setattr(kms.subprocess, 'run', missing)

    with pytest.raises(kms.KMSError, match='openssl not found'):
        kms.certificate_expiry(tmp_path / 'c.pem')


@pytest.mark.parametrize('text,expected', [
    ('Oct 12 10:00:00 2027 GMT', datetime(2027, 10, 12, 10, 0, tzinfo=timezone.utc)),
    ('Feb  3 01:02:03 2028 GMT', datetime(2028, 2, 3, 1, 2, 3, tzinfo=timezone.utc)),
])
def test_parse_openssl_date(text, expected):
    assert kms.parse_openssl_date(text) == expected


@pytest.mark.parametrize('text', ['2027-10-12', 'Foo 12 10:00:00 2027 GMT',
                                  'Oct 12 10:00:00 2027 CET'])
def test_parse_openssl_date_rejects(text):
    with pytest.raises(kms.KMSError, match='step cert'):
        kms.parse_openssl_date(text)


# --- Through runjob: Sentry -------------------------------------------------------------------

def test_kmscheck_job_declared_daily():
    job = jobs.load_jobs(jobs.JOBS_FILE)['kmscheck']
    assert (job.command, job.args) == ('kmscheck', ())
    assert job.schedule == '*-*-* 04:00:00 UTC'
    assert job.crontab == '0 4 * * *'


def _runjob_with(monkeypatch, sentry):
    checkins = []

    def capture_checkin(**kwargs):
        checkins.append(kwargs)
        return 'checkin-1'
    monkeypatch.setattr(runjob, 'sentry_sdk', sentry)
    monkeypatch.setattr(runjob, 'capture_checkin', capture_checkin)
    monkeypatch.setattr(runjob, 'load_jobs', lambda: jobs.load_jobs(jobs.JOBS_FILE))
    return checkins


def test_runjob_kmscheck_ok_checks_in(kms_env, monkeypatch, settings):
    settings.SENTRY_ENVIRONMENT = 'staging'
    fake = _FakeSentry(active=True)
    checkins = _runjob_with(monkeypatch, fake)

    call_command('runjob', 'kmscheck')

    assert [c['status'] for c in checkins] == ['in_progress', 'ok']
    assert checkins[0]['monitor_slug'] == 'mytp-staging-kmscheck'


@pytest.mark.parametrize('case', ['mismatch', 'auth', 'expiring'])
def test_runjob_kmscheck_failure_reaches_sentry_without_secrets(
        kms_env, monkeypatch, sentry_capture, case):  # noqa: F811
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
    _assert_no_secrets(json.dumps(sentry_capture.events))


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
