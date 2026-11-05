"""The signed ping between admindev and dev (epic-admin-interface entry 1): the portal's
/api/v1/ping, the log of every call to /api/, `ping_peer`, the purge and the deploy
files. The admin site is played by its signing key (a fixed, non-secret test seed); no
test reaches the network or a KMS."""
import base64
import io
import json
import logging
import time
import urllib.error
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.conf import settings as django_settings
from django.core.management import CommandError, call_command
from django.utils import timezone

from integration import calls
from integration.models import InterfaceCall
from integration.peers import parse_peers
from mytp_core import interface

DEPLOY = Path(django_settings.BASE_DIR) / 'deploy'

ADMIN_SEED = b'test-only-interface-seed-admin!!'
ADMIN_NEW_SEED = b'test-only-interface-seed-admin2!'
PORTAL_SEED = b'test-only-interface-seed-portal!'
ADMIN = interface.SigningKey('a1', ADMIN_SEED)
ADMIN_NEW = interface.SigningKey('a2', ADMIN_NEW_SEED)
PORTAL = interface.SigningKey('p1', PORTAL_SEED)
SEED_TEXTS = [text for seed in (ADMIN_SEED, ADMIN_NEW_SEED, PORTAL_SEED)
              for text in (seed.decode(), base64.b64encode(seed).decode(), seed.hex())]
PING = '/api/v1/ping'


@pytest.fixture(autouse=True)
def interface_settings(settings):
    """dev, with its own plain test key and admindev's two public keys."""
    settings.SITE = 'dev'
    settings.INTERFACE_SIGNING_KEY = f'p1:{base64.b64encode(PORTAL_SEED).decode()}'
    settings.INTERFACE_PEER_KEYS = (f'admindev:a1:{ADMIN.public_text},'
                                    f'admindev:a2:{ADMIN_NEW.public_text}')
    settings.INTERFACE_PEERS = 'admindev=https://admindev.example.test'
    interface.reset_signing_key()
    yield settings
    interface.reset_signing_key()


def signed(target=PING, key=ADMIN, audience='dev', issuer='admindev', method='GET', **kwargs):
    token = interface.sign_request(method, target, b'', audience=audience, issuer=issuer,
                                   signing_key=key, **kwargs)
    return interface.authorization_header(token)


def call(client, target=PING, authorization=None, **extra):
    if authorization is not None:
        extra['HTTP_AUTHORIZATION'] = authorization
    return client.get(target, REMOTE_ADDR='203.0.113.7', **extra)


def only_call():
    rows = list(InterfaceCall.objects.all())
    assert len(rows) == 1
    return rows[0]


# --- Matrix -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_ping_from_admindev_is_answered_and_logged(client):
    before = timezone.now()
    response = call(client, authorization=signed())
    assert response.status_code == 200
    assert response['Content-Type'] == 'application/json'
    data = response.json()
    assert data['site'] == 'dev' and data['region'] == 'eu'
    assert data['time'].endswith('Z') and '+' not in data['time']
    assert before <= timezone.datetime.fromisoformat(data['time']) <= timezone.now()
    assert 'no-cache' in response['Cache-Control']
    row = only_call()
    assert (row.iss, row.kid, row.method, row.path, row.accepted, row.reason, row.status,
            row.ip) == ('admindev', 'a1', 'GET', PING, True, '', 200, '203.0.113.7')


@pytest.mark.django_db
def test_replay_is_refused_and_logged(client, caplog):
    caplog.set_level(logging.INFO, logger='integration.calls')
    authorization = signed()
    assert call(client, authorization=authorization).status_code == 200
    response = call(client, authorization=authorization)
    assert response.status_code == 401
    assert response.json() == {'error': {'code': 'replayed', 'message':
                                         'The request signature was already used.'}}
    refused = InterfaceCall.objects.get(accepted=False)
    assert (refused.reason, refused.status, refused.iss, refused.kid) == \
        ('replayed', 401, 'admindev', 'a1')
    warnings = [r.getMessage() for r in caplog.records
                if r.name == 'integration.calls' and r.levelno == logging.WARNING]
    assert len(warnings) == 1 and 'refused: replayed' in warnings[0]
    token = authorization.split(' ', 1)[1]
    assert token not in caplog.text
    assert all(part not in caplog.text for part in token.split('.'))


@pytest.mark.django_db
@pytest.mark.parametrize('authorization, target, code', [
    (lambda: signed(now=time.time() - 120), PING, 'expired'),
    (lambda: signed(audience='dev-ae'), PING, 'audience'),
    (lambda: signed(), PING + '?x=1', 'request_hash'),
    (lambda: signed(target=PING + '?x=1'), PING, 'request_hash'),
    (lambda: signed(issuer='dev-ae'), PING, 'unknown_key'),
    (lambda: signed(key=interface.SigningKey('a9', ADMIN_SEED)), PING, 'unknown_key'),
    (lambda: signed(key=interface.SigningKey('a1', PORTAL_SEED)), PING, 'signature'),
    (lambda: 'MyTP-Signature garbage', PING, 'signature'),
    (lambda: 'Bearer abc', PING, 'missing'),
    (lambda: None, PING, 'missing'),
])
def test_refusals_are_401_with_the_code_only_and_logged(client, authorization, target, code):
    response = call(client, target=target, authorization=authorization())
    assert response.status_code == 401
    assert response.json() == interface.refusal_body(code)
    row = only_call()
    assert (row.accepted, row.reason, row.status) == (False, code, 401)


@pytest.mark.django_db
def test_rotation_new_kid_is_accepted_while_both_are_known(client, settings):
    assert call(client, authorization=signed(key=ADMIN_NEW)).status_code == 200
    settings.INTERFACE_PEER_KEYS = f'admindev:a2:{ADMIN_NEW.public_text}'
    assert call(client, authorization=signed(key=ADMIN)).json()['error']['code'] == \
        'unknown_key'
    assert call(client, authorization=signed(key=ADMIN_NEW)).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize('change', [
    {'INTERFACE_SIGNING_KEY': ''},
    {'INTERFACE_SIGNING_KEY': 'p1:not-a-key'},
    {'INTERFACE_PEER_KEYS': 'admindev:a1'},
])
def test_without_a_usable_key_api_answers_503(client, settings, change, caplog):
    for name, value in change.items():
        setattr(settings, name, value)
    response = call(client, authorization=signed())
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'unavailable'
    row = only_call()
    assert (row.accepted, row.reason, row.status) == (False, 'unavailable', 503)


@pytest.mark.django_db
def test_other_methods_and_paths_are_logged_unchecked(client):
    response = client.post(PING, HTTP_AUTHORIZATION=signed(method='POST'))
    assert response.status_code == 405
    assert client.get('/api/v1/nope').status_code == 404
    rows = InterfaceCall.objects.order_by('received_at', 'pk')
    assert [(r.method, r.path, r.reason, r.status, r.accepted) for r in rows] == [
        ('POST', PING, 'not_checked', 405, False),
        ('GET', '/api/v1/nope', 'not_checked', 404, False)]


@pytest.mark.django_db
def test_only_api_paths_are_logged(client):
    client.get('/healthz')
    client.get('/en/')
    assert InterfaceCall.objects.count() == 0


@pytest.mark.django_db
def test_a_failed_log_write_does_not_break_the_answer(client, monkeypatch, caplog):
    def broken(**kwargs):
        raise RuntimeError('database down')
    monkeypatch.setattr(InterfaceCall.objects, 'create', broken)
    assert call(client, authorization=signed()).status_code == 200
    assert 'interface call log write failed (RuntimeError)' in caplog.text


@pytest.mark.django_db
def test_claimed_identifiers_are_sanitized():
    class Request:
        path = PING
        method = 'GET'
        META = {'REMOTE_ADDR': '203.0.113.7'}
    request = Request()
    calls._outcome(request, False, 'signature', 'bad iss\nline', 'k' * 80)
    assert request.interface_outcome['iss'] == '?' and request.interface_outcome['kid'] == '?'


@pytest.mark.django_db
def test_503_is_logged_once_as_a_warning_not_an_error(client, settings, caplog):
    settings.INTERFACE_SIGNING_KEY = ''
    caplog.set_level(logging.DEBUG)
    assert call(client, authorization=signed()).status_code == 503
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert [r.getMessage() for r in caplog.records if r.name == 'integration.calls'
            and r.getMessage().startswith('interface unavailable')] != []


@pytest.mark.django_db
def test_an_unexpected_error_is_logged_with_reason_error(client, monkeypatch):
    from django.test import Client

    def broken(*args, **kwargs):
        raise RuntimeError('cache down')
    monkeypatch.setattr(interface, 'verify_django_request', broken)
    response = Client(raise_request_exception=False).get(
        PING, HTTP_AUTHORIZATION=signed(), REMOTE_ADDR='203.0.113.7')
    assert response.status_code == 500
    row = only_call()
    assert (row.accepted, row.reason, row.status) == (False, 'error', 500)


# --- ping_peer ---------------------------------------------------------------------------

class FakeAdmin:
    """admindev at mytp_core.interface._open: checks the request as the admin site does
    (dev's public key, audience admindev) and answers its ping."""

    def __init__(self):
        self.ring = interface.Keyring.from_entries([('dev', 'p1', PORTAL.public_text)])
        self.requests = []
        self.error = None

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise self.error
        parts = urlsplit(request.full_url)
        try:
            interface.verify_request(request.get_header('Authorization'), request.method,
                                     interface.request_target(parts.path, parts.query), b'',
                                     keyring=self.ring, audience='admindev')
        except interface.VerificationFailed as exc:
            raise urllib.error.HTTPError(
                request.full_url, 401, 'Unauthorized', {},
                io.BytesIO(json.dumps(interface.refusal_body(exc.code)).encode()))
        body = json.dumps({'site': 'admindev', 'region': None, 'time': 'now'}).encode()
        response = io.BytesIO(body)
        response.status = 200
        return response


@pytest.fixture
def admin(monkeypatch):
    fake = FakeAdmin()
    monkeypatch.setattr(interface, '_open', fake)
    return fake


@pytest.mark.django_db
def test_ping_peer_prints_the_answer(admin):
    out = io.StringIO()
    call_command('ping_peer', 'admindev', stdout=out)
    assert json.loads(out.getvalue()) == {'site': 'admindev', 'region': None, 'time': 'now'}
    assert admin.requests[0].full_url == 'https://admindev.example.test/api/v1/ping'


@pytest.mark.django_db
@pytest.mark.parametrize('setup, message', [
    (lambda s, a: setattr(a, 'error', urllib.error.URLError(TimeoutError())),
     'ping_peer admindev: no answer: timed out'),
    (lambda s, a: setattr(a, 'ring', interface.Keyring()), r'HTTP 401 \(unknown_key\)'),
    (lambda s, a: setattr(s, 'INTERFACE_SIGNING_KEY', ''), 'No signing key'),
    (lambda s, a: setattr(s, 'INTERFACE_PEERS', ''), "'admindev' is not in INTERFACE_PEERS"),
    (lambda s, a: setattr(s, 'INTERFACE_PEERS', 'admindev=ftp://x'), 'entry 1 is not'),
])
def test_ping_peer_fails_cleanly(admin, settings, setup, message):
    setup(settings, admin)
    out = io.StringIO()
    with pytest.raises(CommandError, match=message) as error:
        call_command('ping_peer', 'admindev', stdout=out)
    assert error.value.__cause__ is None
    assert out.getvalue() == ''
    assert all(secret not in str(error.value) for secret in SEED_TEXTS)


def test_ping_peer_exits_non_zero_without_a_traceback(settings, capsys):
    """As run from the shell: one line on stderr, exit status 1."""
    from django.core.management import ManagementUtility
    settings.INTERFACE_PEERS = ''
    with pytest.raises(SystemExit) as exit_info:
        ManagementUtility(['manage.py', 'ping_peer', 'admindev']).execute()
    assert exit_info.value.code == 1
    err = capsys.readouterr().err
    assert "CommandError: ping_peer admindev: 'admindev' is not in INTERFACE_PEERS" in err
    assert 'Traceback' not in err


def test_parse_peers(settings):
    assert parse_peers('admindev=https://a.example/') == {'admindev': 'https://a.example'}
    assert parse_peers('') == {}
    with pytest.raises(interface.InterfaceConfigurationError, match='http:// only with DEBUG'):
        parse_peers('x=http://127.0.0.1:8001')
    settings.DEBUG = True
    assert parse_peers(' admindev=https://a.example/ , x=http://127.0.0.1:8001') == {
        'admindev': 'https://a.example', 'x': 'http://127.0.0.1:8001'}


def test_ping_peer_failure_is_one_line(settings, caplog):
    settings.INTERFACE_PEERS = ''
    with pytest.raises(CommandError):
        call_command('ping_peer', 'admindev', stdout=io.StringIO())
    assert [r for r in caplog.records if r.name == 'integration.calls'] == []


@pytest.mark.parametrize('name, value, check_id', [
    ('INTERFACE_PEER_KEYS', 'admindev:a1', 'integration.E003'),
    ('INTERFACE_PEER_KEYS', f'admindev:a1:{ADMIN.public_text},', 'integration.E003'),
    ('INTERFACE_PEERS', 'admindev=https://a.example,', 'integration.E002'),
    ('INTERFACE_PEERS', 'admindev=http://a.example', 'integration.E002'),
    ('INTERFACE_SIGNING_KEY', 'p1:not-a-key', 'integration.E001'),
    ('INTERFACE_SIGNING_KMS_KEY', 'k1:W', 'integration.E001'),  # both set
])
def test_system_check_reports_malformed_interface_settings(settings, name, value, check_id):
    from django.core.checks import run_checks
    from integration.checks import check_interface_settings
    assert check_interface_settings() == []
    setattr(settings, name, value)
    errors = check_interface_settings()
    assert [error.id for error in errors] == [check_id]
    assert check_id in {error.id for error in run_checks()}


def test_system_check_accepts_no_key(settings):
    from integration.checks import check_interface_settings
    settings.INTERFACE_SIGNING_KEY = ''
    settings.INTERFACE_PEERS = ''
    settings.INTERFACE_PEER_KEYS = ''
    assert check_interface_settings() == []


# --- Purge and deploy files -------------------------------------------------------------

@pytest.mark.django_db
def test_purge_deletes_calls_older_than_90_days():
    now = timezone.now()
    for days in (91, 89):
        InterfaceCall.objects.create(received_at=now - timedelta(days=days), method='GET',
                                     path=PING, status=200)
    out = io.StringIO()
    call_command('purge_interface_calls', stdout=out)
    assert out.getvalue() == 'purge_interface_calls: 1 calls deleted\n'
    assert [row.received_at for row in InterfaceCall.objects.all()] == [now - timedelta(days=89)]


def test_purge_job_runs_daily():
    from mytp_core import jobs
    job = jobs.load_jobs(DEPLOY / 'jobs.conf')['purgeinterfacecalls']
    assert (job.command, job.args) == ('purge_interface_calls', ())
    assert job.schedule == '*-*-* 04:20:00 UTC'


def test_nginx_serves_api_without_basic_auth_and_1mb_bodies():
    conf = (DEPLOY / 'nginx' / 'site.conf.template').read_text()
    block = conf.split('location /api/ {', 1)[1].split('}', 1)[0]
    assert 'auth_basic off;' in block
    assert 'client_max_body_size 1m;' in block
    assert 'proxy_pass http://mytp-__SITE__-gunicorn;' in block
    assert 'proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;' in block
