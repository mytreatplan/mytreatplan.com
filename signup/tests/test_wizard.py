"""The signup wizard: every row of the plan's I/O matrix, plus the registry and storage rules."""
import re

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.db import connection

from legal.models import LegalDocument
from signup import steps as signup_steps
from signup.forms import shown_document_ids
from signup.models import PendingRegistration
from signup.verification import EMAIL, FakeVerificationProvider
from signup.views import SESSION_KEY

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()

EMAIL_ADDRESS = 'ana.garcia@Example.COM'
NORMALIZED = 'ana.garcia@example.com'
PASSWORD = 'violet-orbit-canyon-42'


FIRST_NAME = 'Anabel'
LAST_NAME = 'Garcia-Montenegro'
MOBILE = '622 918 374'  # Spain
MOBILE_E164 = '+34622918374'

# A valid step 1, without the email and passwords (see _step1).
STEP1 = {
    'title': 'dr',
    'first_name': FIRST_NAME,
    'middle_name': '',
    'last_name': LAST_NAME,
    'country': 'ES',
    'phone_prefix': 'ES',
    'phone': MOBILE,
    'how_found': 'linkedin',
    'how_found_other': '',
}


def _step1(client, lang='en', email=EMAIL_ADDRESS, password=PASSWORD, password2=None,
           **fields):
    """Post a valid step 1, with any field replaced through `fields`."""
    return client.post(f'/{lang}/signup/', {
        **STEP1,
        'email': email, 'password1': password,
        'password2': password if password2 is None else password2,
        **fields,
    })


# The legal step: the required Terms and Conditions ticked (see _legal).
LEGAL = {'terms': 'on', 'conditions': 'on'}


def _legal(client, lang='en', **fields):
    """Post the legal step with the Terms and the Conditions ticked, the optional ones
    not, as shown with the current versions, with any field added or replaced through
    `fields` (None leaves it out)."""
    shown = shown_document_ids(LegalDocument.objects.current_by_kind().values())
    data = {name: value for name, value in {**LEGAL, 'shown': shown, **fields}.items()
            if value is not None}
    return client.post(f'/{lang}/signup/legal/', data)


def _to_code_step(client, lang='en', **fields):
    """Steps 1 and 2 (legal): the email code is sent."""
    response = _step1(client, lang=lang, **fields)
    assert response.status_code == 302, response.context['form'].errors
    return _legal(client, lang=lang)


def _code_from_log(caplog):
    lines = [r.getMessage() for r in caplog.records if r.name == 'signup.verification']
    assert lines, 'no code was logged'
    return re.search(r'(\d{6})$', lines[-1]).group(1)


def _wrong(code):
    return f'{(int(code) + 1) % 1_000_000:06d}'


# --- Happy path ------------------------------------------------------------------------

def test_happy_path(client, caplog):
    response = _step1(client)
    assert response.status_code == 302
    assert response['Location'] == '/en/signup/legal/'

    pending = PendingRegistration.objects.get()
    assert pending.email == NORMALIZED
    assert pending.step == 'legal'
    assert client.session[SESSION_KEY] == str(pending.pk)
    assert not caplog.records  # no code before the legal step is saved

    response = _legal(client)
    assert response['Location'] == '/en/signup/verify-email/'
    pending.refresh_from_db()
    assert pending.step == 'verify-email'
    assert not User.objects.exists()

    page = client.get('/en/signup/verify-email/')
    assert page.status_code == 200
    assert NORMALIZED in page.content.decode()

    response = client.post('/en/signup/verify-email/', {'code': _code_from_log(caplog)})
    assert response.status_code == 302
    assert response['Location'] == '/en/welcome/'

    user = User.objects.get()
    assert user.email == NORMALIZED
    assert user.check_password(PASSWORD)
    assert not user.is_staff and not user.is_superuser
    assert not PendingRegistration.objects.exists()
    assert SESSION_KEY not in client.session

    welcome = client.get('/en/welcome/')
    assert welcome.status_code == 200
    assert welcome.wsgi_request.user == user
    assert NORMALIZED in welcome.content.decode()


def test_user_gets_the_pending_registrations_hash(client, caplog):
    _to_code_step(client)
    stored_hash = PendingRegistration.objects.get().password
    client.post('/en/signup/verify-email/', {'code': _code_from_log(caplog)})

    assert User.objects.get().password == stored_hash


def _assert_no_plaintext_anywhere(*plaintexts):
    with connection.cursor() as cursor:
        for table in connection.introspection.table_names():
            cursor.execute(f'SELECT * FROM {connection.ops.quote_name(table)}')
            dump = repr(cursor.fetchall()).lower()
            for plaintext in plaintexts:
                assert plaintext.lower() not in dump, table


ENCRYPTED = re.compile(r'^enc:v1:[\w-]+:[A-Za-z0-9+/=]+$')


def test_no_table_holds_the_plaintext_password_email_names_mobile_or_code(client, caplog):
    _to_code_step(client)
    pending_hash = PendingRegistration.objects.get().password
    assert pending_hash != PASSWORD and check_password(PASSWORD, pending_hash)
    code = _code_from_log(caplog)
    personal = (FIRST_NAME, LAST_NAME, MOBILE_E164)
    _assert_no_plaintext_anywhere(PASSWORD, NORMALIZED, f"'{code}'", *personal)
    client.post('/en/signup/verify-email/', {'code': code})
    assert User.objects.exists()

    # The code is 6 digits, so only look for it as a whole value in text columns.
    _assert_no_plaintext_anywhere(PASSWORD, NORMALIZED, f"'{code}'", *personal)


def test_rows_hold_ciphertext_index_and_argon2id(client, caplog):
    """Story 2.2: raw rows show `enc:v1:...` for the email and names, a hex blind index,
    and an Argon2id password hash; never the plaintext."""
    from accounts.crypto import email_index

    _to_code_step(client)
    with connection.cursor() as cursor:
        cursor.execute('SELECT email, email_index, password FROM signup_pendingregistration')
        email, index, password = cursor.fetchone()
    assert ENCRYPTED.match(email)
    assert index == email_index(NORMALIZED) and re.fullmatch(r'[0-9a-f]{64}', index)
    assert password.startswith('argon2$argon2id$')

    client.post('/en/signup/verify-email/', {'code': _code_from_log(caplog)})
    with connection.cursor() as cursor:
        cursor.execute('SELECT email, first_name, last_name, email_index, password '
                       'FROM accounts_user')
        email, first_name, last_name, index, password = cursor.fetchone()
    assert all(ENCRYPTED.match(value) for value in (email, first_name, last_name))
    assert index == email_index(NORMALIZED)
    assert password.startswith('argon2$argon2id$')


def test_session_holds_only_the_pending_id(client):
    _to_code_step(client)
    session = dict(client.session)
    assert session[SESSION_KEY] == str(PendingRegistration.objects.get().pk)
    assert PASSWORD not in repr(session)


# --- Invalid step 1 --------------------------------------------------------------------

@pytest.mark.parametrize('data, field', [
    ({'email': 'not-an-email'}, 'email'),
    ({'email': ''}, 'email'),
    ({'password2': 'something-else-entirely'}, 'password2'),
    ({'password': 'short-pw'}, 'password1'),            # under 12 characters
    ({'password': 'password1234'}, 'password1'),        # common password
    ({'password': '123456789012345'}, 'password1'),     # entirely numeric
    ({'password': 'ana.garcia@example'}, 'password1'),  # too similar to the email
])
def test_invalid_step1_stays_with_field_errors(client, data, field):
    response = _step1(client, **data)

    assert response.status_code == 200
    assert response.context['form'].errors.get(field), response.context['form'].errors
    assert not PendingRegistration.objects.exists()
    assert SESSION_KEY not in client.session
    assert 'aria-invalid="true"' in response.content.decode()


def test_minimum_password_length_is_12(client):
    assert _step1(client, password='abcdefghijk').status_code == 200        # 11
    assert _step1(client, password='zebra-orbit-').status_code == 302      # 12


def test_password_is_not_echoed_back(client):
    response = _step1(client, email='not-an-email')
    assert PASSWORD not in response.content.decode()


# --- Registered email ------------------------------------------------------------------

@pytest.mark.parametrize('typed', [NORMALIZED, '  ana.garcia@EXAMPLE.com ', 'Ana.Garcia@example.com'])
def test_registered_email_is_refused(client, typed):
    User.objects.create_user(NORMALIZED, 'another-password-123')

    response = _step1(client, email=typed)

    assert response.status_code == 200
    assert response.context['form'].errors['email'] == [
        'This email address is already registered.']
    assert not PendingRegistration.objects.exists()


def test_email_registered_meanwhile_is_refused_at_the_code_step(client, caplog):
    _to_code_step(client)
    User.objects.create_user(NORMALIZED, 'another-password-123')

    response = client.post('/en/signup/verify-email/', {'code': _code_from_log(caplog)})

    assert response.status_code == 200
    assert 'This email address is already registered.' in response.content.decode()
    assert User.objects.count() == 1
    assert '_auth_user_id' not in client.session


# --- Wrong code ------------------------------------------------------------------------

def test_wrong_code_keeps_the_step(client, caplog):
    _to_code_step(client)
    code = _code_from_log(caplog)

    response = client.post('/en/signup/verify-email/', {'code': _wrong(code)})

    assert response.status_code == 200
    assert response.context['form'].errors['code'] == [
        'This code is not correct. Check it, or send a new one.']
    assert not User.objects.exists()
    assert PendingRegistration.objects.get().step == 'verify-email'
    # The right code still works afterwards (no attempt limits until entry 7).
    assert client.post('/en/signup/verify-email/', {'code': code})['Location'] == '/en/welcome/'


def test_empty_code_is_a_field_error(client):
    _to_code_step(client)
    response = client.post('/en/signup/verify-email/', {'code': ''})
    assert response.status_code == 200 and response.context['form'].errors['code']


def test_resend_sends_a_new_code(client, caplog):
    _to_code_step(client)
    caplog.clear()

    response = client.post('/en/signup/verify-email/', {'action': 'resend'})

    assert response.status_code == 302
    assert response['Location'] == '/en/signup/verify-email/'
    code = _code_from_log(caplog)
    page = client.get('/en/signup/verify-email/')
    assert 'We have sent you a new code.' in page.content.decode()
    assert client.post('/en/signup/verify-email/', {'code': code})['Location'] == '/en/welcome/'


def test_code_is_sent_to_the_pending_email_in_the_page_language(client, monkeypatch):
    sent = []
    monkeypatch.setattr(FakeVerificationProvider, 'send',
                        lambda self, *args: sent.append(args))
    _to_code_step(client, lang='es')
    assert sent == [(EMAIL, NORMALIZED, 'signup', 'es')]


# --- Skipping ahead and going back -----------------------------------------------------

def test_code_step_without_pending_registration_redirects_to_step1(client):
    response = client.get('/en/signup/verify-email/')
    assert response.status_code == 302
    assert response['Location'] == '/en/signup/'

    response = client.post('/en/signup/verify-email/', {'code': '123456'})
    assert response['Location'] == '/en/signup/'
    assert not User.objects.exists()


def test_stale_session_id_redirects_to_step1(client):
    _step1(client)
    PendingRegistration.objects.all().delete()

    assert client.get('/en/signup/verify-email/')['Location'] == '/en/signup/'
    assert SESSION_KEY not in client.session


def test_unknown_step_is_404(client):
    assert client.get('/en/signup/no-such-step/').status_code == 404


def test_first_step_slug_redirects_to_signup(client):
    assert client.get('/en/signup/account/')['Location'] == '/en/signup/'


def test_back_to_step1_keeps_the_email_and_resends(client, caplog):
    _to_code_step(client)
    page = client.get('/en/signup/')
    assert f'value="{NORMALIZED}"' in page.content.decode()

    caplog.clear()
    # The legal step is done (current Terms and Conditions accepted): skipped.
    response = _step1(client, email='bea@example.com')
    assert response['Location'] == '/en/signup/verify-email/'
    pending = PendingRegistration.objects.get()
    assert pending.email == 'bea@example.com'
    client.post('/en/signup/verify-email/', {'code': _code_from_log(caplog)})
    assert User.objects.get().email == 'bea@example.com'


# --- Step registry ---------------------------------------------------------------------

def test_registry_order():
    assert [s.slug for s in signup_steps.STEPS] == ['account', 'legal', 'verify-email']
    for step in signup_steps.STEPS:
        assert step.form_class and step.template_name and step.title


def test_steps_come_from_the_registry(client, caplog, monkeypatch):
    """A step added to the registry is served and enforced without changing the views."""
    class ExtraStep(signup_steps.Step):
        slug = 'extra'
        form_class = signup_steps.EmailCodeForm
        template_name = 'signup/step_verify_email.html'
        title = 'Extra'

        def get_form_kwargs(self, pending):
            return {'email': pending.email}

        def save(self, request, pending, form):
            return pending

    monkeypatch.setattr(signup_steps, 'STEPS', [*signup_steps.STEPS, ExtraStep()])
    monkeypatch.setattr('signup.views.STEPS', signup_steps.STEPS)

    assert client.get('/en/signup/extra/')['Location'] == '/en/signup/'
    _step1(client)
    assert client.get('/en/signup/extra/')['Location'] == '/en/signup/legal/'
    _legal(client)
    assert client.get('/en/signup/extra/')['Location'] == '/en/signup/verify-email/'
    code = _code_from_log(caplog)
    response = client.post('/en/signup/verify-email/', {'code': code})
    assert response['Location'] == '/en/signup/extra/'
    assert not User.objects.exists()
    assert 'Step 4 of 4' in client.get('/en/signup/extra/').content.decode()
    assert client.post('/en/signup/extra/', {'code': code})['Location'] == '/en/welcome/'
    assert User.objects.exists()


# --- Pages -----------------------------------------------------------------------------

def test_step1_page(client):
    response = client.get('/en/signup/')
    html = response.content.decode()

    assert response.status_code == 200
    assert 'Step 1 of 3' in html
    assert 'autocomplete="new-password"' in html
    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert 'href="/es/signup/"' in html  # language switcher


def test_spanish_wizard(client, caplog):
    html = client.get('/es/signup/').content.decode()
    assert '<html lang="es"' in html
    assert 'Paso 1 de 3' in html
    assert 'Cree su cuenta' in html
    assert 'Create your account' not in html

    response = _step1(client, lang='es', password2='otra-contrasena-distinta')
    assert 'Las dos contraseñas no coinciden.' in response.content.decode()

    response = _step1(client, lang='es')
    assert response['Location'] == '/es/signup/legal/'
    html = client.get('/es/signup/legal/').content.decode()
    assert 'Términos y consentimiento' in html and 'Paso 2 de 3' in html
    assert 'Términos de uso (borrador)' in html and 'Terms of Use' not in html

    response = _legal(client, lang='es', conditions=None)
    assert 'Debe aceptar las Condiciones para continuar.' in response.content.decode()

    response = _legal(client, lang='es')
    assert response['Location'] == '/es/signup/verify-email/'
    html = client.get('/es/signup/verify-email/').content.decode()
    assert 'Revise su correo' in html and 'Paso 3 de 3' in html

    response = client.post('/es/signup/verify-email/', {'code': _code_from_log(caplog)})
    assert response['Location'] == '/es/welcome/'
    assert 'Le damos la bienvenida a MyTreatPlan' in client.get('/es/welcome/').content.decode()


# --- Portal off ------------------------------------------------------------------------

@pytest.mark.parametrize('url', ['/en/signup/', '/es/signup/', '/en/signup/verify-email/',
                                 '/en/welcome/'])
def test_portal_off_is_404(client, settings, url):
    settings.ROOT_URLCONF = 'mytreatplan.urls'  # the real URLconf, built with the switch off
    assert not settings.PORTAL_ENABLED
    assert client.get(url).status_code == 404
