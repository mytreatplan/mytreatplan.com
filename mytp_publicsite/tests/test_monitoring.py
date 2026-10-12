"""Story 1.9: scheduled jobs, monitoring and alerts. /healthz, the Sentry init and its
scrubbing, the job runner (`manage.py runjob`) and the deploy files for both."""
import io
import json
import logging
import re
import runpy

import pytest
import sentry_sdk
from django.conf import settings as django_settings
from django.core.management import CommandError, call_command, get_commands
from django.core.signals import request_finished, request_started
from django.db import IntegrityError, OperationalError, close_old_connections
from django.core.wsgi import get_wsgi_application
from django.test import override_settings
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from sentry_sdk.transport import Transport

import mytp_publicsite.views
from mytp_publicsite import jobs
from mytp_publicsite.management.commands import runjob
from mytreatplan import sentry as mytp_sentry

REPO = django_settings.BASE_DIR
DEPLOY = REPO / 'deploy'
SETTINGS_FILE = str(REPO / 'mytreatplan' / 'settings.py')


# --- /healthz -------------------------------------------------------------------------
# The test database is not needed: the view's query is faked (SELECT 1 needs no tables).

class _FakeCursor:
    def __init__(self, executed):
        self.executed = executed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.executed.append(sql)

    def fetchone(self):
        return (1,)


@pytest.fixture
def db_up(monkeypatch):
    executed = []
    monkeypatch.setattr(mytp_publicsite.views.connection, 'cursor', lambda: _FakeCursor(executed))
    return executed


def test_healthz_ok(client, db_up):
    response = client.get('/healthz')

    assert response.status_code == 200
    assert response.content == b'ok'
    assert response['Content-Type'].startswith('text/plain')
    assert 'no-cache' in response['Cache-Control']
    assert db_up == ['SELECT 1']


def test_healthz_db_down(client, monkeypatch):
    def broken_cursor():
        raise OperationalError('connection to server at "db.internal" failed: secret detail')
    monkeypatch.setattr(mytp_publicsite.views.connection, 'cursor', broken_cursor)

    response = client.get('/healthz')

    assert response.status_code == 503
    assert response.content == b'unavailable'


def test_healthz_head_allowed_post_refused(client, db_up):
    assert client.head('/healthz').status_code == 200
    assert client.post('/healthz').status_code == 405


@override_settings(ROBOTS_NOINDEX=True)
def test_healthz_is_noindex(client, db_up):
    assert client.get('/healthz')['X-Robots-Tag'] == 'noindex, nofollow'


def test_healthz_is_root_only_and_not_in_sitemap(client):
    assert client.get('/en/healthz').status_code == 404
    assert b'healthz' not in client.get('/sitemap.xml').content


# --- Sentry init ----------------------------------------------------------------------

@pytest.fixture
def init_calls(monkeypatch):
    """Record sentry_sdk.init calls instead of starting a real client."""
    calls = []
    monkeypatch.setattr(sentry_sdk, 'init', lambda **kwargs: calls.append(kwargs))
    return calls


def _load_settings(monkeypatch, **env):
    for name in ('SENTRY_DSN', 'SENTRY_ENVIRONMENT', 'RELEASE_SHA'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('SENTRY_DSN', '')  # beats a developer's settings.env
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return runpy.run_path(SETTINGS_FILE)


def test_sentry_off_without_dsn(monkeypatch, init_calls):
    s = _load_settings(monkeypatch)

    assert s['SENTRY_DSN'] == ''
    assert init_calls == []


def test_sentry_off_in_tests():
    assert not sentry_sdk.get_client().is_active()


def test_sentry_on_with_dsn(monkeypatch, init_calls):
    dsn = 'https://public@o1.ingest.de.sentry.io/2'
    _load_settings(monkeypatch, SENTRY_DSN=dsn, SENTRY_ENVIRONMENT='staging',
                   RELEASE_SHA='6b27169cc0f014905d7dbfe73335ef7aa2123d26')

    [kwargs] = init_calls
    assert kwargs['dsn'] == dsn
    assert kwargs['environment'] == 'staging'
    assert kwargs['release'] == '6b27169cc0f014905d7dbfe73335ef7aa2123d26'
    assert kwargs['send_default_pii'] is False
    assert kwargs['before_send'] is mytp_sentry.scrub_event
    assert kwargs['max_request_body_size'] == 'never'
    assert kwargs['include_local_variables'] is False


def test_scrub_event_removes_personal_data():
    event = {
        'request': {
            'method': 'POST',
            'url': 'https://dev.mytpdso.com/en/contact/?email=ana%40example.com',
            'query_string': 'email=ana%40example.com',
            'data': {'email': 'ana@example.com', 'message': 'my teeth'},
            'cookies': {'sessionid': 'abc'},
            'headers': {'Cookie': 'sessionid=abc', 'X-Forwarded-For': '203.0.113.9'},
            'env': {'REMOTE_ADDR': '203.0.113.9'},
        },
        'user': {'id': 7, 'email': 'ana@example.com', 'ip_address': '203.0.113.9',
                 'username': 'ana'},
        'breadcrumbs': {'values': [
            {'category': 'httplib', 'data': {'url': 'https://api.example.com/x?token=t',
                                             'http.query': 'token=t'}},
        ]},
    }

    scrubbed = mytp_sentry.scrub_event(event, {})

    assert scrubbed['request'] == {'method': 'POST',
                                   'url': 'https://dev.mytpdso.com/en/contact/'}
    assert scrubbed['user'] == {'id': 7}
    assert scrubbed['breadcrumbs']['values'][0]['data'] == {'url': 'https://api.example.com/x'}
    text = json.dumps(scrubbed)
    for secret in ('ana@example.com', 'ana%40example.com', '203.0.113.9', 'abc', 'my teeth'):
        assert secret not in text


def test_scrub_event_drops_user_without_id():
    assert 'user' not in mytp_sentry.scrub_event({'user': {'ip_address': '203.0.113.9'}})


class _CaptureTransport(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.events = []

    def capture_envelope(self, envelope):
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)


@csrf_exempt
def _boom(request):
    raise ValueError('forced test error')


urlpatterns = [path('boom/', _boom)]


@pytest.fixture
def sentry_capture(monkeypatch):
    """A real Sentry client (via init_sentry) whose events stay in memory."""
    transport = _CaptureTransport()
    real_init = sentry_sdk.init
    monkeypatch.setattr(sentry_sdk, 'init',
                        lambda **kwargs: real_init(transport=transport, **kwargs))
    mytp_sentry.init_sentry('https://public@o1.ingest.de.sentry.io/2', 'test', 'abc123')
    yield transport
    sentry_sdk.get_global_scope().set_client(None)


@override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=['dev.mytpdso.com'],
                   SECURE_SSL_REDIRECT=False, DEBUG_PROPAGATE_EXCEPTIONS=False)
def test_request_error_reaches_sentry_scrubbed(sentry_capture):
    """Through the real WSGI application, as Gunicorn calls it, so Sentry attaches
    everything it normally would (URL, query string, headers, cookies, client IP)."""
    body = b'email=ana%40example.com&message=private'
    environ = {
        'REQUEST_METHOD': 'POST',
        'PATH_INFO': '/boom/',
        'QUERY_STRING': 'email=ana%40example.com',
        'SERVER_NAME': 'dev.mytpdso.com',
        'SERVER_PORT': '443',
        'SERVER_PROTOCOL': 'HTTP/1.1',
        'REMOTE_ADDR': '198.51.100.7',
        'HTTP_HOST': 'dev.mytpdso.com',
        'HTTP_COOKIE': 'sessionid=cookie-value-123',
        'HTTP_X_FORWARDED_FOR': '203.0.113.9',
        'HTTP_X_FORWARDED_PROTO': 'https',
        'CONTENT_TYPE': 'application/x-www-form-urlencoded',
        'CONTENT_LENGTH': str(len(body)),
        'wsgi.input': io.BytesIO(body),
        'wsgi.errors': io.StringIO(),
        'wsgi.url_scheme': 'https',
        'wsgi.version': (1, 0),
        'wsgi.multithread': False,
        'wsgi.multiprocess': True,
        'wsgi.run_once': False,
    }
    statuses = []
    # Like Django's test client: the handler must not close database connections that
    # earlier (database) tests opened, which pytest-django blocks in this test.
    request_started.disconnect(close_old_connections)
    request_finished.disconnect(close_old_connections)
    try:
        b''.join(get_wsgi_application()(environ,
                                        lambda status, headers, *a: statuses.append(status)))
    finally:
        request_started.connect(close_old_connections)
        request_finished.connect(close_old_connections)
    sentry_sdk.flush()

    assert statuses[0].startswith('500')
    [event] = [e for e in sentry_capture.events if e.get('exception')]
    assert event['environment'] == 'test'
    assert event['release'] == 'abc123'
    assert event['request'] == {'method': 'POST', 'url': 'https://dev.mytpdso.com/boom/'}
    text = json.dumps(event)
    for secret in ('ana@example.com', 'ana%40example.com', 'private', 'cookie-value-123',
                   '203.0.113.9', '198.51.100.7'):
        assert secret not in text


def test_redact_keeps_times_and_versions():
    text = 'run at 03:30:00 UTC, sentry-sdk 2.71.0, hash deadbeef'
    assert mytp_sentry.redact(text) == text


def test_error_messages_and_logs_reach_sentry_redacted(sentry_capture):
    detail = ('duplicate key value violates unique constraint "accounts_user_email_key"\n'
              'DETAIL:  Key (email)=(ana@example.com) already exists. '
              'Client 203.0.113.9 / 2001:db8::7')
    try:
        raise IntegrityError(detail)
    except IntegrityError:
        sentry_sdk.capture_exception()
    logging.getLogger('mytp.test').error('signup failed for %s from %s',
                                         'bob@example.org', '198.51.100.7')
    sentry_sdk.flush()

    events = sentry_capture.events
    assert any(e.get('exception') for e in events)
    assert any(e.get('logentry') for e in events)
    for event in events:
        assert 'params' not in (event.get('logentry') or {})
    # Everything except stack frames, whose source context is this test's own code.
    text = json.dumps([
        {**e, 'exception': [{k: v for k, v in x.items() if k != 'stacktrace'}
                            for x in (e.get('exception') or {}).get('values', [])]}
        for e in events])
    assert '[redacted]' in text
    for secret in ('ana@example.com', 'bob@example.org', '203.0.113.9', '2001:db8::7',
                   '198.51.100.7'):
        assert secret not in text


def test_verification_codes_never_reach_sentry(sentry_capture):
    """The fake provider's code line is neither a breadcrumb nor an event (signup 2.1)."""
    from signup.verification import FakeVerificationProvider

    code_logger = logging.getLogger('signup.verification')
    seen = []
    handler = logging.Handler()
    handler.emit = seen.append
    code_logger.addHandler(handler)
    try:
        FakeVerificationProvider().send('email', 'ana@example.com', 'signup', 'en')
        code_logger.error('an error on the code logger')
    finally:
        code_logger.removeHandler(handler)
    code = re.search(r'(\d{6})$', seen[0].getMessage()).group(1)
    try:
        raise ValueError('later error in the same request')
    except ValueError:
        sentry_sdk.capture_exception()
    sentry_sdk.flush()

    assert any(e.get('exception') for e in sentry_capture.events)
    # Everything except stack frames, whose source context is this test's own code.
    text = json.dumps([
        {**e, 'exception': [{k: v for k, v in x.items() if k != 'stacktrace'}
                            for x in (e.get('exception') or {}).get('values', [])]}
        for e in sentry_capture.events])
    assert 'Verification code' not in text and 'an error on the code logger' not in text
    assert code not in text


# --- Jobs: jobs.conf and the runner ---------------------------------------------------

def test_jobs_conf_declares_clearsessions_daily():
    declared = jobs.load_jobs(jobs.JOBS_FILE)

    job = declared['clearsessions']
    assert (job.command, job.args) == ('clearsessions', ())
    assert job.crontab == '30 3 * * *'


@pytest.mark.parametrize('schedule,crontab', [
    ('*-*-* 03:30:00 UTC', '30 3 * * *'),
    ('*-*-* 3:05 UTC', '5 3 * * *'),
    ('*-*-* *:15:00 UTC', '15 * * * *'),
    ('Mon *-*-* 02:00:00 UTC', '0 2 * * 1'),
    ('Mon..Fri *-*-* 06:00 UTC', '0 6 * * 1-5'),
    ('Sat,Sun *-*-* 06:00 UTC', '0 6 * * 6,0'),
    ('*-*-01 04:00:00 UTC', '0 4 1 * *'),
    ('Mon..Sat,Sun *-*-* 06:00 UTC', '0 6 * * 1-6,0'),
])
def test_oncalendar_to_crontab(schedule, crontab):
    assert jobs.oncalendar_to_crontab(schedule) == crontab


@pytest.mark.parametrize('schedule', [
    'daily', '*-*-* 03:30:00', '*-*-* 03:30:10 UTC', '*-*-* 25:00 UTC',
    '*-*-31 03:00 UTC', 'Funday *-*-* 03:00 UTC', '*-*-* 03:30:00 Europe/Madrid',
])
def test_oncalendar_unsupported(schedule):
    with pytest.raises(ValueError):
        jobs.oncalendar_to_crontab(schedule)


@pytest.mark.parametrize('dow', ['Mon..Sun', 'Sat..Sun', 'Fri..Mon', 'Wed..Wed'])
def test_oncalendar_rejects_wrapping_weekday_ranges(dow):
    with pytest.raises(ValueError, match='list Sunday'):
        jobs.oncalendar_to_crontab(f'{dow} *-*-* 06:00 UTC')


def test_jobs_file_prefers_provisioned_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'INSTALLED_JOBS_FILE', tmp_path / 'jobs.conf')
    assert jobs.jobs_file() == jobs.JOBS_FILE  # not provisioned: the release's copy

    (tmp_path / 'jobs.conf').write_text('clearsessions | clearsessions | *-*-* 05:00 UTC\n')

    assert jobs.jobs_file() == tmp_path / 'jobs.conf'
    assert jobs.load_jobs()['clearsessions'].crontab == '0 5 * * *'


@pytest.mark.parametrize('text', [
    'clearsessions | clearsessions',
    'Bad_Name | clearsessions | *-*-* 03:30 UTC',
    'a | clearsessions | *-*-* 03:30 UTC\na | clearsessions | *-*-* 04:30 UTC',
    'a |  | *-*-* 03:30 UTC',
])
def test_parse_jobs_rejects_bad_lines(text):
    with pytest.raises(ValueError):
        jobs.parse_jobs(text)


def test_parse_jobs_splits_arguments_and_skips_comments():
    parsed = jobs.parse_jobs('# comment\n\nx | cmd --flag "a b" | *-*-* 01:00 UTC\n')
    assert parsed['x'].command == 'cmd'
    assert parsed['x'].args == ('--flag', 'a b')


class _FakeSentry:
    def __init__(self, active):
        self.active = active
        self.exceptions = 0
        self.flushed = False

    def get_client(self):
        return self

    def is_active(self):
        return self.active

    def capture_exception(self):
        self.exceptions += 1

    def flush(self, timeout=None):
        self.flushed = True


@pytest.fixture
def job_env(monkeypatch, settings):
    """runjob with a fake Sentry and a recorded inner command."""
    settings.SENTRY_ENVIRONMENT = 'staging'
    checkins, ran = [], []
    fake = _FakeSentry(active=True)

    def capture_checkin(**kwargs):
        checkins.append(kwargs)
        return kwargs.get('check_in_id') or 'checkin-1'

    def inner(command, *args):
        ran.append((command, args))
        if command == 'failing':
            raise CommandError('job failed')

    monkeypatch.setattr(runjob, 'sentry_sdk', fake)
    monkeypatch.setattr(runjob, 'capture_checkin', capture_checkin)
    monkeypatch.setattr(runjob, 'call_command', inner)
    monkeypatch.setattr(runjob, 'load_jobs', lambda: jobs.parse_jobs(
        'clearsessions | clearsessions | *-*-* 03:30:00 UTC\n'
        'broken | failing --now | *-*-* 04:00 UTC\n'))
    return fake, checkins, ran


def test_runjob_ok_checks_in(job_env):
    fake, checkins, ran = job_env

    call_command('runjob', 'clearsessions')

    assert ran == [('clearsessions', ())]
    assert [c['status'] for c in checkins] == ['in_progress', 'ok']
    assert {c['monitor_slug'] for c in checkins} == {'mytp-staging-clearsessions'}
    assert checkins[1]['check_in_id'] == 'checkin-1'
    config = checkins[0]['monitor_config']
    assert config['schedule'] == {'type': 'crontab', 'value': '30 3 * * *'}
    assert config['timezone'] == 'UTC'
    assert fake.flushed


def test_runjob_failure_checks_in_error_and_exits_non_zero(job_env):
    fake, checkins, ran = job_env

    with pytest.raises(CommandError):
        call_command('runjob', 'broken')

    assert ran == [('failing', ('--now',))]
    assert [c['status'] for c in checkins] == ['in_progress', 'error']
    assert checkins[1]['monitor_slug'] == 'mytp-staging-broken'
    assert fake.exceptions == 1


def test_runjob_without_sentry_sends_nothing(job_env):
    fake, checkins, ran = job_env
    fake.active = False

    call_command('runjob', 'clearsessions')

    assert ran == [('clearsessions', ())]
    assert checkins == []


def test_runjob_unknown_job(job_env):
    with pytest.raises(CommandError, match='unknown job'):
        call_command('runjob', 'nope')


def test_jobs_conf_commands_exist():
    available = get_commands()
    for job in jobs.load_jobs(jobs.JOBS_FILE).values():
        assert job.command in available, job.name


# --- Deploy files ---------------------------------------------------------------------

def test_nginx_healthz_without_basic_auth():
    https = (DEPLOY / 'nginx' / 'mytp.conf.template').read_text().split('\n# __HTTPS_BEGIN__\n', 1)[1]
    block = re.search(r'location = /healthz \{(.*?)\}', https, re.S)
    assert block, 'no exact-match /healthz location in the HTTPS server'
    body = block.group(1)
    assert 'auth_basic off;' in body
    assert 'proxy_pass http://mytp_gunicorn;' in body
    assert 'proxy_set_header Host $host;' in body
    assert 'proxy_set_header X-Forwarded-Proto $scheme;' in body
    # Everything else keeps the password.
    assert https.count('auth_basic off;') == 2  # ACME challenge and /healthz


def test_job_service_unit():
    unit = (DEPLOY / 'systemd' / 'mytp-job@.service').read_text()
    assert 'Type=oneshot' in unit
    assert 'User=mytp' in unit and 'Group=mytp' in unit
    assert 'ExecStart=/bin/bash /srv/mytp/current/deploy/run-job.sh %i' in unit


def test_job_timer_template():
    timer = (DEPLOY / 'systemd' / 'mytp-job.timer.template').read_text()
    assert 'OnCalendar=__SCHEDULE__' in timer
    assert 'Persistent=true' in timer
    assert 'Unit=mytp-job@__NAME__.service' in timer
    assert 'WantedBy=timers.target' in timer


def test_run_job_script_runs_runjob_in_current():
    script = (DEPLOY / 'run-job.sh').read_text()
    assert 'cd -P "$APP_HOME/current"' in script
    assert 'manage.py runjob "$NAME"' in script
    assert 'shared/settings.env' in script


def test_provision_installs_job_timers():
    script = (DEPLOY / 'provision.sh').read_text()
    assert '"$DEPLOY_DIR/jobs.conf"' in script
    assert 'mytp-job@.service' in script
    assert 'mytp-job.timer.template' in script
    assert 'disable --now "mytp-job@$name.timer"' in script
    assert ('install_file "$DEPLOY_DIR/jobs.conf" "$APP_HOME/shared/jobs.conf" 0644 '
            '"$APP_USER:$APP_USER"') in script


def test_release_writes_revision():
    assert 'echo "$SHA" >"$RELEASE/REVISION"' in (DEPLOY / 'release.sh').read_text()


def test_release_warns_when_jobs_conf_not_provisioned():
    script = (DEPLOY / 'release.sh').read_text()
    assert 'cmp -s "$RELEASE/deploy/jobs.conf" "$SHARED/jobs.conf"' in script
    assert 're-run deploy/provision.sh' in script


def test_staging_env_example_lists_sentry_without_secret():
    values = dict(
        line.split('=', 1) for line in (DEPLOY / 'staging.env.example').read_text().splitlines()
        if line.strip() and not line.startswith('#')
    )
    assert values['SENTRY_DSN'] == ''
    assert values['SENTRY_ENVIRONMENT'] == 'staging'


def test_sentry_sdk_pinned():
    lines = (REPO / 'requirements.txt').read_text().splitlines()
    assert any(re.fullmatch(r'sentry-sdk==\d+\.\d+\.\d+', line) for line in lines)
