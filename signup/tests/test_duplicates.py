"""Signup entry 5: duplicate checks and the email-first resume (stories 2.5 and 2.5b).
Every row of both plans' I/O matrices, plus the event sequence on both attempts."""
import json
import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from eventlog.models import Attempt
from signup.models import PendingRegistration
from signup.verification import EMAIL, PURPOSE_RESUME, PURPOSE_SIGNUP
from signup.views import ATTEMPT_SESSION_KEY, RESUME_SESSION_KEY, SESSION_KEY

from .helpers import (EMAIL_ADDRESS, MOBILE_E164, NORMALIZED, PASSWORD, _current_code,
                      _email_code, _legal, _mobile_code, _step1, _wrong)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()

# The first signup's own details, which the second one must not see before the code.
OLD = {'first_name': 'Beatriz', 'last_name': 'Olmedo-Saavedra', 'country': 'PT'}
OTHER_EMAIL = 'carla@example.org'
OTHER_MOBILE = '611 223 344'
RECOVERY_NOTE = 'If this account is yours, you can recover access to it'
RESUME_URL = '/en/signup/resume/'


def _without_csrf_tokens(html):
    """The page without its CSRF tokens: they are random, and may contain a short value
    checked as absent (e.g. "PT")."""
    return re.sub(r'name="csrfmiddlewaretoken" value="[^"]*"', '', html)


def _code(channel, contact, purpose=PURPOSE_RESUME):
    return _current_code(channel, contact, purpose)


def _events(attempt):
    return [(e.event, e.step, e.field, e.reason) for e in attempt.events.all()]


def _attempt_of(client):
    return Attempt.objects.get(pk=client.session[ATTEMPT_SESSION_KEY])


@pytest.fixture
def first(client):
    """A signup in progress (step 1 and the legal step saved, at the email code) by
    another browser, with its own names."""
    assert _step1(client, **OLD).status_code == 302
    assert _legal(client).status_code == 302
    return PendingRegistration.objects.get()


def _resume(second, contact=NORMALIZED):
    """Send the resume code (the offer's Resume button) and enter it."""
    second.post(RESUME_URL, {'action': 'send'})
    return second.post(RESUME_URL, {'code': _code(EMAIL, contact)})


# --- Registered email or mobile ----------------------------------------------------------

def test_registered_email_whatever_its_case(client):
    User.objects.create_user(NORMALIZED.upper(), 'another-password-123')
    response = _step1(client)
    assert response.status_code == 200
    assert response.context['form'].errors == {
        'email': ['This email address is already registered.']}
    assert RECOVERY_NOTE in response.content.decode()
    # The link to the password reset request (epic-login entry 10).
    assert 'href="/en/password-reset/"' in response.content.decode()
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


def test_recovery_link_opens_the_password_reset_request(client):
    User.objects.create_user(NORMALIZED, 'another-password-123')
    page = _step1(client).content.decode()
    assert RECOVERY_NOTE in page
    assert 'href="/en/password-reset/"' in page and 'data-recovery-link' in page
    response = Client().get('/en/password-reset/')
    assert response.status_code == 200
    assert 'Reset your password' in response.content.decode()


def test_recovery_note_in_spanish(client):
    User.objects.create_user(NORMALIZED, 'another-password-123')
    page = _step1(client, lang='es').content.decode()
    assert 'Esta dirección de correo electrónico ya está registrada.' in page
    assert 'Si esta cuenta es suya, puede recuperar el acceso' in page


# --- Live email check (htmx) and pending email ------------------------------------------

def _check(client, email=EMAIL_ADDRESS, lang='en'):
    return client.post(f'/{lang}/signup/check-email/', {'email': email})


def test_step1_puts_the_email_first_with_the_live_check(client):
    page = client.get('/en/signup/')
    assert list(page.context['form'].fields)[0] == 'email'
    text = page.content.decode()
    assert text.index('id="id_email"') < text.index('id="id_title"')
    assert 'hx-post="/en/signup/check-email/"' in text
    assert 'hx-trigger="change"' in text and 'hx-target="#email-check"' in text
    assert 'hx-params="email,csrfmiddlewaretoken"' in text  # never the passwords
    assert 'id="email-check"' in text


def test_live_check_pending_email(first):
    second = Client()
    response = _check(second)
    assert response.status_code == 200
    text = response.content.decode()
    assert 'A registration with this email address is already in progress.' in text
    assert 'form="resume-form"' in text
    for value in (*OLD.values(), MOBILE_E164, '622'):
        assert value not in text  # nothing of the matched registration
    assert second.session[RESUME_SESSION_KEY] == {'id': str(first.pk), 'sent': False}
    assert SESSION_KEY not in second.session

    # An attempt is started, with the email (encrypted) in the event's data.
    [event] = _attempt_of(second).events.all()
    assert (event.event, event.step, event.field, event.reason) == (
        'pending_found', 'account', 'email', 'email pending')
    assert event.data['email'] == NORMALIZED

    # The Resume button sends the code to that email.
    assert second.post(RESUME_URL, {'action': 'send'})['Location'] == RESUME_URL
    assert NORMALIZED in second.get(RESUME_URL).content.decode()


def test_live_check_registered_email(client):
    User.objects.create_user(NORMALIZED.upper(), 'another-password-123')
    text = _check(client).content.decode()
    assert RECOVERY_NOTE in text and 'data-resume-offer' not in text
    assert not Attempt.objects.exists()


def test_live_check_no_match_shows_and_logs_nothing(client, first):
    attempts = Attempt.objects.count()
    for email in (OTHER_EMAIL, 'not-an-email', ''):
        response = _check(Client(), email=email)
        assert response.status_code == 200
        assert response.content.decode().strip() == ''
    assert Attempt.objects.count() == attempts


def test_live_check_ignores_the_own_registration(client, first):
    assert _check(client).content.decode().strip() == ''
    assert 'pending_found' not in [e[0] for e in _events(first.attempt)]


def test_live_check_is_post_only(client):
    assert client.get('/en/signup/check-email/').status_code == 405


def test_pending_email_without_javascript(first):
    """Continue with a full step 1: the same offer, under the email."""
    second = Client()
    response = _step1(second, phone=OTHER_MOBILE)
    assert response.status_code == 200
    text = response.content.decode()
    assert 'data-resume-offer' in text
    _assert_continue_is_the_default_button(text)
    assert PendingRegistration.objects.get() == first  # nothing typed here is stored
    assert SESSION_KEY not in second.session
    assert second.session[RESUME_SESSION_KEY] == {'id': str(first.pk), 'sent': False}

    [event] = _attempt_of(second).events.all()
    assert (event.event, event.step, event.field, event.reason) == (
        'pending_found', 'account', 'email', 'email pending')
    assert event.data['email'] == NORMALIZED and event.data['phone'] == '+34611223344'
    assert PASSWORD not in json.dumps(event.data)
    assert not {'password1', 'password2'} & set(event.data)
    for value in (OLD['first_name'], OLD['last_name'], MOBILE_E164):
        assert value not in _without_csrf_tokens(text)

    page = second.get(RESUME_URL).content.decode()
    assert 'A registration with this email address is already in progress.' in page
    assert 'value="send"' in page
    for value in (*OLD.values(), MOBILE_E164, '622'):
        assert value not in _without_csrf_tokens(page)


def _assert_continue_is_the_default_button(text):
    """The step 1 form's first submit button is Continue (Enter submits it); the Resume
    button belongs to #resume-form, which sits outside the step 1 form and sends the code."""
    step1 = re.search(r'<form method="post" class="mt-8[^>]*>(.*?)</form>', text, re.S).group(1)
    buttons = re.findall(r'<button[^>]*type="submit"[^>]*>', step1)
    owned = [b for b in buttons if 'form="' not in b]
    assert owned and 'Continue' in step1.split(owned[0], 1)[1].split('</button>', 1)[0]
    assert any('form="resume-form"' in b for b in buttons)
    resume_form = re.search(r'<form id="resume-form"[^>]*>(.*?)</form>', text, re.S)
    assert resume_form and 'id="resume-form"' not in step1
    assert 'action="/en/signup/resume/"' in resume_form.group(0)
    assert 'name="action" value="send"' in resume_form.group(1)
    assert 'csrfmiddlewaretoken' in resume_form.group(1)


def test_mobile_in_another_pending_registration_is_allowed(first, caplog):
    """A new email with the mobile of another signup in progress: step 1 saves; when the
    account is created that signup is removed and its attempt ends `abandoned`."""
    second = Client()
    assert 'data-resume-offer' not in _check(second, email=OTHER_EMAIL).content.decode()
    assert _step1(second, email=OTHER_EMAIL)['Location'] == '/en/signup/legal/'
    response = _legal(second)
    assert response['Location'] == '/en/signup/verify-email/'
    assert PendingRegistration.objects.count() == 2
    assert 'pending_found' not in [e[0] for e in _events(_attempt_of(second))]

    response = second.post('/en/signup/verify-email/',
                           {'code': _code(EMAIL, OTHER_EMAIL, PURPOSE_SIGNUP)})
    assert response['Location'] == '/en/signup/verify-mobile/'
    assert _mobile_code(second)['Location'] == '/en/welcome/'
    assert User.objects.get().email == OTHER_EMAIL
    assert not PendingRegistration.objects.exists()
    first.attempt.refresh_from_db()
    assert first.attempt.outcome == 'abandoned' and first.attempt.ended_at is not None
    assert ATTEMPT_SESSION_KEY not in second.session


def test_account_creation_removes_other_registrations_with_its_email(client, first):
    """A stale (no longer active) signup with the same email is removed too."""
    PendingRegistration.objects.update(created_at=timezone.now() - timedelta(hours=25))
    second = Client()
    _step1(second)  # not a match: the other one is stale
    _legal(second)
    other = PendingRegistration.objects.exclude(pk=first.pk).get()
    second.post('/en/signup/verify-email/',
                {'code': _code(EMAIL, NORMALIZED, PURPOSE_SIGNUP)})
    _mobile_code(second)
    assert User.objects.get().email == NORMALIZED
    assert not PendingRegistration.objects.exists()
    first.attempt.refresh_from_db()
    other.attempt.refresh_from_db()
    assert first.attempt.outcome == 'abandoned'
    assert other.attempt.outcome == 'completed'


def test_pending_check_runs_only_when_step1_is_valid(first):
    second = Client()
    response = _step1(second, password2='a-different-password-1')
    assert response.status_code == 200
    assert 'data-resume-offer' not in response.content.decode()
    assert [e[0] for e in _events(_attempt_of(second))] == ['rejected']
    assert RESUME_SESSION_KEY not in second.session


def test_resume_offer_in_spanish(first):
    second = Client()
    text = _check(second, lang='es').content.decode()
    assert 'Ya hay un registro en curso con esta dirección de correo electrónico.' in text
    assert 'Reanudar' in text
    page = second.get('/es/signup/resume/').content.decode()
    assert 'Ya hay un registro en curso con esta dirección de correo electrónico.' in page
    assert 'Enviar código' in page


# --- Resume code -------------------------------------------------------------------------

def test_correct_code_replaces_the_old_registration(client, first, caplog):
    old_attempt = first.attempt
    started = timezone.now() - timedelta(hours=3)
    PendingRegistration.objects.update(created_at=started)
    second = Client()
    _check(second)
    new_attempt = _attempt_of(second)

    response = _resume(second)
    assert response.status_code == 302
    assert response['Location'] == '/en/signup/'  # step 1, for review

    new = PendingRegistration.objects.get()
    assert new.pk != first.pk and new.attempt == new_attempt
    assert new.created_at == started
    assert new.email_verified_at is not None  # the code went to this email
    assert second.session[SESSION_KEY] == str(new.pk)
    assert RESUME_SESSION_KEY not in second.session
    for name in (*PendingRegistration.USER_FIELDS, 'email', 'password', 'phone_region'):
        assert getattr(new, name) == getattr(first, name), name
    # The legal choices are copied (same versions, decisions and times).
    copied = [(c.document_id, c.accepted, c.decided_at) for c in new.legal_choices.all()]
    assert len(copied) == 4 and sum(accepted for _d, accepted, _t in copied) == 2

    # Old attempt: ended `abandoned`. New one: found, code sent, resumed; no email code.
    old_attempt.refresh_from_db()
    assert old_attempt.outcome == 'abandoned' and old_attempt.ended_at is not None
    events = list(new_attempt.events.all())
    assert [(e.event, e.step) for e in events] == [
        ('pending_found', 'account'), ('code_sent', 'resume'), ('resumed', 'resume')]
    assert events[1].data['channel'] == EMAIL and events[1].data['purpose'] == 'resume'
    assert events[2].data['attempt'] == str(old_attempt.pk)
    assert new_attempt.ended_at is None

    # Step 1 shows the saved data, with the password fields empty.
    page = second.get('/en/signup/')
    form = page.context['form']
    assert (form.initial['email'], form.initial['first_name'], form.initial['last_name'],
            form.initial['country'], form.initial['phone_prefix'],
            form.initial['phone']) == (
        NORMALIZED, OLD['first_name'], OLD['last_name'], 'PT', 'ES', '622 91 83 74')
    text = page.content.decode()
    assert 'We have restored your registration.' in text
    assert re.search(r'<input type="password" name="password1"(?![^>]*value=)', text)
    assert not form.fields['password1'].required

    # The email code step is not reachable: step 1 (review) is next.
    assert new.step == 'account'
    assert second.get('/en/signup/verify-email/')['Location'] == '/en/signup/'

    # The first browser lost its registration and starts again.
    assert client.get('/en/signup/verify-email/')['Location'] == '/en/signup/'
    assert SESSION_KEY not in client.session


def _review(client, password='', password2='', **fields):
    """Continue at step 1 after a resume: the saved data as shown, passwords empty."""
    return _step1(client, password=password, password2=password2, **{**OLD, **fields})


def test_review_and_continue_asks_only_the_mobile_code(first, caplog):
    second = Client()
    _check(second)
    _resume(second)
    sent_before = len([r for r in caplog.records if r.name == 'signup.verification'])

    response = _review(second)
    assert response['Location'] == '/en/signup/verify-mobile/'
    assert PendingRegistration.objects.get().phone_verified_at is None
    lines = [r.getMessage() for r in caplog.records if r.name == 'signup.verification']
    assert len(lines) == sent_before + 1  # no email code; the mobile code
    assert f'whatsapp {MOBILE_E164}' in lines[-1]
    assert not User.objects.exists()
    assert _mobile_code(second)['Location'] == '/en/welcome/'

    user = User.objects.get()
    assert user.first_name == OLD['first_name'] and user.check_password(PASSWORD)
    assert second.get('/en/welcome/').wsgi_request.user == user
    assert not PendingRegistration.objects.exists()
    attempt = Attempt.objects.get(outcome='completed')
    assert [e[0] for e in _events(attempt)] == [
        'pending_found', 'code_sent', 'resumed', 'step_saved', 'code_sent', 'code_accepted',
        'completed']


def test_email_changed_in_review_runs_the_email_code_step(first, caplog):
    second = Client()
    _check(second)
    _resume(second)
    response = _review(second, email=OTHER_EMAIL)
    assert response['Location'] == '/en/signup/verify-email/'
    pending = PendingRegistration.objects.get()
    assert pending.email == OTHER_EMAIL and pending.email_verified_at is None
    line = [r.getMessage() for r in caplog.records if r.name == 'signup.verification'][-1]
    assert f'{EMAIL} {OTHER_EMAIL} ({PURPOSE_SIGNUP}' in line

    response = second.post('/en/signup/verify-email/',
                           {'code': _code(EMAIL, OTHER_EMAIL, PURPOSE_SIGNUP)})
    assert response['Location'] == '/en/signup/verify-mobile/'
    assert _mobile_code(second)['Location'] == '/en/welcome/'
    assert User.objects.get().email == OTHER_EMAIL


def test_email_case_changed_in_review_stays_verified(first):
    second = Client()
    _check(second)
    _resume(second)
    assert _review(second, email=NORMALIZED.upper())['Location'] == (
        '/en/signup/verify-mobile/')


def test_new_password_in_review(first):
    second = Client()
    _check(second)
    _resume(second)
    # Every rule applies once a password is typed.
    response = _review(second, password='short', password2='short')
    assert response.status_code == 200 and 'password1' in response.context['form'].errors
    response = _review(second, password='indigo-harbor-lantern-77', password2='')
    assert response.context['form'].errors == {'password2': ['This field is required.']}
    response = _review(second, password='indigo-harbor-lantern-77',
                       password2='indigo-harbor-lantern-78')
    assert 'password2' in response.context['form'].errors
    assert not User.objects.exists()

    response = _review(second, password='indigo-harbor-lantern-77',
                       password2='indigo-harbor-lantern-77')
    assert response['Location'] == '/en/signup/verify-mobile/'
    assert _mobile_code(second)['Location'] == '/en/welcome/'
    user = User.objects.get()
    assert user.check_password('indigo-harbor-lantern-77')
    assert not user.check_password(PASSWORD)


def test_wrong_code_reveals_nothing(first):
    second = Client()
    _check(second)
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
    assert SESSION_KEY not in second.session

    event = list(_attempt_of(second).events.all())[-1]
    assert (event.event, event.step, event.field, event.reason) == (
        'rejected', 'resume', 'code', 'OTP verification failed')
    assert code not in json.dumps(event.data) and _wrong(code) not in json.dumps(event.data)


def test_a_signup_code_does_not_resume(first):
    second = Client()
    _check(second)
    second.post(RESUME_URL, {'action': 'send'})
    response = second.post(RESUME_URL, {'code': _code(EMAIL, NORMALIZED, PURPOSE_SIGNUP)})
    assert response.status_code == 200 and response.context['form'].errors
    assert PendingRegistration.objects.get() == first


def test_resend(first, clock):
    second = Client()
    _check(second)
    second.post(RESUME_URL, {'action': 'send'})
    clock.advance(60)
    second.post(RESUME_URL, {'action': 'resend'})
    events = list(_attempt_of(second).events.all())
    assert [e.event for e in events] == ['pending_found', 'code_sent', 'code_sent']
    assert events[-1].data['resend'] is True


def test_use_different_details_clears_the_resume(first):
    second = Client()
    _check(second)
    response = second.get(RESUME_URL + '?cancel')
    assert response['Location'] == '/en/signup/'
    assert RESUME_SESSION_KEY not in second.session
    assert second.get(RESUME_URL)['Location'] == '/en/signup/'


def test_another_email_clears_the_offer(first):
    second = Client()
    _check(second)
    _check(second, email=OTHER_EMAIL)
    assert RESUME_SESSION_KEY not in second.session


def test_resume_page_without_an_offer_goes_to_step1(client):
    assert client.get(RESUME_URL)['Location'] == '/en/signup/'


def test_browsers_own_earlier_registration_is_replaced(first):
    second = Client()
    _step1(second, email=OTHER_EMAIL, phone=OTHER_MOBILE)
    own = PendingRegistration.objects.exclude(pk=first.pk).get()
    _check(second)  # back to step 1, now with the first signup's email
    _resume(second)
    new = PendingRegistration.objects.get()
    assert new.pk not in (first.pk, own.pk) and new.email == NORMALIZED
    assert new.attempt_id == own.attempt_id  # this signup's attempt continues


# --- Page closed, stale, own -------------------------------------------------------------

def test_page_closed_then_resumed_in_a_fresh_browser(first):
    """A signup closed at the email code step, resumed by its email in a fresh browser:
    the resume code, then Continue asks only the mobile code."""
    fresh = Client()
    assert 'data-resume-offer' in _check(fresh).content.decode()
    assert _resume(fresh)['Location'] == '/en/signup/'
    assert _review(fresh)['Location'] == '/en/signup/verify-mobile/'
    assert _mobile_code(fresh)['Location'] == '/en/welcome/'
    assert User.objects.get().email == NORMALIZED


def test_stale_pending_is_ignored(first):
    PendingRegistration.objects.update(created_at=timezone.now() - timedelta(hours=25))
    second = Client()
    response = _step1(second)
    assert response['Location'] == '/en/signup/legal/'
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
    _email_code(client)
    User.objects.create_user('someone@example.net', 'another-password-123', phone=MOBILE_E164)
    response = _mobile_code(client)
    assert response.status_code == 200
    assert response.context['form'].non_field_errors() == [
        'This mobile phone number is already registered.']
    assert RECOVERY_NOTE in response.content.decode()
    assert User.objects.count() == 1 and PendingRegistration.objects.exists()
    event = list(first.attempt.events.all())[-1]
    assert (event.event, event.field, event.reason) == ('rejected', 'phone', 'phone exists')
    assert event.data['phone'] == MOBILE_E164


def test_email_registered_meanwhile_shows_the_recovery_note(client, first):
    _email_code(client)
    User.objects.create_user(NORMALIZED, 'another-password-123')
    response = _mobile_code(client)
    assert response.context['form'].non_field_errors() == [
        'This email address is already registered.']
    assert RECOVERY_NOTE in response.content.decode()


def test_email_step_code_marks_the_email_verified(client, first):
    """A correct email-step code sets the mark; the mobile one is still to do."""
    assert first.email_verified_at is None
    client.post('/en/signup/verify-email/', {'code': _code(EMAIL, NORMALIZED, PURPOSE_SIGNUP)})
    first.refresh_from_db()
    assert first.email_verified_at is not None and first.phone_verified_at is None
    assert first.step == 'verify-mobile'

