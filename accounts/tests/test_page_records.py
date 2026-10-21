"""Page-request records (epic-login entry 7, CAP-4): every row of the plan's I/O matrix.
Each full page shown to a logged-in user is an event-log record of kind `page` tied to the
account's pk (accounts.page_records)."""
import json
from datetime import timedelta

import pytest
import sentry_sdk
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, connection
from django.http import HttpResponse, JsonResponse
from django.test import Client
from django.urls import path
from django.utils import timezone

from accounts import page_records
from accounts.sessions import IDLE_LIMIT, end_all_sessions
from eventlog import retention
from eventlog.models import Attempt, Event
from signup.tests.conftest import capture_codes, clear_cache  # noqa: F401 - autouse
from signup.tests.conftest import clock  # noqa: F401 - fixture
from signup.tests.helpers import PASSWORD
from signup.tests.urls_portal import urlpatterns as portal_urlpatterns

from .test_sessions import _during_the_view, idle_clock  # noqa: F401 - fixture

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('page_urls')]

User = get_user_model()

WELCOME = '/en/welcome/'
IP = '203.0.113.58'
AGENT = 'Mozilla/5.0 (PageRecordsBrowser)'
META = {'REMOTE_ADDR': IP, 'HTTP_USER_AGENT': AGENT}


# Test-only pages (this module is the test URLconf): a JSON answer, a file, a 403, a 500,
# a plain page that answers 204, and the portal routes.
def _json(request):
    return JsonResponse({'ok': True})


def _file(request):
    return HttpResponse(b'%PDF-1.4', content_type='application/pdf')


def _forbidden(request):
    raise PermissionDenied


def _broken(request):
    raise RuntimeError('broken page')


def _no_content(request):
    return HttpResponse(status=204, content_type='text/html')


urlpatterns = list(portal_urlpatterns) + [
    path('test-pages/json/', _json, name='test_json'),
    path('test-pages/file/', _file, name='test_file'),
    path('test-pages/forbidden/', _forbidden, name='test_forbidden'),
    path('test-pages/broken/', _broken, name='test_broken'),
    path('test-pages/no-content/', _no_content, name='test_no_content'),
]


@pytest.fixture
def page_urls(settings):
    settings.ROOT_URLCONF = __name__


@pytest.fixture
def doctor():
    return User.objects.create_user('doctor.pages@example.net', PASSWORD)


@pytest.fixture
def staff():
    return User.objects.create_superuser('staff.pages@example.net', PASSWORD)


def _browser(user, **kwargs):
    browser = Client(**kwargs)
    browser.force_login(user)
    return browser


def _records():
    return list(Event.objects.filter(attempt__kind=Attempt.PAGE)
                .select_related('attempt').order_by('created_at', 'id'))


# --- Recorded ---------------------------------------------------------------------------

def test_welcome_is_recorded(doctor):
    before = timezone.now()
    response = _browser(doctor).get(WELCOME, **META)

    assert response.status_code == 200
    [record] = _records()
    assert record.attempt.account_id == doctor.pk
    assert record.attempt.outcome == 'page_view' and record.attempt.ended_at >= before
    assert record.event == 'page_view'
    assert record.step == 'welcome'
    assert record.reason == '200'
    assert record.field == ''
    assert record.data == {'path': WELCOME, 'ip': IP, 'user_agent': AGENT}


def test_query_string_is_in_the_encrypted_data_only(doctor):
    _browser(doctor).get(f'{WELCOME}?x=1', **META)

    [record] = _records()
    assert record.data['path'] == f'{WELCOME}?x=1'
    plain = [record.step, record.event, record.field, record.reason,
             record.attempt.kind, record.attempt.outcome]
    assert all('x=1' not in value and WELCOME not in value for value in plain)
    # Encrypted at rest: the stored column holds neither the path nor the IP.
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT data FROM {Event._meta.db_table} WHERE id = %s', [record.pk])
        [stored] = cursor.fetchone()
    assert stored and 'x=1' not in str(stored) and IP not in str(stored)


def test_two_pages_and_an_htmx_request_make_two_records(doctor):
    browser = _browser(doctor)

    browser.get(WELCOME)
    browser.get(WELCOME)
    browser.get(WELCOME, headers={'HX-Request': 'true'})

    records = _records()
    assert len(records) == 2
    assert {record.attempt.account_id for record in records} == {doctor.pk}


@pytest.mark.parametrize('url, status', [
    ('/en/nowhere/', 404),
    ('/test-pages/forbidden/', 403),
    ('/test-pages/broken/', 500),
])
def test_error_page_is_recorded_with_its_status(doctor, url, status):
    browser = _browser(doctor, raise_request_exception=False)

    response = browser.get(url)

    assert response.status_code == status
    assert response['Content-Type'].startswith('text/html')
    [record] = _records()
    assert record.reason == str(status)
    assert record.attempt.account_id == doctor.pk
    assert record.data['path'] == url


def test_unresolved_page_has_no_step(doctor):
    _browser(doctor).get('/en/nowhere/')

    [record] = _records()
    assert record.step == ''


def test_admin_page_is_recorded(staff):
    response = _browser(staff).get('/admin/')

    assert response.status_code == 200
    [record] = _records()
    assert record.attempt.account_id == staff.pk
    assert record.step == 'admin:index'
    assert record.reason == '200'


@pytest.mark.parametrize('header', ['HX-Boosted', 'HX-History-Restore-Request'])
def test_htmx_full_page_load_is_recorded(doctor, header):
    response = _browser(doctor).get(WELCOME, headers={'HX-Request': 'true', header: 'true'})

    assert response.status_code == 200
    [record] = _records()
    assert record.step == 'welcome' and record.reason == '200'
    assert record.attempt.account_id == doctor.pk


# --- Not recorded -----------------------------------------------------------------------

def test_htmx_request_is_not_recorded(doctor):
    response = _browser(doctor).get(WELCOME, headers={'HX-Request': 'true'})

    assert response.status_code == 200
    assert _records() == []


def test_static_file_is_not_recorded(doctor):
    _browser(doctor).get('/static/css/site.css')

    assert _records() == []


@pytest.mark.parametrize('url', [
    '/',  # LocaleMiddleware: to the language prefix
    '/en/welcome',  # CommonMiddleware: the missing slash
    '/admin',
])
def test_redirect_is_not_recorded(doctor, url):
    response = _browser(doctor).get(url)

    assert response.status_code in (301, 302)
    assert _records() == []


def test_other_success_status_is_not_recorded(doctor):
    response = _browser(doctor).get('/test-pages/no-content/')

    assert response.status_code == 204
    assert _records() == []


def test_post_is_not_recorded(doctor):
    response = _browser(doctor).post('/en/logout/')

    assert response.status_code == 302
    assert _records() == []


def test_post_answered_with_a_page_is_not_recorded(doctor):
    response = _browser(doctor).post(WELCOME)

    assert response['Content-Type'].startswith('text/html')
    assert _records() == []


def test_anonymous_visitor_is_not_recorded():
    response = Client().get('/en/login/')

    assert response.status_code == 200
    assert _records() == []


@pytest.mark.parametrize('url', ['/test-pages/json/', '/test-pages/file/', '/robots.txt'])
def test_non_html_response_is_not_recorded(doctor, url):
    response = _browser(doctor).get(url)

    assert response.status_code == 200
    assert _records() == []


def test_session_ended_for_idleness_is_not_recorded(doctor, idle_clock):  # noqa: F811
    browser = _browser(doctor)
    browser.get(WELCOME)
    idle_clock.advance(IDLE_LIMIT + 1)

    response = browser.get(WELCOME)

    assert response.status_code == 302
    assert len(_records()) == 1
    # The login page it lands on is shown to an anonymous visitor.
    assert browser.get('/en/login/').status_code == 200
    assert len(_records()) == 1


def test_session_ended_for_idleness_on_the_login_page_is_not_recorded(doctor,
                                                                        idle_clock):  # noqa: F811
    browser = _browser(doctor)
    idle_clock.advance(IDLE_LIMIT + 1)

    response = browser.get('/en/login/')

    assert response.status_code == 200
    assert _records() == []


def test_session_ended_during_the_view_is_not_recorded(doctor, monkeypatch):
    browser = _browser(doctor)
    _during_the_view(monkeypatch, lambda: end_all_sessions(doctor))

    response = browser.get(WELCOME)

    assert response.status_code == 302
    assert _records() == []


def test_session_replaced_by_another_login_is_not_recorded(doctor):
    older = _browser(doctor)
    _browser(doctor)

    response = older.get(WELCOME)

    assert response.status_code == 302
    assert _records() == []


# --- Retention --------------------------------------------------------------------------

def test_retention_leaves_page_records_intact(doctor):
    _browser(doctor).get(f'{WELCOME}?x=1', **META)
    [record] = _records()
    columns = (record.step, record.event, record.reason, record.data)

    retention.anonymize(now=timezone.now() + timedelta(days=31))
    assert retention.delete_old(now=timezone.now() + timedelta(days=800)) == (0, 0)

    [kept] = _records()
    assert (kept.step, kept.event, kept.reason, kept.data) == columns


# --- Failures ---------------------------------------------------------------------------

def _sentry_text(sentry_capture):
    sentry_sdk.flush()
    return json.dumps(sentry_capture.events)


def test_failed_write_serves_the_page_unchanged(doctor, monkeypatch, sentry_capture):
    browser = _browser(doctor)

    def fail(*args, **kwargs):
        raise DatabaseError(f'insert failed: {WELCOME}?secret=1 from {IP}')
    monkeypatch.setattr(Event.objects, 'create', fail)

    response = browser.get(f'{WELCOME}?secret=1', **META)

    assert response.status_code == 200
    assert response['Content-Type'].startswith('text/html')
    assert 'accounts/welcome.html' in [t.name for t in response.templates]
    assert not Attempt.objects.filter(kind=Attempt.PAGE).exists()
    text = _sentry_text(sentry_capture)
    assert len(sentry_capture.events) == 1
    assert 'DatabaseError' in text and 'page_view' in text
    for secret in ('secret=1', IP, AGENT, 'doctor.pages'):
        assert secret not in text


def test_failure_around_the_write_serves_the_page(doctor, monkeypatch, sentry_capture):
    def fail(request):
        raise ValueError(f'bad {request.get_full_path()}')
    monkeypatch.setattr(page_records, 'url_name', fail)

    response = _browser(doctor).get(f'{WELCOME}?secret=1', **META)

    assert response.status_code == 200
    assert _records() == []
    text = _sentry_text(sentry_capture)
    assert len(sentry_capture.events) == 1
    assert 'ValueError' in text and 'record_page' in text
    for secret in ('secret=1', IP, AGENT):
        assert secret not in text
