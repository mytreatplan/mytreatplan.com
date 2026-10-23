"""Signup entry 8: cancel and the idle timeout, with the Django client. Every cancel and
idle row of the plan's I/O matrix (the connection rows are in browser/test_connection.py),
plus the markup the connection handling relies on."""
import re
import time

import pytest
from django.contrib.messages import get_messages
from django.test import Client

from eventlog.models import Attempt, Event
from legal.models import LegalChoice
from signup.models import PendingRegistration
from signup.steps import CODE_STATE_SESSION_KEY
from signup.views import (ATTEMPT_SESSION_KEY, IDLE_TIMEOUT, RESUME_SESSION_KEY,
                          SEEN_SESSION_KEY, SESSION_KEY)
from signup.verification import EMAIL, PURPOSE_RESUME

from .helpers import NORMALIZED, _current_code, _legal, _step1

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

CANCEL_URL = '/en/signup/cancel/'
LEGAL_URL = '/en/signup/legal/'
SIGNUP_KEYS = (SESSION_KEY, ATTEMPT_SESSION_KEY, RESUME_SESSION_KEY, CODE_STATE_SESSION_KEY)
CANCELLED_MESSAGE = 'Your registration was cancelled.'
IDLE_MESSAGE = ('Your session ended after 30 minutes without activity. Enter your email '
                'to continue where you left off.')


def _messages(response):
    return [str(message) for message in get_messages(response.wsgi_request)]


def _signup_keys(client):
    return {key for key in SIGNUP_KEYS if key in client.session}


def _set_seen(client, seconds_ago):
    session = client.session
    session[SEEN_SESSION_KEY] = time.time() - seconds_ago
    session.save()


# --- Cancel ----------------------------------------------------------------------------

def test_cancel_link_on_every_step_and_the_resume_page(at_legal):
    page = at_legal.get('/en/signup/').content.decode()
    assert f'href="{CANCEL_URL}?from=account"' in page
    page = at_legal.get(LEGAL_URL).content.decode()
    assert f'href="{CANCEL_URL}?from=legal"' in page and 'Cancel registration' in page
    _legal(at_legal)
    page = at_legal.get('/en/signup/verify-email/').content.decode()
    assert f'href="{CANCEL_URL}?from=verify-email"' in page

    second = Client()
    second.post('/en/signup/check-email/', {'email': NORMALIZED})
    page = second.get('/en/signup/resume/').content.decode()
    assert f'href="{CANCEL_URL}?from=resume"' in page


def test_cancel_get_only_shows_the_confirmation(at_legal):
    """Cancel GET only: the confirmation page; nothing removed."""
    page = at_legal.get(CANCEL_URL + '?from=legal')
    html = page.content.decode()
    assert page.status_code == 200
    assert 'Do you want to cancel your registration?' in html
    assert re.search(r'<form method="post"[^>]*>', html)
    assert 'hx-boost' not in re.search(r'<form method="post"[^>]*>', html).group(0)
    assert 'Keep my registration' in html and f'href="{LEGAL_URL}"' in html
    assert PendingRegistration.objects.count() == 1
    assert Attempt.objects.get().ended_at is None
    assert _signup_keys(at_legal) == {SESSION_KEY, ATTEMPT_SESSION_KEY}


def test_cancel_back_out_keeps_everything(at_legal):
    """Cancel back out: "Keep my registration" leads back to the same step."""
    _legal(at_legal)
    page = at_legal.get(CANCEL_URL + '?from=legal').content.decode()
    assert 'href="/en/signup/legal/"' in page
    assert at_legal.get(LEGAL_URL).status_code == 200
    pending = PendingRegistration.objects.get()
    assert pending.legal_choices.count() == 4
    assert Attempt.objects.get().ended_at is None


@pytest.mark.parametrize('origin, expected', [
    ('', LEGAL_URL),                      # no origin: the current step
    ('account', '/en/signup/'),
    ('verify-mobile', LEGAL_URL),         # a step not reached yet: the current one
    ('no-such-step', LEGAL_URL),
    ('resume', LEGAL_URL),                # no resume on offer
])
def test_keep_my_registration_target(at_legal, origin, expected):
    page = at_legal.get(f'{CANCEL_URL}?from={origin}').content.decode()
    assert re.search(r'href="([^"]*)"[^>]*data-keep-registration', page).group(1) == expected


def test_cancel_confirm_at_the_legal_step(at_legal):
    """Cancel confirm: row and legal choices gone, `purged` (cancelled), the attempt ended
    `cancelled`, an empty step 1 with the message."""
    _legal(at_legal)
    pending = PendingRegistration.objects.get()
    assert LegalChoice.objects.filter(pending_registration=pending).count() == 4
    at_legal.get('/en/signup/verify-email/')  # code-page state in the session
    assert CODE_STATE_SESSION_KEY in at_legal.session

    response = at_legal.post(CANCEL_URL)
    assert response.status_code == 302 and response['Location'] == '/en/signup/'
    assert _messages(response) == [CANCELLED_MESSAGE]

    assert not PendingRegistration.objects.exists()
    assert not LegalChoice.objects.exists()
    attempt = Attempt.objects.get()
    assert attempt.outcome == 'cancelled' and attempt.ended_at is not None
    purged = attempt.events.last()
    assert (purged.event, purged.reason, purged.data) == ('purged', 'cancelled', None)
    assert not _signup_keys(at_legal)

    page = at_legal.get('/en/signup/')
    html = page.content.decode()
    assert page.status_code == 200 and CANCELLED_MESSAGE in html
    assert NORMALIZED not in html  # empty step 1
    assert page.context['pending'] is None


def test_cancel_keeps_the_log_values(at_legal):
    """Log kept: the attempt's events keep their data until the 30-day job."""
    at_legal.post(CANCEL_URL)
    saved = Attempt.objects.get().events.get(event='step_saved')
    assert saved.data['email'] == NORMALIZED


def test_cancel_before_step1_is_saved(client):
    """Cancel before save: an open attempt (a rejected step 1) ends `cancelled`; the
    session keys are cleared."""
    response = _step1(client, password2='a-different-password-1')
    assert response.status_code == 200
    attempt = Attempt.objects.get()
    assert attempt.ended_at is None

    response = client.post(CANCEL_URL)
    assert response['Location'] == '/en/signup/'
    assert _messages(response) == [CANCELLED_MESSAGE]
    attempt.refresh_from_db()
    assert attempt.outcome == 'cancelled'
    assert not attempt.events.filter(event='purged').exists()
    assert not _signup_keys(client)
    assert not PendingRegistration.objects.exists()


def test_cancel_with_nothing_started(client):
    response = client.post(CANCEL_URL)
    assert response['Location'] == '/en/signup/'
    assert not Attempt.objects.exists()


def test_cancel_with_a_resume_on_offer_leaves_the_other_registration(at_legal):
    """The matched registration belongs to another signup: cancel does not touch it."""
    second = Client()
    second.post('/en/signup/check-email/', {'email': NORMALIZED})
    assert RESUME_SESSION_KEY in second.session
    page = second.get(CANCEL_URL + '?from=resume').content.decode()
    assert 'href="/en/signup/resume/"' in page

    second.post(CANCEL_URL)
    assert not _signup_keys(second)
    assert PendingRegistration.objects.count() == 1
    first_attempt = PendingRegistration.objects.get().attempt
    assert first_attempt.ended_at is None
    second_attempt = Attempt.objects.exclude(pk=first_attempt.pk).get()
    assert second_attempt.outcome == 'cancelled'


def test_cancel_in_spanish(at_legal):
    page = at_legal.get('/es/signup/cancel/').content.decode()
    assert 'Cancelar registro' in page and 'Mantener mi registro' in page
    assert '¿Quiere cancelar su registro?' in page
    response = at_legal.post('/es/signup/cancel/')
    assert response['Location'] == '/es/signup/'
    assert _messages(response) == ['Su registro se ha cancelado.']
    assert 'Cancelar registro' in at_legal.get('/es/signup/').content.decode()


# --- Idle timeout ----------------------------------------------------------------------

def test_every_signup_request_stores_its_time(client):
    before = time.time()
    client.get('/en/signup/')
    assert before <= client.session[SEEN_SESSION_KEY] <= time.time()


def test_idle_31_minutes(at_legal):
    """Idle 31 min: keys cleared, `idle_timeout` logged (attempt open), step 1 with the
    message, the row kept."""
    _set_seen(at_legal, 31 * 60)
    pending = PendingRegistration.objects.get()

    response = at_legal.get(LEGAL_URL)
    assert response.status_code == 302 and response['Location'] == '/en/signup/'
    assert _messages(response) == [IDLE_MESSAGE]
    assert not _signup_keys(at_legal)

    attempt = Attempt.objects.get()
    event = attempt.events.last()
    assert (event.event, event.reason) == ('idle_timeout', 'idle timeout')
    assert attempt.ended_at is None
    assert PendingRegistration.objects.get().pk == pending.pk

    page = at_legal.get('/en/signup/')
    assert page.status_code == 200 and IDLE_MESSAGE in page.content.decode()
    assert page.context['pending'] is None
    # The rest of the session stays.
    assert SEEN_SESSION_KEY in at_legal.session


def test_idle_29_minutes(at_legal):
    """Idle 29 min: the normal page."""
    _set_seen(at_legal, 29 * 60)
    response = at_legal.get(LEGAL_URL)
    assert response.status_code == 200
    assert _signup_keys(at_legal) == {SESSION_KEY, ATTEMPT_SESSION_KEY}
    assert not Attempt.objects.get().events.filter(event='idle_timeout').exists()


def test_idle_exactly_at_the_timeout_is_not_idle(at_legal, monkeypatch):
    now = time.time()
    monkeypatch.setattr('signup.views._now', lambda: now)
    session = at_legal.session
    session[SEEN_SESSION_KEY] = now - IDLE_TIMEOUT
    session.save()
    assert at_legal.get(LEGAL_URL).status_code == 200


def test_idle_post_is_not_processed(at_legal):
    """Idle POST: a step POST after 31 min is not saved; step 1 with the message."""
    _set_seen(at_legal, 31 * 60)
    response = _legal(at_legal)
    assert response['Location'] == '/en/signup/'
    assert _messages(response) == [IDLE_MESSAGE]
    pending = PendingRegistration.objects.get()
    assert pending.step == 'legal'
    assert not pending.legal_choices.exists()
    assert not Attempt.objects.get().events.filter(step='legal').exists()


def test_idle_live_email_check_answers_nothing_and_queues_the_message(at_legal):
    """The live check as the first request after the timeout: no offer, keys cleared, the
    idle message on the next page."""
    second = Client()
    assert _step1(second, password2='a-different-password-1').status_code == 200
    assert ATTEMPT_SESSION_KEY in second.session  # a signup in progress (rejected step 1)
    _set_seen(second, 31 * 60)
    response = second.post('/en/signup/check-email/', {'email': NORMALIZED})
    assert response.status_code == 200
    assert 'data-resume-offer' not in response.content.decode()
    assert not _signup_keys(second)
    page = second.get('/en/signup/')
    assert page.status_code == 200 and IDLE_MESSAGE in page.content.decode()


def test_idle_without_a_signup_in_progress_get(client):
    """No signup state (only step 1 looked at): nothing times out."""
    client.get('/en/signup/')
    _set_seen(client, 31 * 60)
    page = client.get('/en/signup/')
    assert page.status_code == 200 and IDLE_MESSAGE not in page.content.decode()
    assert not Attempt.objects.exists()


def test_idle_without_a_signup_in_progress_first_step1_post(client):
    """A first step 1 POST after 31 minutes of typing is saved."""
    client.get('/en/signup/')
    _set_seen(client, 31 * 60)
    response = _step1(client)
    assert response['Location'] == LEGAL_URL
    assert IDLE_MESSAGE not in _messages(response)
    assert PendingRegistration.objects.get().email == NORMALIZED
    assert not Event.objects.filter(event='idle_timeout').exists()


def test_idle_after_cancel_does_not_time_out(at_legal):
    at_legal.post(CANCEL_URL)
    _set_seen(at_legal, 31 * 60)
    page = at_legal.get('/en/signup/')
    assert page.status_code == 200 and IDLE_MESSAGE not in page.content.decode()


def test_idle_resume_page_goes_to_step1(at_legal):
    second = Client()
    second.post('/en/signup/check-email/', {'email': NORMALIZED})
    _set_seen(second, 31 * 60)
    response = second.post('/en/signup/resume/', {'action': 'send'})
    assert response['Location'] == '/en/signup/'
    assert _messages(response) == [IDLE_MESSAGE]
    assert RESUME_SESSION_KEY not in second.session


def test_idle_then_resume_restores_the_data(at_legal):
    """Idle then resume: the same email at step 1 offers the resume; the code restores the
    data; the idle attempt then ends `abandoned`."""
    _legal(at_legal)
    old = PendingRegistration.objects.get()
    old_attempt = old.attempt
    _set_seen(at_legal, 31 * 60)
    at_legal.get('/en/signup/verify-email/')

    response = at_legal.post('/en/signup/check-email/', {'email': NORMALIZED})
    assert 'data-resume-offer' in response.content.decode()
    at_legal.post('/en/signup/resume/', {'action': 'send'})
    response = at_legal.post('/en/signup/resume/',
                             {'code': _current_code(EMAIL, NORMALIZED, PURPOSE_RESUME)})
    assert response['Location'] == '/en/signup/'

    new = PendingRegistration.objects.get()
    assert new.pk != old.pk and new.email == NORMALIZED
    # Neither the password nor the legal choices come back (plan-fix-resume-takeover).
    assert new.password == '' and not new.legal_choices.exists()
    old_attempt.refresh_from_db()
    assert old_attempt.outcome == 'abandoned'
    assert [e.event for e in old_attempt.events.all()][-1] == 'idle_timeout'


def test_idle_in_spanish(at_legal):
    _set_seen(at_legal, 31 * 60)
    response = at_legal.get('/es/signup/legal/')
    assert response['Location'] == '/es/signup/'
    assert _messages(response) == [
        'Su sesión ha finalizado tras 30 minutos sin actividad. Introduzca su correo '
        'electrónico para continuar donde lo dejó.']


# --- Connection handling markup (the behaviour is in browser/test_connection.py) -------

def test_wizard_and_resume_forms_are_boosted_with_the_banner(at_legal):
    for url in ('/en/signup/', LEGAL_URL):
        html = at_legal.get(url).content.decode()
        forms = re.findall(r'<form method="post"[^>]*>', html)
        assert forms and all('hx-boost="true"' in f and 'hx-target="body"' in f
                             for f in forms), forms
        banner = re.search(r'<div[^>]*data-connection-lost[^>]*>', html).group(0)
        assert 'hidden' in banner and 'role="alert"' in banner
        assert "Connection lost. We&#x27;ll send your data again when you&#x27;re back online." in html \
            or "Connection lost. We'll send your data again when you're back online." in html
        assert 'data-connection-retry' in html
        assert 'js/signup-connection.js' in html and 'js/signup-account.js' in html

    _legal(at_legal)
    html = at_legal.get('/en/signup/verify-email/').content.decode()
    forms = re.findall(r'<form method="post"[^>]*>', html)
    assert len(forms) >= 2 and all('hx-boost="true"' in f for f in forms)  # code help too

    second = Client()
    second.post('/en/signup/check-email/', {'email': NORMALIZED})
    html = second.get('/en/signup/resume/').content.decode()
    assert all('hx-boost="true"' in f for f in re.findall(r'<form method="post"[^>]*>', html))
    assert 'data-connection-lost' in html


def test_step1_keeps_the_resume_form_trick(client):
    html = client.get('/en/signup/').content.decode()
    resume_form = re.search(r'<form id="resume-form"[^>]*>', html).group(0)
    assert 'hx-boost="true"' in resume_form and 'action="/en/signup/resume/"' in resume_form
    assert 'data-scripts="' in html  # zxcvbn-ts, loaded by signup-account.js after a swap


def test_banner_in_spanish(client):
    html = client.get('/es/signup/').content.decode()
    assert ('Se ha perdido la conexión. Volveremos a enviar sus datos cuando vuelva a estar '
            'en línea.') in html
    assert 'Intentar de nuevo' in html
