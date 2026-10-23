"""Story 2.6: the legal step. Every row of the plan's I/O matrix, through the wizard."""
import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from eventlog.models import Attempt
from legal.models import LegalChoice, LegalDocument
from signup.models import PendingRegistration
from signup.verification import EMAIL, PURPOSE_RESUME, PURPOSE_SIGNUP

from .helpers import (NORMALIZED, _code_from_log, _current_code, _email_code, _legal,
                      _mobile_code, _step1)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()

LEGAL_URL = '/en/signup/legal/'
KINDS = ['terms', 'conditions', 'ormco_tps', 'commercial_consent']


def _choices(owner):
    """{kind: (version, accepted)} of a pending registration's or a user's choices."""
    return {c.document.kind: (c.document.version, c.accepted)
            for c in owner.legal_choices.select_related('document')}


def _checked(html, name):
    tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', html).group(0)
    return ' checked' in tag


def _new_version(kind='terms', published_at=None):
    return LegalDocument.objects.create(
        kind=kind, title_en='Terms of Use v-next', title_es='Términos de uso v-next',
        body_en='## New terms\n\nThe **new** text.', body_es='## Nuevos términos\n\nTexto.',
        published_at=published_at or timezone.now())


# --- All required / all ticked ---------------------------------------------------------

def test_required_ticked_optional_not(at_legal, caplog):
    before = timezone.now()
    response = _legal(at_legal)
    assert response['Location'] == '/en/signup/verify-email/'

    pending = PendingRegistration.objects.get()
    assert pending.step == 'verify-email'
    assert _choices(pending) == {'terms': (1, True), 'conditions': (1, True),
                                 'ormco_tps': (1, False), 'commercial_consent': (1, False)}
    for choice in pending.legal_choices.all():
        assert before <= choice.decided_at <= timezone.now() and choice.user is None
    _code_from_log(caplog)  # the email code was sent on entering its step


def test_all_ticked(at_legal):
    _legal(at_legal, ormco_tps='on', commercial_consent='on')
    assert _choices(PendingRegistration.objects.get()) == {
        kind: (1, True) for kind in KINDS}


@pytest.mark.parametrize('missing, message', [
    ('terms', 'You must accept the Terms to continue.'),
    ('conditions', 'You must accept the Conditions to continue.'),
])
def test_required_document_missing(at_legal, caplog, missing, message):
    caplog.clear()
    response = _legal(at_legal, ormco_tps='on', **{missing: None})

    assert response.status_code == 200
    assert response.context['form'].errors == {missing: [message]}
    assert message in response.content.decode()
    assert not LegalChoice.objects.exists()
    pending = PendingRegistration.objects.get()
    assert pending.step == 'legal'
    assert not [r for r in caplog.records if r.name == 'signup.verification']  # no code

    event = list(Attempt.objects.get().events.all())[-1]
    assert (event.event, event.step, event.field, event.reason) == (
        'rejected', 'legal', missing, 'required')
    assert event.data[missing] == {'version': 1, 'accepted': False}
    assert event.data['ormco_tps'] == {'version': 1, 'accepted': True}


def test_saved_choices_are_logged_without_personal_data(at_legal):
    _legal(at_legal, commercial_consent='on')
    event = Attempt.objects.get().events.get(event='step_saved', step='legal')
    data = {k: v for k, v in event.data.items() if k not in ('ip', 'user_agent')}
    assert data == {
        'terms': {'version': 1, 'accepted': True},
        'conditions': {'version': 1, 'accepted': True},
        'ormco_tps': {'version': 1, 'accepted': False},
        'commercial_consent': {'version': 1, 'accepted': True},
    }


# --- Unticked by default, refill with Back ---------------------------------------------

def test_first_visit_nothing_is_ticked(at_legal):
    page = at_legal.get(LEGAL_URL)
    html = page.content.decode()
    assert page.status_code == 200 and 'Step 2 of 4' in html
    for kind in KINDS:
        assert not _checked(html, kind), kind
    # Every current document, its text (rendered Markdown) and version.
    for document in LegalDocument.objects.current_by_kind().values():
        assert document.title_en in html
    assert '<strong>Draft.</strong>' in html and 'version 1' in html
    assert '<details' in html


def test_back_refills_the_required_but_never_ticks_the_optional(at_legal):
    _legal(at_legal, ormco_tps='on', commercial_consent='on')
    html = at_legal.get(LEGAL_URL).content.decode()
    assert _checked(html, 'terms') and _checked(html, 'conditions')
    assert not _checked(html, 'ormco_tps') and not _checked(html, 'commercial_consent')


def test_saving_again_replaces_the_choices(at_legal):
    _legal(at_legal, ormco_tps='on', commercial_consent='on')
    first_ids = set(LegalChoice.objects.values_list('pk', flat=True))
    _legal(at_legal)
    pending = PendingRegistration.objects.get()
    assert pending.legal_choices.count() == 4
    assert not first_ids & set(pending.legal_choices.values_list('pk', flat=True))
    assert _choices(pending)['ormco_tps'] == (1, False)


def test_legal_step_cannot_be_skipped(client):
    assert client.get(LEGAL_URL)['Location'] == '/en/signup/'
    _step1(client)
    assert client.get('/en/signup/verify-email/')['Location'] == LEGAL_URL


# --- New version, draft version --------------------------------------------------------

def test_new_version_is_shown_and_stored_at_once(at_legal):
    v2 = _new_version()
    html = at_legal.get(LEGAL_URL).content.decode()
    assert 'Terms of Use v-next' in html and 'version 2' in html
    assert '<h2>New terms</h2>' in html
    _legal(at_legal)
    assert _choices(PendingRegistration.objects.get())['terms'] == (2, True)
    assert LegalChoice.objects.get(document__kind='terms').document == v2


def test_future_version_is_not_shown(at_legal):
    _new_version()  # v2, current
    _new_version(published_at=timezone.now() + timedelta(days=1))  # v3, draft
    html = at_legal.get(LEGAL_URL).content.decode()
    assert 'version 2' in html and 'version 3' not in html
    _legal(at_legal)
    assert _choices(PendingRegistration.objects.get())['terms'] == (2, True)


def test_a_new_terms_version_asks_again(at_legal):
    """Accepted in v1, then v2 is published: Back to step 1 and Continue shows the legal
    step again (not done for the current version), with v2 unticked."""
    _legal(at_legal)
    _new_version()
    assert _step1(at_legal)['Location'] == LEGAL_URL
    assert not _checked(at_legal.get(LEGAL_URL).content.decode(), 'terms')


# --- Spanish ---------------------------------------------------------------------------

def test_spanish(client):
    _step1(client, lang='es')
    html = client.get('/es/signup/legal/').content.decode()
    for title in ('Términos de uso (borrador)', 'Condiciones del servicio (borrador)',
                  'Contrato Ormco TPS (borrador)', 'Comunicaciones comerciales (borrador)'):
        assert title in html
    assert '<strong>Borrador.</strong>' in html and 'versión 1' in html
    assert 'He leído y acepto: Términos de uso (borrador).' in html
    assert 'Acepto recibir comunicaciones comerciales del grupo MyTPDSO' in html
    assert 'Terms of Use' not in html and 'Draft.' not in html

    response = _legal(client, lang='es', terms=None)
    assert 'Debe aceptar los Términos para continuar.' in response.content.decode()


# --- Account created -------------------------------------------------------------------

def test_choices_move_to_the_user(at_legal, caplog):
    _legal(at_legal, commercial_consent='on')
    pending = PendingRegistration.objects.get()
    before = {c.document_id: (c.accepted, c.decided_at) for c in pending.legal_choices.all()}

    _email_code(at_legal)
    response = _mobile_code(at_legal)
    assert response['Location'] == '/en/welcome/'

    user = User.objects.get()
    assert not PendingRegistration.objects.exists()
    assert LegalChoice.objects.filter(pending_registration__isnull=False).count() == 0
    after = {c.document_id: (c.accepted, c.decided_at) for c in user.legal_choices.all()}
    assert after == before
    assert _choices(user) == {'terms': (1, True), 'conditions': (1, True),
                              'ormco_tps': (1, False), 'commercial_consent': (1, True)}


def test_choices_stay_with_the_pending_row_when_creation_fails(at_legal, caplog):
    _legal(at_legal)
    _email_code(at_legal)
    User.objects.create_user(NORMALIZED, 'another-password-123')  # registered meanwhile
    response = _mobile_code(at_legal)
    assert response.status_code == 200
    assert PendingRegistration.objects.get().legal_choices.count() == 4
    assert not LegalChoice.objects.filter(user__isnull=False).exists()


# --- Resume ----------------------------------------------------------------------------

def _resume(second):
    second.post('/en/signup/check-email/', {'email': NORMALIZED})
    second.post('/en/signup/resume/', {'action': 'send'})
    return second.post('/en/signup/resume/',
                       {'code': _current_code(EMAIL, NORMALIZED, PURPOSE_RESUME)})


def test_resume_after_the_legal_step_asks_it_again(at_legal):
    """A resume never carries the old row's legal choices: whoever made that row chose
    them, and the resume code proves only the email (plan-fix-resume-takeover). The legal
    step is shown again, nothing ticked, and the account gets only the new choices."""
    _legal(at_legal, ormco_tps='on', commercial_consent='on')
    old = PendingRegistration.objects.get()
    old_times = {c.decided_at for c in old.legal_choices.all()}

    second = Client()
    assert _resume(second)['Location'] == '/en/signup/'
    new = PendingRegistration.objects.get()
    assert new.pk != old.pk and not new.legal_choices.exists()
    assert not LegalChoice.objects.exists()  # the old row's went with it

    # Continue at step 1 (a password set again) asks the legal step, unticked; then the
    # mobile code (the email is verified by the resume code).
    assert _step1(second)['Location'] == LEGAL_URL
    form = second.get(LEGAL_URL).context['form']
    assert not any(form[kind].value() for kind in ('terms', 'conditions', 'ormco_tps',
                                                    'commercial_consent'))
    assert _legal(second)['Location'] == '/en/signup/verify-mobile/'
    assert _mobile_code(second)['Location'] == '/en/welcome/'
    choices = _choices(User.objects.get())
    assert choices['ormco_tps'][1] is False and choices['commercial_consent'][1] is False
    assert not {c.decided_at for c in User.objects.get().legal_choices.all()} & old_times


def test_resume_before_the_legal_step_asks_for_it(at_legal):
    second = Client()
    _resume(second)
    assert not PendingRegistration.objects.get().legal_choices.exists()
    assert _step1(second)['Location'] == LEGAL_URL
    # The email is verified (resume code): after the legal step, the mobile code.
    assert _legal(second)['Location'] == '/en/signup/verify-mobile/'
    assert _mobile_code(second)['Location'] == '/en/welcome/'
    assert len(_choices(User.objects.get())) == 4


def test_the_signup_code_is_still_the_email_step(at_legal):
    _legal(at_legal)
    code = _current_code(EMAIL, NORMALIZED, PURPOSE_SIGNUP)
    assert at_legal.post('/en/signup/verify-email/', {'code': code})['Location'] == (
        '/en/signup/verify-mobile/')


# --- Version published between showing and sending -------------------------------------

def test_version_published_after_the_page_was_shown_is_refused(at_legal):
    page = at_legal.get(LEGAL_URL).content.decode()
    shown = re.search(r'name="shown" value="([^"]*)"', page).group(1)
    v1 = LegalDocument.objects.current('terms')
    assert str(v1.pk) in shown.split(',')

    v2 = _new_version()
    response = _legal(at_legal, shown=shown)  # the page with v1
    assert response.status_code == 200
    message = 'The documents have been updated. Please review them again.'
    assert response.context['form'].non_field_errors() == [message]
    assert message in response.content.decode()
    assert not LegalChoice.objects.exists()
    assert PendingRegistration.objects.get().step == 'legal'
    event = list(Attempt.objects.get().events.all())[-1]
    assert (event.event, event.step, event.reason) == ('rejected', 'legal',
                                                       'documents_changed')

    # The page shown again carries v2, unticked; sending it stores v2.
    html = response.content.decode()
    assert 'Terms of Use v-next' in html and 'version 2' in html
    assert not _checked(html, 'terms') and not _checked(html, 'conditions')
    new_shown = re.search(r'name="shown" value="([^"]*)"', html).group(1)
    assert str(v2.pk) in new_shown.split(',') and str(v1.pk) not in new_shown.split(',')
    assert _legal(at_legal, shown=new_shown)['Location'] == '/en/signup/verify-email/'
    assert _choices(PendingRegistration.objects.get())['terms'] == (2, True)


def test_documents_changed_in_spanish(at_legal):
    _step1(at_legal, lang='es')
    response = _legal(at_legal, lang='es', shown='0')
    assert ('Los documentos se han actualizado. Revíselos de nuevo.'
            in response.content.decode())


# --- Rows saved before the legal step existed (migration signup 0008) ------------------

def test_pending_row_past_the_legal_step_without_choices_goes_back_to_it(at_legal, caplog):
    from importlib import import_module

    from django.apps import apps

    _legal(at_legal)
    pending = PendingRegistration.objects.get()
    # As saved before this story: at the email code, no legal choices.
    pending.legal_choices.all().delete()
    PendingRegistration.objects.filter(pk=pending.pk).update(step='verify-email')
    other = Client()
    _step1(other, email='bea@example.com', phone='611 223 344')
    _legal(other)  # past the legal step with choices: untouched

    migration = import_module('signup.migrations.0008_pending_rows_back_to_legal_step')
    migration.back_to_legal(apps, None)

    pending.refresh_from_db()
    assert pending.step == 'legal'
    assert PendingRegistration.objects.exclude(pk=pending.pk).get().step == 'verify-email'

    assert at_legal.get('/en/signup/verify-email/')['Location'] == LEGAL_URL
    code = _current_code(EMAIL, NORMALIZED, PURPOSE_SIGNUP)
    assert at_legal.post('/en/signup/verify-email/', {'code': code})['Location'] == LEGAL_URL
    assert not User.objects.exists()

    # Back at the email step within a minute: no new code; the one sent still works.
    assert _legal(at_legal)['Location'] == '/en/signup/verify-email/'
    assert at_legal.post('/en/signup/verify-email/', {'code': code})['Location'] == (
        '/en/signup/verify-mobile/')
    assert _mobile_code(at_legal)['Location'] == '/en/welcome/'
    assert len(_choices(User.objects.get())) == 4
