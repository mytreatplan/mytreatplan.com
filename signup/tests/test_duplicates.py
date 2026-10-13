"""Signup entry 5: duplicate checks and resume. Every row of the plan's I/O matrix, plus
the event sequence on both attempts."""
import json
import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import Client
from django.urls import path
from django.utils import timezone

from eventlog.models import Attempt
from signup.models import PendingRegistration
from signup.tests import urls_portal
from signup.verification import (EMAIL, MOBILE, PURPOSE_RESUME, PURPOSE_SIGNUP,
                                 FakeVerificationProvider)
from signup.views import ATTEMPT_SESSION_KEY, RESUME_SESSION_KEY, SESSION_KEY

from .test_wizard import MOBILE_E164, NORMALIZED, PASSWORD, _step1, _wrong

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()

# The first signup's own details, which the second one must not see before the code.
OLD = {'first_name': 'Beatriz', 'last_name': 'Olmedo-Saavedra', 'country': 'PT'}
OTHER_EMAIL = 'carla@example.org'
OTHER_MOBILE = '611 223 344'
RECOVERY_NOTE = 'If this account is yours, you can recover access to it'
RESUME_URL = '/en/signup/resume/'

# A URLconf with a `password_reset` route, as epic-login will add (recovery link test).
urlpatterns = list(urls_portal.urlpatterns) + [
    path('password-reset/', lambda request: HttpResponse(), name='password_reset'),
]


def _without_csrf_tokens(html):
    """The page without its CSRF tokens: they are random, and may contain a short value
    checked as absent (e.g. "PT")."""
    return re.sub(r'name="csrfmiddlewaretoken" value="[^"]*"', '', html)


def _code(channel, contact, purpose=PURPOSE_RESUME):
    return FakeVerificationProvider._code(channel, contact, purpose,
                                          FakeVerificationProvider._window())


def _events(attempt):
    return [(e.event, e.step, e.field, e.reason) for e in attempt.events.all()]


def _attempt_of(client):
    return Attempt.objects.get(pk=client.session[ATTEMPT_SESSION_KEY])


@pytest.fixture
def first(client):
    """A signup in progress (step 1 saved) by another browser, with its own names."""
    assert _step1(client, **OLD).status_code == 302
    return PendingRegistration.objects.get()


def _resume(second, channel=EMAIL, contact=NORMALIZED):
    """Send the resume code and enter it."""
    second.post(RESUME_URL, {'action': 'send'})
    return second.post(RESUME_URL, {'code': _code(channel, contact)})


# --- Registered email or mobile ----------------------------------------------------------

def test_registered_email_whatever_its_case(client):
    User.objects.create_user(NORMALIZED.upper(), 'another-password-123')
    response = _step1(client)
    assert response.status_code == 200
    assert response.context['form'].errors == {
        'email': ['This email address is already registered.']}
    assert RECOVERY_NOTE in response.content.decode()
    assert 'data-recovery-link' not in response.content.decode()  # no password_reset yet
    assert _events(_attempt_of(client)) == [('rejected', 'account', 'email', 'email exists')]


def test_registered_mobile_typed_in_a_different_format(client):
    User.objects.create_user('someone@example.net', 'another-password-123', phone=MOBILE_E164)
    response = _step1(client, phone='+34 622-91-83-74')
    assert response.status_code == 200
    assert response.context['form'].errors == {
        'phone': ['This mobile phone number is already registered.']}
    assert RECOVERY_NOTE in response.content.decode()
    assert _events(_attempt_of(client)) == [('rejected', 'account', 'phone', 'phone exists')]
    assert not PendingRegistration.objects.exists()


def test_registered_wins_over_pending(first):
    User.objects.create_user(NORMALIZED, 'another-password-123')
    second = Client()
    response = _step1(second)
    assert response.status_code == 200
    assert set(response.context['form'].errors) == {'email'}
    assert RESUME_SESSION_KEY not in second.session
    assert [e[0] for e in _events(_attempt_of(second))] == ['rejected']


def test_recovery_link_appears_once_password_reset_is_routed(client, settings):
    settings.ROOT_URLCONF = __name__
    User.objects.create_user(NORMALIZED, 'another-password-123')
    page = _step1(client).content.decode()
    assert RECOVERY_NOTE in page
    assert 'href="/password-reset/"' in page and 'data-recovery-link' in page


def test_recovery_note_in_spanish(client):
    User.objects.create_user(NORMALIZED, 'another-password-123')
    page = _step1(client, lang='es').content.decode()
    assert 'Esta dirección de correo electrónico ya está registrada.' in page
    assert 'Si esta cuenta es suya, puede recuperar el acceso' in page


# --- Pending email or mobile -------------------------------------------------------------

def test_pending_email(first):
    second = Client()
    response = _step1(second, phone=OTHER_MOBILE)
    assert response.status_code == 302 and response['Location'] == RESUME_URL
    assert PendingRegistration.objects.get() == first  # nothing typed here is stored
    assert SESSION_KEY not in second.session
    assert second.session[RESUME_SESSION_KEY] == {
        'id': str(first.pk), 'channel': EMAIL, 'sent': False}  # never a contact

    [event] = _attempt_of(second).events.all()
    assert (event.event, event.step, event.field, event.reason) == (
        'pending_found', 'account', 'email', 'email pending')
    assert event.data['email'] == NORMALIZED and event.data['phone'] == '+34611223344'
    assert PASSWORD not in json.dumps(event.data)
    assert not {'password1', 'password2'} & set(event.data)

    page = second.get(RESUME_URL)
    text = page.content.decode()
    assert 'A registration with this email address is already in progress.' in text
    assert 'value="send"' in text
    for value in (*OLD.values(), MOBILE_E164, '622'):
        assert value not in _without_csrf_tokens(text)  # nothing of the matched registration


def test_pending_mobile(first, caplog):
    second = Client()
    response = _step1(second, email=OTHER_EMAIL)
    assert response['Location'] == RESUME_URL
    [event] = _attempt_of(second).events.all()
    assert (event.field, event.reason) == ('phone', 'phone pending')
    assert second.session[RESUME_SESSION_KEY]['channel'] == MOBILE

    assert 'this mobile phone number is already in progress' in second.get(
        RESUME_URL).content.decode()
    second.post(RESUME_URL, {'action': 'send'})
    line = [r.getMessage() for r in caplog.records if r.name == 'signup.verification'][-1]
    assert f'{MOBILE} {MOBILE_E164} ({PURPOSE_RESUME}' in line
    assert MOBILE_E164 in second.get(RESUME_URL).content.decode()  # the typed mobile

    response = second.post(RESUME_URL, {'code': _code(MOBILE, MOBILE_E164)})
    assert response['Location'] == '/en/signup/verify-email/'
    new = PendingRegistration.objects.get()
    assert new.email == NORMALIZED and new.first_name == OLD['first_name']


def test_email_is_matched_before_mobile(first):
    """One row has the email, another the mobile: the email match wins."""
    other = Client()
    _step1(other, email=OTHER_EMAIL, phone=OTHER_MOBILE)
    second = Client()
    _step1(second, phone=OTHER_MOBILE)
    state = second.session[RESUME_SESSION_KEY]
    assert (state['id'], state['channel']) == (str(first.pk), EMAIL)


def test_pending_check_runs_only_when_step1_is_valid(first):
    second = Client()
    response = _step1(second, password2='a-different-password-1')
    assert response.status_code == 200
    assert [e[0] for e in _events(_attempt_of(second))] == ['rejected']
    assert RESUME_SESSION_KEY not in second.session


def test_resume_page_in_spanish(first):
    second = Client()
    _step1(second, lang='es')
    page = second.get('/es/signup/resume/').content.decode()
    assert 'Ya hay un registro en curso con esta dirección de correo electrónico.' in page
    assert 'Enviar código' in page


# --- Resume code -------------------------------------------------------------------------

def test_correct_code_replaces_the_old_registration(client, first, caplog):
    old_attempt = first.attempt
    second = Client()
    _step1(second, phone=OTHER_MOBILE)
    new_attempt = _attempt_of(second)

    response = _resume(second)
    assert response.status_code == 302
    assert response['Location'] == '/en/signup/verify-email/'  # the old row's step

    new = PendingRegistration.objects.get()
    assert new.pk != first.pk and new.attempt == new_attempt
    assert second.session[SESSION_KEY] == str(new.pk)
    assert RESUME_SESSION_KEY not in second.session
    for name in (*PendingRegistration.USER_FIELDS, 'email', 'password', 'phone_region',
                 'step'):
        assert getattr(new, name) == getattr(first, name), name
    assert new.phone == MOBILE_E164  # the old mobile, not the one typed here

    # Old attempt: ended `abandoned`. New one: found, code sent, resumed, email code sent.
    old_attempt.refresh_from_db()
    assert old_attempt.outcome == 'abandoned' and old_attempt.ended_at is not None
    events = list(new_attempt.events.all())
    assert [(e.event, e.step) for e in events] == [
        ('pending_found', 'account'), ('code_sent', 'resume'), ('resumed', 'resume'),
        ('code_sent', 'verify-email')]
    assert events[1].data['channel'] == EMAIL and events[1].data['purpose'] == 'resume'
    assert events[2].data['attempt'] == str(old_attempt.pk)
    assert new_attempt.ended_at is None

    # The resumed signup completes with the email code just sent.
    response = second.post('/en/signup/verify-email/',
                           {'code': _code(EMAIL, NORMALIZED, PURPOSE_SIGNUP)})
    assert response['Location'] == '/en/welcome/'
    user = User.objects.get()
    assert user.first_name == OLD['first_name'] and user.check_password(PASSWORD)

    # The first browser lost its registration and starts again.
    assert client.get('/en/signup/verify-email/')['Location'] == '/en/signup/'
    assert SESSION_KEY not in client.session


def test_wrong_code_reveals_nothing(first):
    second = Client()
    _step1(second)
    second.post(RESUME_URL, {'action': 'send'})
    code = _code(EMAIL, NORMALIZED)

    response = second.post(RESUME_URL, {'code': _wrong(code)})
    assert response.status_code == 200
    assert response.context['form'].errors == {
        'code': ['This code is not correct. Check it, or send a new one.']}
    text = response.content.decode()
    for value in (*OLD.values(), MOBILE_E164):
        assert value not in _without_csrf_tokens(text)
    assert PendingRegistration.objects.get() == first
    assert first.attempt.ended_at is None

    event = list(_attempt_of(second).events.all())[-1]
    assert (event.event, event.step, event.field, event.reason) == (
        'rejected', 'resume', 'code', 'OTP verification failed')
    assert code not in json.dumps(event.data) and _wrong(code) not in json.dumps(event.data)


def test_a_signup_code_does_not_resume(first):
    second = Client()
    _step1(second)
    second.post(RESUME_URL, {'action': 'send'})
    response = second.post(RESUME_URL, {'code': _code(EMAIL, NORMALIZED, PURPOSE_SIGNUP)})
    assert response.status_code == 200 and response.context['form'].errors
    assert PendingRegistration.objects.get() == first


def test_resend(first):
    second = Client()
    _step1(second)
    second.post(RESUME_URL, {'action': 'send'})
    second.post(RESUME_URL, {'action': 'resend'})
    events = list(_attempt_of(second).events.all())
    assert [e.event for e in events] == ['pending_found', 'code_sent', 'code_sent']
    assert events[-1].data['resend'] is True


def test_use_different_details_clears_the_resume(first):
    second = Client()
    _step1(second)
    response = second.get(RESUME_URL + '?cancel')
    assert response['Location'] == '/en/signup/'
    assert RESUME_SESSION_KEY not in second.session
    assert second.get(RESUME_URL)['Location'] == '/en/signup/'


def test_resume_page_without_an_offer_goes_to_step1(client):
    assert client.get(RESUME_URL)['Location'] == '/en/signup/'


def test_browsers_own_earlier_registration_is_replaced(first):
    second = Client()
    _step1(second, email=OTHER_EMAIL, phone=OTHER_MOBILE)
    own = PendingRegistration.objects.exclude(pk=first.pk).get()
    _step1(second)  # back to step 1, now with the first signup's email
    _resume(second)
    new = PendingRegistration.objects.get()
    assert new.pk not in (first.pk, own.pk) and new.email == NORMALIZED
    assert new.attempt_id == own.attempt_id  # this signup's attempt continues


# --- Page closed, stale, own -------------------------------------------------------------

def test_page_closed_then_resumed_in_a_fresh_browser(first):
    fresh = Client()
    assert _step1(fresh)['Location'] == RESUME_URL
    _resume(fresh)
    form = fresh.get('/en/signup/').context['form']
    assert (form.initial['first_name'], form.initial['last_name'], form.initial['country'],
            form.initial['phone_prefix'], form.initial['phone']) == (
        OLD['first_name'], OLD['last_name'], 'PT', 'ES', '622 91 83 74')


def test_stale_pending_is_ignored(first):
    PendingRegistration.objects.update(created_at=timezone.now() - timedelta(hours=25))
    second = Client()
    response = _step1(second)
    assert response['Location'] == '/en/signup/verify-email/'
    assert PendingRegistration.objects.count() == 2
    assert 'pending_found' not in [e[0] for e in _events(_attempt_of(second))]


def test_active_queryset_window(first):
    assert list(PendingRegistration.objects.active()) == [first]
    PendingRegistration.objects.update(created_at=timezone.now() - timedelta(hours=24))
    assert not PendingRegistration.objects.active().exists()


def test_resume_of_a_row_gone_stale_meanwhile(first):
    second = Client()
    _step1(second)
    second.post(RESUME_URL, {'action': 'send'})
    PendingRegistration.objects.update(created_at=timezone.now() - timedelta(hours=25))
    assert second.post(RESUME_URL, {'code': _code(EMAIL, NORMALIZED)})['Location'] == (
        '/en/signup/')
    assert PendingRegistration.objects.get() == first


def test_own_pending_is_not_a_match(client, first):
    response = _step1(client, **OLD)  # back to step 1, saved again
    assert response['Location'] == '/en/signup/verify-email/'
    assert PendingRegistration.objects.get() == first
    assert 'pending_found' not in [e[0] for e in _events(first.attempt)]


def test_resumed_row_keeps_the_original_created_at(first):
    started = timezone.now() - timedelta(hours=23)
    PendingRegistration.objects.update(created_at=started)
    second = Client()
    _step1(second)
    _resume(second)
    new = PendingRegistration.objects.get()
    assert new.pk != first.pk and new.created_at == started
    assert PendingRegistration.objects.active().exists()
    # 24 h after the first step 1 it is no longer active.
    assert not PendingRegistration.objects.active(now=started + timedelta(hours=24)).exists()


# --- Race at creation --------------------------------------------------------------------

def test_mobile_registered_meanwhile(client, first):
    User.objects.create_user('someone@example.net', 'another-password-123', phone=MOBILE_E164)
    response = client.post('/en/signup/verify-email/',
                           {'code': _code(EMAIL, NORMALIZED, PURPOSE_SIGNUP)})
    assert response.status_code == 200
    assert response.context['form'].non_field_errors() == [
        'This mobile phone number is already registered.']
    assert RECOVERY_NOTE in response.content.decode()
    assert User.objects.count() == 1 and PendingRegistration.objects.exists()
    event = list(first.attempt.events.all())[-1]
    assert (event.event, event.field, event.reason) == ('rejected', 'phone', 'phone exists')
    assert event.data['phone'] == MOBILE_E164


def test_email_registered_meanwhile_shows_the_recovery_note(client, first):
    User.objects.create_user(NORMALIZED, 'another-password-123')
    response = client.post('/en/signup/verify-email/',
                           {'code': _code(EMAIL, NORMALIZED, PURPOSE_SIGNUP)})
    assert response.context['form'].non_field_errors() == [
        'This email address is already registered.']
    assert RECOVERY_NOTE in response.content.decode()

