"""Story 2.4: the complete step 1. Every row of the plan's I/O matrix, with Have I Been
Pwned and Debounce replaced by the fakes of conftest.py (`external_checks`)."""
import hashlib
import io
import json
import urllib.error

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.db import connection

from accounts.crypto import phone_index
from accounts.password_validation import (BreachedPasswordValidator, fetch_pwned_range,
                                          pwned_count)
from conftest import NetworkAccessInTests
from eventlog.models import Attempt, Event
from signup.checks import fetch_disposable, is_disposable_domain
from signup.models import PendingRegistration

from .test_wizard import (ENCRYPTED, FIRST_NAME, LAST_NAME, MOBILE_E164, NORMALIZED, PASSWORD,
                          _code_from_log, _legal, _step1, _to_code_step)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()


def _errors(response):
    return response.context['form'].errors


def _events():
    return list(Attempt.objects.get().events.all())


# --- Valid form --------------------------------------------------------------------------

def test_valid_form_stores_ciphertext_codes_and_the_phone_index(client):
    response = _step1(client)
    assert response.status_code == 302, _errors(response)

    pending = PendingRegistration.objects.get()
    assert (pending.title, pending.first_name, pending.middle_name, pending.last_name) == (
        'dr', FIRST_NAME, '', LAST_NAME)
    assert pending.country.code == 'ES'
    assert (pending.phone_region, pending.phone) == ('ES', MOBILE_E164)
    assert (pending.how_found, pending.how_found_other) == ('linkedin', '')

    with connection.cursor() as cursor:
        cursor.execute('SELECT email, first_name, middle_name, last_name, phone, phone_index, '
                       'how_found_other, title, country, how_found '
                       'FROM signup_pendingregistration')
        (email, first_name, middle_name, last_name, phone, index, other,
         title, country, how_found) = cursor.fetchone()
    for stored in (email, first_name, middle_name, last_name, phone, other):
        assert ENCRYPTED.match(stored)
    assert FIRST_NAME not in first_name and MOBILE_E164 not in phone
    assert index == phone_index(MOBILE_E164)
    assert (title, country, how_found) == ('dr', 'ES', 'linkedin')  # codes


def test_middle_name_is_optional_and_kept_when_given(client):
    assert _step1(client, middle_name='J.').status_code == 302
    assert PendingRegistration.objects.get().middle_name == 'J.'


@pytest.mark.parametrize('field', ['title', 'first_name', 'last_name', 'country', 'email',
                                   'password1', 'password2', 'phone_prefix', 'phone',
                                   'how_found'])
def test_every_other_field_is_mandatory(client, field):
    response = _step1(client, **{field: ''})
    assert response.status_code == 200
    assert _errors(response)[field] == ['This field is required.']
    assert not PendingRegistration.objects.exists()


def test_unknown_choice_is_refused(client):
    response = _step1(client, title='king', country='XX', how_found='tv')
    assert set(_errors(response)) == {'title', 'country', 'how_found'}


def test_back_to_step1_refills_every_field_but_the_passwords(client):
    _step1(client, middle_name='J.', how_found='others', how_found_other='A colleague')
    form = client.get('/en/signup/').context['form']
    assert form.initial == {
        'title': 'dr', 'first_name': FIRST_NAME, 'middle_name': 'J.', 'last_name': LAST_NAME,
        'country': 'ES', 'email': NORMALIZED, 'phone_prefix': 'ES', 'phone': '622 91 83 74',
        'how_found': 'others', 'how_found_other': 'A colleague',
    }
    # The refilled number is accepted again as it is shown.
    assert _step1(client, phone=form.initial['phone']).status_code == 302


# --- Others ------------------------------------------------------------------------------

def test_others_needs_its_text(client):
    response = _step1(client, how_found='others', how_found_other='')
    assert _errors(response) == {
        'how_found_other': ['Please tell us how you found out about us.']}
    assert [(e.event, e.field, e.reason) for e in _events()] == [
        ('rejected', 'how_found_other', 'required')]


def test_others_text_is_stored_with_others(client):
    assert _step1(client, how_found='others', how_found_other='A colleague').status_code == 302
    pending = PendingRegistration.objects.get()
    assert (pending.how_found, pending.how_found_other) == ('others', 'A colleague')


def test_others_text_is_ignored_without_others(client):
    assert _step1(client, how_found='whatsapp', how_found_other='Ignored text').status_code == 302
    assert PendingRegistration.objects.get().how_found_other == ''
    assert _events()[0].data['how_found_other'] == ''


# --- Mobile ------------------------------------------------------------------------------

@pytest.mark.parametrize('prefix, number', [
    ('GB', '612 345 678'),        # a Spanish mobile with the UK prefix
    ('ES', '912 345 678'),        # a Spanish fixed line
    ('ES', '61234'),              # too short
    ('ES', 'not a number'),
    ('ES', '+44 7700 900123'),    # another country's prefix typed in the number
    ('ES', '612345678 ext 5'),    # an extension (E.164 would drop it)
    ('ES', '612345678x'),
])
def test_invalid_mobile_is_refused(client, prefix, number):
    response = _step1(client, phone_prefix=prefix, phone=number)
    assert _errors(response) == {
        'phone': ['Enter a valid mobile phone number for the selected prefix.']}
    [event] = _events()
    assert (event.event, event.field, event.reason) == ('rejected', 'phone', 'phone_invalid')


@pytest.mark.parametrize('prefix, number, e164', [
    ('ES', '+34 612 345 678', '+34612345678'),
    ('ES', '612-345-678', '+34612345678'),
    ('GB', '07911 123456', '+447911123456'),
    ('US', '(201) 555-0123', '+12015550123'),  # +1 does not tell mobiles from fixed lines
])
def test_mobile_is_stored_in_e164(client, prefix, number, e164):
    assert _step1(client, phone_prefix=prefix, phone=number).status_code == 302
    pending = PendingRegistration.objects.get()
    assert pending.phone == e164 and pending.phone_index == phone_index(e164)


def test_prefix_options_show_the_country_and_its_calling_code(client):
    html = client.get('/en/signup/').content.decode()
    assert '<option value="ES">Spain (+34)</option>' in html
    assert '<option value="AE">United Arab Emirates (+971)</option>' in html


# --- Breached password -------------------------------------------------------------------

def test_breached_password_is_refused(client, external_checks):
    external_checks.breached.add(PASSWORD)

    response = _step1(client)

    assert _errors(response)['password1'] == [
        'This password has appeared in a data breach, so it is not safe to use. '
        'Please choose another one.']
    [event] = _events()
    assert (event.event, event.field, event.reason) == ('rejected', 'password1',
                                                        'password_breached')
    assert PASSWORD not in json.dumps(event.data)
    # Only the first 5 hex characters of the SHA-1 left the server.
    digest = hashlib.sha1(PASSWORD.encode()).hexdigest().upper()
    assert external_checks.pwned_prefixes == [digest[:5]]


def test_breached_password_validator_is_a_default_validator(external_checks):
    """So createsuperuser and later password changes use it too."""
    assert any(isinstance(v, BreachedPasswordValidator)
               for v in password_validation.get_default_password_validators())
    external_checks.breached.add('correct-horse-battery')
    with pytest.raises(ValidationError) as error:
        password_validation.validate_password('correct-horse-battery')
    assert [e.code for e in error.value.error_list] == ['password_breached']


# --- Similar to the name -----------------------------------------------------------------

@pytest.mark.parametrize('password, fields', [
    ('garcia-montenegro', {}),                         # the surnames
    ('maximiliano1', {'first_name': 'Maximiliano'}),    # the name
    ('bartholomew99', {'middle_name': 'Bartholomew'}),  # the middle name
])
def test_password_like_the_name_is_refused(client, password, fields):
    response = _step1(client, password=password, **fields)
    assert response.status_code == 200
    errors = response.context['form'].errors.as_data()['password1']
    assert 'password_too_similar' in [e.code for e in errors]
    assert 'too similar' in str(_errors(response)['password1'])


# --- Have I Been Pwned down --------------------------------------------------------------

@pytest.mark.parametrize('error', [
    TimeoutError('timed out'),
    urllib.error.HTTPError('https://x/', 503, 'Service Unavailable', {}, None),
    urllib.error.URLError('down'),
])
def test_hibp_down_accepts_the_password_and_logs_it(client, external_checks, error):
    external_checks.pwned_error = error

    assert _step1(client).status_code == 302

    events = _events()
    assert [(e.event, e.field, e.reason) for e in events] == [
        ('check_unavailable', 'password1', 'breached_password'),
        ('step_saved', '', ''),
    ]
    assert PASSWORD not in json.dumps(events[0].data)


def test_hibp_down_does_not_save_a_password_the_other_rules_refuse(client, external_checks):
    external_checks.pwned_error = TimeoutError()
    response = _step1(client, password='password1234')  # too common
    assert response.status_code == 200
    assert {e.event for e in _events()} == {'check_unavailable', 'rejected'}


# --- Disposable email --------------------------------------------------------------------

def test_disposable_email_is_refused(client, external_checks):
    external_checks.disposable.add('mailinator.com')

    response = _step1(client, email='Ana.Garcia@Mailinator.com')

    assert _errors(response) == {'email': [
        'Please use your professional email address: disposable email addresses are not '
        'accepted.']}
    [event] = _events()
    assert (event.event, event.field, event.reason) == ('rejected', 'email',
                                                        'email disposable')
    # Only the domain left the server.
    assert external_checks.debounce_domains == ['mailinator.com']
    assert not PendingRegistration.objects.exists()


def test_registered_email_is_not_sent_to_debounce(client, external_checks):
    User.objects.create_user(NORMALIZED, 'another-password-123')
    _step1(client)
    assert external_checks.debounce_domains == []


# --- Debounce down -----------------------------------------------------------------------

@pytest.mark.parametrize('error', [
    TimeoutError('timed out'),
    urllib.error.HTTPError('https://x/', 500, 'Server Error', {}, None),
    json.JSONDecodeError('Expecting value', '<html>', 0),
])
def test_debounce_down_accepts_the_email_and_logs_it(client, external_checks, error):
    external_checks.debounce_error = error

    assert _step1(client).status_code == 302

    events = _events()
    assert [(e.event, e.field, e.reason) for e in events][:2] == [
        ('check_unavailable', 'email', 'disposable_email'),
        ('step_saved', '', ''),
    ]
    assert events[0].data['email'] == NORMALIZED


@pytest.mark.parametrize('answer', [{}, {'disposable': 'maybe'}, ['true'], {'disposable': None}])
def test_debounce_unexpected_body_accepts_the_email(client, monkeypatch, answer):
    monkeypatch.setattr('signup.checks.fetch_disposable', lambda domain: answer)
    assert _step1(client).status_code == 302
    assert _events()[0].event == 'check_unavailable'


def test_both_checks_down(client, external_checks):
    external_checks.pwned_error = TimeoutError()
    external_checks.debounce_error = TimeoutError()
    assert _step1(client).status_code == 302
    assert [(e.event, e.field) for e in _events()][:3] == [
        ('check_unavailable', 'email'), ('check_unavailable', 'password1'), ('step_saved', '')]


# --- Spanish -----------------------------------------------------------------------------

def test_spanish_labels_and_errors(client, external_checks):
    html = client.get('/es/signup/').content.decode()
    for label in ('Tratamiento', 'Nombre', 'Segundo nombre o inicial (opcional)', 'Apellido(s)',
                  'País / Región', 'Prefijo internacional', 'Número de teléfono móvil',
                  '¿Cómo nos conoció?', 'Otros: especifique', 'Dr./Dra.', 'Llamada telefónica',
                  '>España<', 'España (+34)', 'Seguridad: muy débil'):
        assert label in html, label
    for english in ('Title / Treatment', 'Surname(s)', 'International prefix', '>Spain<'):
        assert english not in html, english

    response = _step1(client, lang='es', first_name='', phone='123',
                      how_found='others', how_found_other='')
    html = response.content.decode()
    assert 'Este campo es obligatorio.' in html
    assert 'Introduzca un número de teléfono móvil válido para el prefijo seleccionado.' in html
    assert 'Indíquenos cómo nos conoció.' in html

    external_checks.disposable.add('example.com')
    external_checks.breached.add(PASSWORD)
    html = _step1(client, lang='es').content.decode()
    assert 'Utilice su dirección de correo electrónico profesional' in html
    assert 'Esta contraseña ha aparecido en una filtración de datos' in html

    html = _step1(client, lang='es', email='ana@other.org',
                  password='garcia-montenegro').content.decode()
    assert 'La contraseña es demasiado similar' in html


# --- Account created ---------------------------------------------------------------------

def test_account_gets_the_step1_details(client, caplog):
    _to_code_step(client, middle_name='J.', how_found='others', how_found_other='A colleague')
    client.post('/en/signup/verify-email/', {'code': _code_from_log(caplog)})

    user = User.objects.get()
    assert (user.title, user.first_name, user.middle_name, user.last_name) == (
        'dr', FIRST_NAME, 'J.', LAST_NAME)
    assert user.country.code == 'ES'
    assert user.phone == MOBILE_E164 and user.phone_index == phone_index(MOBILE_E164)
    assert (user.how_found, user.how_found_other) == ('others', 'A colleague')
    with connection.cursor() as cursor:
        cursor.execute('SELECT first_name, middle_name, last_name, phone, how_found_other '
                       'FROM accounts_user')
        assert all(ENCRYPTED.match(value) for value in cursor.fetchone())


def test_user_phone_index_follows_the_phone():
    user = User.objects.create_user('ana@example.com', PASSWORD)
    assert user.phone_index == ''
    user.phone = MOBILE_E164
    user.save(update_fields=['phone'])
    user.refresh_from_db()
    assert user.phone_index == phone_index(MOBILE_E164)


# --- Page --------------------------------------------------------------------------------

def test_step1_page_loads_the_strength_meter_and_the_others_toggle(client):
    html = client.get('/en/signup/').content.decode()
    for script in ('js/zxcvbn-ts-core.js', 'js/zxcvbn-ts-common.js', 'js/signup-account.js'):
        assert script in html
    assert 'id="password-strength"' in html and ' hidden' in html
    # The Others field is in the page (visible without JavaScript), tied to the select.
    assert 'data-show-if-field="id_how_found" data-show-if-value="others"' in html
    assert 'name="how_found_other"' in html


def test_strength_meter_is_loaded_on_step1_only(client):
    _step1(client)
    html = client.get('/en/signup/legal/').content.decode()
    assert 'zxcvbn' not in html and 'signup-account.js' not in html
    _legal(client)
    html = client.get('/en/signup/verify-email/').content.decode()
    assert 'zxcvbn' not in html and 'signup-account.js' not in html


def test_zxcvbn_is_vendored_and_pinned():
    package = json.loads((settings.BASE_DIR / 'package.json').read_text())
    for name in ('@zxcvbn-ts/core', '@zxcvbn-ts/language-common'):
        assert package['devDependencies'][name][0].isdigit()  # an exact version
    assert 'vendor:zxcvbn' in package['scripts']
    for name in ('zxcvbn-ts-core.js', 'zxcvbn-ts-common.js'):
        assert (settings.BASE_DIR / 'static' / 'js' / name).stat().st_size > 10_000


def test_requirements_pin_countries_and_phonenumbers():
    lines = (settings.BASE_DIR / 'requirements.txt').read_text().splitlines()
    assert any(line.startswith('django-countries==') for line in lines)
    assert any(line.startswith('phonenumbers==') for line in lines)


# --- The HTTP calls themselves -----------------------------------------------------------

class FakeResponse(io.BytesIO):
    status = 200


@pytest.fixture
def urlopen(monkeypatch):
    """Capture urllib requests; `answer` is the body (bytes) or an exception to raise."""
    calls = []

    class Opener:
        answer = b''

        def __call__(self, request, timeout=None):
            calls.append((request, timeout))
            if isinstance(self.answer, BaseException):
                raise self.answer
            return FakeResponse(self.answer)

    opener = Opener()
    opener.calls = calls
    monkeypatch.setattr('urllib.request.urlopen', opener)
    monkeypatch.setattr('accounts.password_validation.fetch_pwned_range', fetch_pwned_range)
    monkeypatch.setattr('signup.checks.fetch_disposable', fetch_disposable)
    return opener


def test_hibp_request_sends_only_the_hash_prefix(urlopen):
    digest = hashlib.sha1(PASSWORD.encode()).hexdigest().upper()
    urlopen.answer = f'{digest[5:]}:7\r\n0123456789ABCDEF0123456789ABCDEF012:0'.encode()

    assert pwned_count(PASSWORD) == 7

    [(request, timeout)] = urlopen.calls
    assert request.full_url == settings.PWNED_PASSWORDS_API_URL + digest[:5]
    assert request.get_header('Add-padding') == 'true'
    assert request.get_header('User-agent').startswith('MyTreatPlan')
    assert timeout == settings.EXTERNAL_CHECK_TIMEOUT == 3
    assert digest[5:] not in request.full_url and PASSWORD not in request.full_url


def test_hibp_padding_entries_are_not_breaches(urlopen):
    digest = hashlib.sha1(PASSWORD.encode()).hexdigest().upper()
    urlopen.answer = f'{digest[5:]}:0\r\nFFFFF:3'.encode()
    assert pwned_count(PASSWORD) == 0


@pytest.mark.parametrize('answer', [
    TimeoutError('timed out'),
    urllib.error.HTTPError('https://x/', 502, 'Bad Gateway', {}, None),
    urllib.error.URLError('no route'),
    b'',            # no entries at all: not a real range response
    b'\xff\xfe',    # not UTF-8
])
def test_hibp_failure_is_unknown(urlopen, answer):
    urlopen.answer = answer
    assert pwned_count(PASSWORD) is None
    BreachedPasswordValidator().validate(PASSWORD)  # accepted


def test_debounce_request_sends_only_the_domain(urlopen):
    urlopen.answer = b'{"disposable":"true"}'

    assert is_disposable_domain('mailinator.com') is True

    [(request, timeout)] = urlopen.calls
    assert request.full_url == (settings.DISPOSABLE_EMAIL_API_URL
                                + '?email=check%40mailinator.com')
    assert request.get_header('User-agent').startswith('MyTreatPlan')
    assert timeout == 3


@pytest.mark.parametrize('answer, expected', [
    (b'{"disposable":"false"}', False),
    (b'{"disposable":false}', False),
    (b'{"disposable":true}', True),
    (b'{"error":"rate limited"}', None),
    (b'<html>busy</html>', None),
    (TimeoutError(), None),
])
def test_debounce_answers(urlopen, answer, expected):
    urlopen.answer = answer
    assert is_disposable_domain('example.com') is expected


def test_real_calls_fail_in_tests(monkeypatch):
    """The guard in conftest.py: a real request fails the test (it is not swallowed as
    "check unavailable")."""
    monkeypatch.setattr('accounts.password_validation.fetch_pwned_range', fetch_pwned_range)
    monkeypatch.setattr('signup.checks.fetch_disposable', fetch_disposable)
    with pytest.raises(NetworkAccessInTests):
        pwned_count(PASSWORD)
    with pytest.raises(NetworkAccessInTests):
        is_disposable_domain('example.com')
    assert 'invalid' in settings.PWNED_PASSWORDS_API_URL
    assert 'invalid' in settings.DISPOSABLE_EMAIL_API_URL
    assert not Event.objects.exists()
