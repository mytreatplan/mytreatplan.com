"""A logged-in user on the signup pages (signup retrospective R2,
plan-fix-signup-logged-in-and-idle-cancel): sent to the welcome page, nothing saved."""
import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from eventlog.models import Attempt
from signup.models import PendingRegistration
from signup.views import ATTEMPT_SESSION_KEY, SESSION_KEY

from .helpers import NORMALIZED, _legal, _step1

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()

WELCOME_URL = '/en/welcome/'


@pytest.fixture
def logged_in(client):
    client.force_login(User.objects.create_user('ana@example.com', 'a-password-1234'))
    return client


@pytest.mark.parametrize('url', ['/en/signup/', '/en/signup/legal/', '/en/signup/resume/',
                                 '/en/signup/cancel/'])
def test_signup_pages_send_a_logged_in_user_to_welcome(logged_in, url):
    assert logged_in.get(url)['Location'] == WELCOME_URL
    assert logged_in.post(url)['Location'] == WELCOME_URL


def test_step1_post_creates_nothing(logged_in):
    assert _step1(logged_in)['Location'] == WELCOME_URL
    assert not PendingRegistration.objects.exists()
    assert not Attempt.objects.exists()
    assert User.objects.count() == 1
    # Still the same user.
    assert logged_in.get(WELCOME_URL).wsgi_request.user.email == 'ana@example.com'


def test_live_email_check_answers_nothing(logged_in):
    assert _step1(Client())['Location'] == '/en/signup/legal/'  # a signup elsewhere
    response = logged_in.post('/en/signup/check-email/', {'email': NORMALIZED})
    assert response.status_code == 200
    assert 'data-resume-offer' not in response.content.decode()
    assert not Attempt.objects.get().events.filter(event='pending_found').exists()


def test_logging_in_mid_signup_stops_the_wizard(client):
    _step1(client)
    client.force_login(User.objects.create_user('ana@example.com', 'a-password-1234'))
    assert _legal(client)['Location'] == WELCOME_URL
    assert not PendingRegistration.objects.get().legal_choices.exists()
    assert client.post('/en/signup/cancel/')['Location'] == WELCOME_URL
    assert PendingRegistration.objects.exists()  # not cancelled either
    assert Attempt.objects.get().ended_at is None
    assert {SESSION_KEY, ATTEMPT_SESSION_KEY} <= set(client.session.keys())
