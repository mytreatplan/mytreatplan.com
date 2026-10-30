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

import mytp_publicsite.views
from mytp_publicsite import jobs
from mytp_publicsite.management.commands import runjob
from mytreatplan import sentry as mytp_sentry
from signup.twilio_verify import TWILIO_PROVIDER

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


# A view that fails, for the test below. Sentry sends the source lines around its `raise`
# as the frame's context, so no personal value may be written near it.


@csrf_exempt
def _boom(request):
    raise ValueError('forced test error')


urlpatterns = [path('boom/', _boom)]


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


# Phone numbers (QA A16): the forms the portal stores and shows.
PHONES = ('+34 622 918 374', '622918374', '+34622918374', '+1 (415) 555-2671',
          '+34-622-918-374')


@pytest.mark.parametrize('phone', PHONES)
def test_redact_phone_numbers(phone):
    assert mytp_sentry.redact(f'send failed to {phone}.') == 'send failed to [redacted].'
    assert mytp_sentry.redact(f'Key (phone)=({phone})') == 'Key (phone)=([redacted])'


@pytest.mark.parametrize('text', [
    'HTTP 404 then 500 for id 1234',  # statuses and short IDs
    'status 200 301 404 503',  # space-separated groups without a +
    'price 1234.56 EUR, order 12345678',  # 8 digits at most
    'pk=42 took 3.14159265358 s',  # a decimal
    'uuid 123e4567-e89b-12d3-a456-426614174000',
    'Twilio error 60200 (HTTP 400)',
])
def test_redact_keeps_short_numbers(text):
    assert mytp_sentry.redact(text) == text


def test_scrub_event_redacts_phone_numbers_in_breadcrumbs():
    event = {'breadcrumbs': {'values': [
        {'category': 'log', 'message': 'code sent to +34 622 918 374 (whatsapp)'}]}}

    scrubbed = mytp_sentry.scrub_event(event, {})

    assert scrubbed['breadcrumbs']['values'][0]['message'] == (
        'code sent to [redacted] (whatsapp)')


def test_error_messages_and_logs_reach_sentry_redacted(sentry_capture):
    detail = ('duplicate key value violates unique constraint "accounts_user_email_key"\n'
              'DETAIL:  Key (email)=(ana@example.com) already exists. '
              'Client 203.0.113.9 / 2001:db8::7, phone +34622918374')
    try:
        raise IntegrityError(detail)
    except IntegrityError:
        sentry_sdk.capture_exception()
    logging.getLogger('mytp.test').error('signup failed for %s from %s (%s)',
                                         'bob@example.org', '198.51.100.7', '622918374')
    sentry_sdk.capture_message('code not sent to +34 622 918 374 (HTTP 429)')
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
                   '198.51.100.7', '+34622918374', '622918374', '622 918 374'):
        assert secret not in text
    # A status stays readable.
    assert 'HTTP 429' in text


@pytest.mark.django_db
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


@pytest.mark.django_db
def test_failed_code_send_reaches_sentry_without_personal_data(sentry_capture):
    """Signup entry 7: a failed send is an error event (Sentry alerts on spikes), naming
    the channel and the purpose only."""
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    from signup.steps import FAILED, send_code
    from signup.verification import FAKE_SEND_FAILS_NUMBER, WHATSAPP

    request = RequestFactory().post('/en/signup/verify-mobile/')
    request.session = SessionStore()
    outcome = send_code(request, None, 'verify-mobile', FAKE_SEND_FAILS_NUMBER, 'signup',
                        WHATSAPP)
    sentry_sdk.flush()

    assert outcome.status == FAILED
    text = json.dumps(sentry_capture.events)
    assert 'Signup code send failed' in text and 'whatsapp' in text
    assert FAKE_SEND_FAILS_NUMBER not in text and FAKE_SEND_FAILS_NUMBER[1:] not in text


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
    https = (DEPLOY / 'nginx' / 'site.conf.template').read_text().split('\n# __HTTPS_BEGIN__\n', 1)[1]
    block = re.search(r'location = /healthz \{(.*?)\}', https, re.S)
    assert block, 'no exact-match /healthz location in the HTTPS server'
    body = block.group(1)
    assert 'auth_basic off;' in body
    assert 'proxy_pass http://mytp-__SITE__-gunicorn;' in body
    assert 'proxy_set_header Host $host;' in body
    assert 'proxy_set_header X-Forwarded-Proto $scheme;' in body
    # Everything else keeps the password.
    assert https.count('auth_basic off;') == 2  # ACME challenge and /healthz


def test_job_service_unit():
    unit = (DEPLOY / 'systemd' / 'job@.service.template').read_text()
    assert 'Type=oneshot' in unit
    assert 'User=mytp-__SITE__' in unit and 'Group=mytp-__SITE__' in unit
    assert 'ExecStart=/bin/bash /srv/mytp-__SITE__/current/deploy/run-job.sh __SITE__ %i' in unit


def test_job_timer_template():
    timer = (DEPLOY / 'systemd' / 'job.timer.template').read_text()
    assert 'OnCalendar=__SCHEDULE__' in timer
    assert 'Persistent=true' in timer
    assert 'Unit=mytp-__SITE__-job@__NAME__.service' in timer
    assert 'WantedBy=timers.target' in timer


def test_run_job_script_runs_runjob_in_current():
    script = (DEPLOY / 'run-job.sh').read_text()
    assert 'cd -P "$APP_HOME/current"' in script
    assert 'manage.py runjob "$NAME"' in script
    assert 'shared/settings.env' in script


def test_provision_installs_job_timers():
    script = (DEPLOY / 'provision.sh').read_text()
    assert '"$DEPLOY_DIR/jobs.conf"' in script
    assert 'JOB=mytp-$SITE-job' in script
    assert '"$DEPLOY_DIR/systemd/job@.service.template"' in script
    assert '"$DEPLOY_DIR/systemd/job.timer.template"' in script
    assert '"/etc/systemd/system/$JOB@$name.timer"' in script
    assert 'disable --now "$JOB@$name.timer"' in script
    # Unlisted-timer cleanup looks only at this site's timers.
    assert 'for path in "/etc/systemd/system/$JOB@"*.timer; do' in script
    assert 'mytp-job@' not in script.replace('mytp-<site>-job@', '')
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


@pytest.mark.django_db
def test_twilio_send_and_check_failures_reach_sentry_without_secrets(sentry_capture, settings,
                                                                      monkeypatch):
    """Signup entry 11: Twilio errors reach Sentry through the callers' `signup.steps`
    errors, with no contact, code or credential; the provider's own lines stay out."""
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    from signup.forms import MobileCodeForm
    from signup.steps import FAILED, send_code
    from signup.verification import SMS

    secret = 'not-a-real-secret-0123456789abcdef'
    phone, code = '+34622918374', '482913'
    settings.VERIFICATION_PROVIDER = TWILIO_PROVIDER
    settings.TWILIO_ACCOUNT_SID = 'AC' + '0' * 32
    settings.TWILIO_API_KEY_SID = 'SK' + '0' * 32
    settings.TWILIO_API_KEY_SECRET = secret
    settings.TWILIO_VERIFY_SERVICE_SID = 'VA' + '0' * 32
    answers = iter([(401, {'code': 20003, 'message': f'Authenticate {phone}'}),
                    (503, {'message': f'down for {phone} {code}'})])
    monkeypatch.setattr('signup.twilio_verify.post_verify', lambda path, data: next(answers))

    request = RequestFactory().post('/en/signup/verify-mobile/')
    request.session = SessionStore()
    outcome = send_code(request, None, 'verify-mobile', phone, 'signup', SMS)
    form = MobileCodeForm({'code': code}, phone=phone)
    assert not form.is_valid() and form.errors.as_data()['code'][0].code == 'check_failed'
    sentry_sdk.flush()

    assert outcome.status == FAILED
    text = json.dumps(sentry_capture.events)
    assert 'Signup code send failed' in text and 'Signup code check failed' in text
    # Twilio's error code and HTTP status reach Sentry, to tell the failures apart.
    assert 'code 20003, HTTP 401' in text and 'code none, HTTP 503' in text
    assert 'Twilio Verify' not in text  # the provider's own lines (signup.verification)
    for value in (phone, phone[1:], code, secret, settings.TWILIO_API_KEY_SID,
                  settings.TWILIO_ACCOUNT_SID, settings.TWILIO_VERIFY_SERVICE_SID):
        assert value not in text
