"""The code-confirmed mobile and contact-email changes (epic-doctor-profile entry 3): every
row of the plan's I/O matrix (the pages accounts.views settings_mobile / settings_email and
their code pages, the service accounts.profile.confirm_contact_change, the remembered
devices, the per-account limit, the staff admin and the personal-data page). The
migration's duplicate stop is in test_phone_unique_migration.py; the browser journey in
browser/test_contact_change.py."""
import json
import logging
import re
from datetime import timedelta

import pytest
from django.contrib.auth import SESSION_KEY, get_user_model
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import Client

from accounts import devices, login_failures, profile
from accounts.admin import UserChangeAdminForm
from accounts.models import PendingContactChange, Profile
from accounts.views import CHANGE_SESSION_KEY
from eventlog.models import Event
from integration.models import OutboxEvent
from signup import limits
from signup.forms import BLOCKED_MESSAGE
from signup.steps import CODE_STATE_SESSION_KEY
from signup.tests.helpers import MOBILE_E164, NORMALIZED, PASSWORD, _current_code, _wrong
from signup.verification import EMAIL, SMS

from .helpers import HOME, VERIFY, _html, _login, _login_remembering, _logged_in

pytestmark = [pytest.mark.django_db,
              pytest.mark.usefixtures('capture_codes', 'clear_cache', 'portal_urls')]

User = get_user_model()

MOBILE_PAGE, MOBILE_CODE = '/en/settings/mobile/', '/en/settings/mobile/code/'
EMAIL_PAGE, EMAIL_CODE = '/en/settings/email/', '/en/settings/email/code/'
NEW_MOBILE, NEW_E164 = '622 918 375', '+34622918375'
NEW_EMAIL = 'clinic@example.org'
PURPOSE = profile.CHANGE_PURPOSE
IN_USE = 'This mobile phone number is already used by another account.'


@pytest.fixture(autouse=True)
def inline_notice(monkeypatch):
    """The notice mail is sent in the request (not a thread)."""
    from accounts import password_reset

    monkeypatch.setattr(password_reset, 'in_background', lambda function: function())


def _browser(user):
    """A browser signed in as `user`. The outbox starts empty here (signup's own
    client.updated is not this test's)."""
    OutboxEvent.objects.all().delete()
    browser = Client()
    browser.force_login(user)
    return browser


def _start_mobile(browser, number=NEW_MOBILE, prefix='ES', password=PASSWORD, lang='en'):
    return browser.post(f'/{lang}/settings/mobile/',
                        {'phone_prefix': prefix, 'phone': number, 'password': password})


def _start_email(browser, email=NEW_EMAIL, password=PASSWORD, lang='en'):
    return browser.post(f'/{lang}/settings/email/', {'email': email, 'password': password})


def _mobile_code(phone=NEW_E164):
    return _current_code(SMS, phone, PURPOSE)


def _email_code(email=NEW_EMAIL):
    return _current_code(EMAIL, email, PURPOSE)


def _events(user):
    return list(OutboxEvent.objects.filter(aggregate_id=user.uid).order_by('id'))


def _sent_codes(capture_codes, contact):
    return [r for r in capture_codes.records if r.name == 'signup.verification'
            and contact in r.getMessage() and f'({PURPOSE},' in r.getMessage()]


def _errors(response, field):
    return response.context['form'].errors.get(field, [])


# --- Start a mobile change ------------------------------------------------------------------

def test_start_keeps_the_new_mobile_pending_and_sends_it_a_code(doctor, capture_codes):
    browser = _browser(doctor)
    assert browser.get(MOBILE_PAGE).status_code == 200

    response = _start_mobile(browser)

    assert response.status_code == 302 and response['Location'] == MOBILE_CODE
    change = PendingContactChange.objects.get(user=doctor)
    assert (change.kind, change.value) == ('mobile', NEW_E164)
    assert browser.session[CHANGE_SESSION_KEY] == {'mobile': change.pk}
    assert len(_sent_codes(capture_codes, NEW_E164)) == 1
    page = browser.get(MOBILE_CODE)
    assert page.status_code == 200
    # As signup's: by WhatsApp first (the fake provider has it), with "send by SMS".
    assert 'data-sent="whatsapp"' in _html(page) and NEW_E164 in _html(page)
    assert 'data-sms' in _html(page)
    doctor.refresh_from_db()
    assert doctor.phone == MOBILE_E164  # nothing written yet


def test_pending_value_is_encrypted_in_the_database(doctor):
    _start_mobile(_browser(doctor))
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute('SELECT value, value_index FROM accounts_pendingcontactchange')
        value, index = cursor.fetchone()
    assert NEW_E164 not in value and value.startswith('enc:')
    assert index and NEW_E164 not in index


def test_wrong_password_is_a_form_error_counted_on_the_password_lockout(doctor,
                                                                        capture_codes):
    response = _start_mobile(_browser(doctor), password='wrong-password-123')

    assert response.status_code == 200
    assert 'The password is not correct.' in _errors(response, 'password')
    assert not PendingContactChange.objects.exists()
    assert _sent_codes(capture_codes, NEW_E164) == []
    assert login_failures.failure_count(login_failures.PASSWORD, account_id=doctor.pk) == 1


def test_wrong_password_never_tells_whether_the_mobile_is_in_use(doctor):
    User.objects.create_user('other@example.com', PASSWORD, phone=NEW_E164)

    response = _start_mobile(_browser(doctor), password='wrong-password-123')

    assert set(response.context['form'].errors) == {'password'}


def test_locked_account_gets_the_lockouts_own_message(doctor, capture_codes):
    browser = _browser(doctor)
    for _ in range(login_failures.LOCK_THRESHOLD - 1):
        _start_mobile(browser, password='wrong-password-123')
    response = _start_mobile(browser, password='wrong-password-123')  # the 5th locks
    assert BLOCKED_MESSAGE in response.context['form'].non_field_errors()
    assert login_failures.is_locked(account_id=doctor.pk)

    # While locked even the right password is refused, with that message only.
    response = _start_mobile(browser)

    assert response.context['form'].non_field_errors() == [BLOCKED_MESSAGE]
    assert not PendingContactChange.objects.exists()
    assert _sent_codes(capture_codes, NEW_E164) == []
    assert _logged_in(browser)  # the session stays


def test_mobile_used_by_another_account_is_refused(doctor, capture_codes):
    User.objects.create_user('other@example.com', PASSWORD, phone=NEW_E164)

    response = _start_mobile(_browser(doctor))

    assert IN_USE in _errors(response, 'phone')
    assert not PendingContactChange.objects.exists()
    assert _sent_codes(capture_codes, NEW_E164) == []


def test_an_erased_accounts_mobile_is_free(doctor, settings):
    from .helpers import _erase

    other = User.objects.create_user('other@example.com', PASSWORD, phone=NEW_E164)
    _erase(other, settings)

    assert _start_mobile(_browser(doctor))['Location'] == MOBILE_CODE


def test_same_mobile_is_a_form_error(doctor, capture_codes):
    response = _start_mobile(_browser(doctor), number='622 918 374')

    assert 'This is already your mobile phone number.' in _errors(response, 'phone')
    assert not PendingContactChange.objects.exists()
    assert _sent_codes(capture_codes, MOBILE_E164) == []


@pytest.mark.parametrize('prefix, number, field', [
    ('ES', '12', 'phone'), ('ES', '910 00 00 00', 'phone'), ('ES', '622 918 375 ext 5', 'phone'),
    ('FR', '+34 622 918 375', 'phone'), ('', '622 918 375', 'phone_prefix')])
def test_invalid_mobile_is_a_form_error(doctor, prefix, number, field):
    response = _start_mobile(_browser(doctor), number=number, prefix=prefix)

    assert response.status_code == 200 and _errors(response, field)
    assert not PendingContactChange.objects.exists()


# --- The code -------------------------------------------------------------------------------

def test_right_code_changes_the_mobile_and_keeps_the_session(doctor):
    browser = _browser(doctor)
    _start_mobile(browser)

    response = browser.post(MOBILE_CODE, {'code': _mobile_code()})

    assert response.status_code == 302 and response['Location'] == '/en/settings/'
    doctor.refresh_from_db()
    assert doctor.phone == NEW_E164 and doctor.devices_valid_after is not None
    events = _events(doctor)
    assert [event.type for event in events] == ['client.updated']
    assert events[0].payload['phone'] == NEW_E164
    assert not PendingContactChange.objects.exists()
    assert CHANGE_SESSION_KEY not in browser.session or not browser.session[CHANGE_SESSION_KEY]
    assert _logged_in(browser)
    page = browser.get('/en/settings/')
    assert page.status_code == 200
    assert 'Your mobile phone number has been changed.' in _html(page)
    assert f'value="{NEW_E164}"' in _html(page)
    # The notice, to the login email: what changed, the new number masked.
    assert len(mail.outbox) == 1
    notice = mail.outbox[0]
    assert notice.to == [NORMALIZED]
    assert 'mobile phone number' in notice.subject
    assert '+34 ••• ••• 375' in notice.body
    assert NEW_E164 not in notice.body and '622918375' not in notice.body
    assert 'will ask for a code' in notice.body


def test_a_mobile_change_forgets_every_remembered_device(client, doctor):
    # A browser remembered for login, then logged out.
    assert _login_remembering(client)['Location'] == HOME
    client.post('/en/logout/')
    assert _login(client)['Location'] == HOME  # the mark skips the code
    client.post('/en/logout/')

    browser = _browser(doctor)
    _start_mobile(browser)
    response = browser.post(MOBILE_CODE, {'code': _mobile_code()})
    # This browser's own mark is deleted too.
    assert response.cookies[devices.cookie_name(doctor)].value == ''

    response = _login(client)

    assert response['Location'] == VERIFY  # the mark given before no longer skips the code
    assert not _logged_in(client)


def test_a_mark_given_after_the_change_is_valid(doctor):
    doctor.devices_valid_after = doctor.date_joined
    later = doctor.date_joined.timestamp() + 5
    assert devices.is_valid(devices.issue(doctor, later), doctor, later + 1)
    assert not devices.is_valid(devices.issue(doctor, later - 10), doctor, later + 1)
    # Given in the same second: counts as before (the mark's time is whole seconds).
    same = int(doctor.date_joined.timestamp())
    assert not devices.is_valid(devices.issue(doctor, same), doctor, same + 1)


def test_wrong_codes_kill_the_code_after_the_fifth_and_never_lock(doctor, clock):
    browser = _browser(doctor)
    _start_mobile(browser)
    right = _mobile_code()
    for _ in range(4):
        response = browser.post(MOBILE_CODE, {'code': _wrong(right)})
        assert response.context['form'].has_error('code', 'invalid_code')
    response = browser.post(MOBILE_CODE, {'code': _wrong(right)})  # the 5th: dead
    assert response.context['form'].has_error('code', 'code_expired')

    response = browser.post(MOBILE_CODE, {'code': right})  # the 6th try

    assert response.context['form'].has_error('code', 'code_expired')
    assert 'This code is no longer valid. Send a new code.' in _html(response)
    doctor.refresh_from_db()
    assert doctor.phone == MOBILE_E164 and _events(doctor) == []
    assert not login_failures.is_locked(account_id=doctor.pk)
    assert login_failures.failure_count(login_failures.CODE, account_id=doctor.pk) == 0
    # A new code is needed, and works.
    clock.advance(limits.COOLDOWN_SECONDS)
    browser.post(MOBILE_CODE, {'action': 'resend'})
    assert browser.post(MOBILE_CODE, {'code': _mobile_code()})['Location'] == '/en/settings/'
    # The account can still log in.
    browser.post('/en/logout/')
    assert _login(Client())['Location'] == VERIFY


def test_expired_change_starts_again_and_writes_nothing(doctor, clock):
    browser = _browser(doctor)
    _start_mobile(browser)
    code = _mobile_code()
    PendingContactChange.objects.update(
        created_at=PendingContactChange.objects.get().created_at - timedelta(minutes=11))
    clock.advance(11 * 60)

    response = browser.post(MOBILE_CODE, {'code': code}, follow=True)

    assert response.redirect_chain[-1][0] == MOBILE_PAGE
    assert 'This change has expired. Please start again.' in _html(response)
    doctor.refresh_from_db()
    assert doctor.phone == MOBILE_E164 and _events(doctor) == []
    assert not PendingContactChange.objects.exists()


def test_a_gone_pending_row_starts_again(doctor):
    browser = _browser(doctor)
    _start_mobile(browser)
    PendingContactChange.objects.all().delete()

    response = browser.post(MOBILE_CODE, {'code': '123456'}, follow=True)

    assert 'This change has expired. Please start again.' in _html(response)
    doctor.refresh_from_db()
    assert doctor.phone == MOBILE_E164


def test_code_page_without_a_change_goes_to_the_start(doctor):
    response = _browser(doctor).get(MOBILE_CODE)

    assert response['Location'] == MOBILE_PAGE


def test_race_another_account_takes_the_mobile_before_the_check(doctor):
    browser = _browser(doctor)
    _start_mobile(browser)
    User.objects.create_user('other@example.com', PASSWORD, phone=NEW_E164)

    response = browser.post(MOBILE_CODE, {'code': _mobile_code()}, follow=True)

    assert response.redirect_chain[-1][0] == MOBILE_PAGE
    assert IN_USE in _html(response)
    doctor.refresh_from_db()
    assert doctor.phone == MOBILE_E164 and _events(doctor) == []
    assert not PendingContactChange.objects.exists()


def test_race_the_database_rule_is_the_last_guard(doctor, monkeypatch):
    browser = _browser(doctor)
    _start_mobile(browser)
    User.objects.create_user('other@example.com', PASSWORD, phone=NEW_E164)
    # The check misses it (taken between the check and the write).
    monkeypatch.setattr(profile, 'mobile_in_use', lambda phone, user: False)

    result = profile.confirm_contact_change(doctor, 'mobile', browser.session[
        CHANGE_SESSION_KEY]['mobile'])

    assert result.status == profile.IN_USE
    doctor.refresh_from_db()
    assert doctor.phone == MOBILE_E164 and doctor.devices_valid_after is None
    assert _events(doctor) == []


def test_database_refuses_two_accounts_with_one_mobile(doctor):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user('other@example.com', PASSWORD, phone=MOBILE_E164)
    # Any number of accounts without a mobile.
    User.objects.create_user('a@example.com', PASSWORD)
    User.objects.create_user('b@example.com', PASSWORD)


# --- Resend, SMS and the limits -----------------------------------------------------------------

def test_resend_and_send_by_sms(doctor, clock, capture_codes):
    browser = _browser(doctor)
    _start_mobile(browser)
    clock.advance(limits.COOLDOWN_SECONDS)

    response = browser.post(MOBILE_CODE, {'action': 'resend'}, follow=True)
    assert 'We have sent you the code again.' in _html(response)
    clock.advance(limits.COOLDOWN_SECONDS)
    response = browser.post(MOBILE_CODE, {'action': 'sms'}, follow=True)
    assert 'We have sent you the code again by SMS.' in _html(response)
    assert len(_sent_codes(capture_codes, NEW_E164)) == 3
    assert browser.session[CODE_STATE_SESSION_KEY]['settings-mobile']['channel'] == SMS

    # Within the cooldown: not sent, the cooldown notice.
    response = browser.post(MOBILE_CODE, {'action': 'resend'}, follow=True)
    assert 'You can ask for a new code in' in _html(response)
    # The hourly limit (4 per contact): the existing limit message.
    clock.advance(limits.COOLDOWN_SECONDS)
    browser.post(MOBILE_CODE, {'action': 'resend'})
    clock.advance(limits.COOLDOWN_SECONDS)
    response = browser.post(MOBILE_CODE, {'action': 'resend'}, follow=True)
    assert 'You have asked for too many codes.' in _html(response)
    assert len(_sent_codes(capture_codes, NEW_E164)) == limits.HOURLY_SENDS


def test_sms_is_not_offered_for_an_email_change(doctor, capture_codes, clock):
    browser = _browser(doctor)
    _start_email(browser)
    clock.advance(limits.COOLDOWN_SECONDS)

    browser.post(EMAIL_CODE, {'action': 'sms'})

    assert len(_sent_codes(capture_codes, NEW_EMAIL)) == 1
    assert 'data-sms' not in _html(browser.get(EMAIL_CODE))


def test_per_account_limit_whatever_the_numbers(doctor, capture_codes):
    browser = _browser(doctor)
    numbers = [f'+346229183{n:02d}' for n in range(80, 80 + limits.ACCOUNT_CHANGE_SENDS + 1)]
    for number in numbers[:-1]:
        assert _start_mobile(browser, number=number[3:])['Location'] == MOBILE_CODE
        assert len(_sent_codes(capture_codes, number)) == 1

    _start_mobile(browser, number=numbers[-1][3:])

    assert _sent_codes(capture_codes, numbers[-1]) == []
    page = browser.get(MOBILE_CODE)
    assert 'You have asked for too many codes.' in _html(page)
    # An email change shares the account's limit.
    _start_email(browser)
    assert _sent_codes(capture_codes, NEW_EMAIL) == []


def test_per_account_limit_figure_is_two_contacts_worth():
    assert limits.ACCOUNT_CHANGE_SENDS == 2 * limits.HOURLY_SENDS == 8


# --- The contact email ------------------------------------------------------------------------

def test_contact_email_change_after_the_code(doctor, capture_codes):
    browser = _browser(doctor)
    assert browser.get(EMAIL_PAGE).status_code == 200

    response = _start_email(browser, email='Clinic@Example.ORG')

    assert response['Location'] == EMAIL_CODE
    assert len(_sent_codes(capture_codes, 'Clinic@example.org')) == 1
    assert not Profile.objects.filter(user=doctor).exists()  # unchanged before the code
    response = browser.post(EMAIL_CODE, {'code': _current_code(EMAIL, 'Clinic@example.org',
                                                               PURPOSE)})
    assert response['Location'] == '/en/settings/'
    assert Profile.objects.get(user=doctor).alternative_email == 'Clinic@example.org'
    events = _events(doctor)
    assert [event.type for event in events] == ['client.updated']
    assert events[0].payload['alternative_email'] == 'Clinic@example.org'
    doctor.refresh_from_db()
    assert doctor.devices_valid_after is None  # devices are not forgotten
    assert devices.cookie_name(doctor) not in response.cookies
    assert len(mail.outbox) == 1 and mail.outbox[0].to == [NORMALIZED]
    assert 'C•••@example.org' in mail.outbox[0].body
    assert 'Clinic@example.org' not in mail.outbox[0].body
    assert 'Your contact email has been changed.' in _html(browser.get('/en/settings/'))


def test_contact_email_wrong_code_writes_nothing(doctor):
    browser = _browser(doctor)
    _start_email(browser)

    response = browser.post(EMAIL_CODE, {'code': _wrong(_email_code())})

    assert response.context['form'].has_error('code', 'invalid_code')
    assert not Profile.objects.filter(user=doctor).exists() and _events(doctor) == []


@pytest.mark.parametrize('email, code', [
    ('ANA.GARCIA@example.com', 'alternative_is_login'), ('not-an-email', 'invalid')])
def test_contact_email_same_as_login_or_malformed_is_a_form_error(doctor, email, code):
    response = _start_email(_browser(doctor), email=email)

    assert response.context['form'].has_error('email', code)
    assert not PendingContactChange.objects.exists()


def test_contact_email_same_as_the_current_one_is_a_form_error(doctor):
    Profile.objects.create(user=doctor, alternative_email=NEW_EMAIL)

    response = _start_email(_browser(doctor))

    assert response.context['form'].has_error('email', 'same_email')


# --- The personal-data page ---------------------------------------------------------------------

def test_personal_data_ignores_a_posted_alternative_email_and_links_the_changes(doctor):
    from .test_personal_data import _initial

    Profile.objects.create(user=doctor, alternative_email='old@example.org')
    browser = _browser(doctor)
    response = browser.post('/en/settings/', _initial(doctor, city='Madrid',
                                                      alternative_email='forged@example.org'))

    assert response.status_code == 302
    assert Profile.objects.get(user=doctor).alternative_email == 'old@example.org'
    html = _html(browser.get('/en/settings/'))
    assert 'name="alternative_email"' not in html
    assert re.search(r'value="old@example.org" readonly aria-readonly="true" '
                     r'data-readonly="alternative_email"', html)
    assert re.search(r'href="/en/settings/mobile/"[^>]*data-change="mobile">Change<', html)
    assert re.search(r'href="/en/settings/email/"[^>]*data-change="email">Change<', html)


def test_service_refuses_the_alternative_email(doctor):
    with pytest.raises(ValueError):
        profile.update_personal_data(doctor, {'alternative_email': 'x@example.org'})


# --- The staff admin ------------------------------------------------------------------------

def test_staff_admin_form_refuses_a_mobile_used_by_another_account(doctor):
    other = User.objects.create_user('other@example.com', PASSWORD, phone=NEW_E164)
    data = {'email': other.email, 'phone': MOBILE_E164, 'date_joined_0': '2026-01-01',
            'date_joined_1': '00:00:00', 'is_active': 'on'}

    form = UserChangeAdminForm(data=data, instance=other)

    assert not form.is_valid()
    assert IN_USE in form.errors['phone']
    # Its own mobile, or a free one, is accepted.
    for phone in (NEW_E164, '+34622918399', ''):
        form = UserChangeAdminForm(data={**data, 'phone': phone}, instance=other)
        assert 'phone' not in form.errors, phone


def test_staff_admin_change_page_never_sets_the_mobile(client, doctor, staff):
    other = User.objects.create_user('other@example.com', PASSWORD, phone=NEW_E164)
    client.force_login(staff)
    page = client.get(f'/admin/accounts/user/{other.pk}/change/')
    assert page.status_code == 200
    assert 'name="phone"' not in _html(page)  # read-only there


# --- Signed out, staff, Spanish ---------------------------------------------------------------

@pytest.mark.parametrize('path', [MOBILE_PAGE, MOBILE_CODE, EMAIL_PAGE, EMAIL_CODE])
def test_signed_out_visitor_is_sent_to_login(client, path):
    response = client.get(path)

    assert response['Location'] == f'/en/login/?next={path}'


def test_staff_account_changes_its_mobile_without_an_event(staff):
    browser = _browser(staff)
    assert browser.get(MOBILE_PAGE).status_code == 200
    _start_mobile(browser)

    assert browser.post(MOBILE_CODE, {'code': _mobile_code()})['Location'] == '/en/settings/'
    staff.refresh_from_db()
    assert staff.phone == NEW_E164 and _events(staff) == []


def test_spanish_pages_and_messages(doctor):
    browser = _browser(doctor)
    html = _html(browser.get('/es/settings/mobile/'))
    for text in ('Cambiar su número de teléfono móvil', 'Nuevo número de teléfono móvil',
                 'Contraseña actual', 'Enviar el código', 'Le enviaremos un código'):
        assert text in html, text
    response = _start_mobile(browser, password='wrong-password-123', lang='es')
    assert 'La contraseña no es correcta.' in _html(response)
    User.objects.create_user('other@example.com', PASSWORD, phone=NEW_E164)
    response = _start_mobile(browser, lang='es')
    assert 'Este número de teléfono móvil ya lo utiliza otra cuenta.' in _html(response)
    response = _start_mobile(browser, number='622 918 376', lang='es')
    assert response['Location'] == '/es/settings/mobile/code/'
    html = _html(browser.get('/es/settings/mobile/code/'))
    assert 'Introdúzcalo a continuación para confirmar el cambio.' in html
    response = browser.post('/es/settings/mobile/code/', {'code': _mobile_code('+34622918376')},
                            follow=True)
    assert 'Su número de teléfono móvil se ha cambiado.' in _html(response)
    assert 'número de teléfono móvil de su cuenta de MyTreatPlan' in mail.outbox[-1].body
    html = _html(browser.get('/es/settings/email/'))
    assert 'Cambiar su email de contacto' in html and 'Nuevo email de contacto' in html


# --- No code or contact value in the session, the logs, the event log or page records -----

def test_no_code_or_contact_in_the_session_logs_or_records(doctor, caplog):
    caplog.set_level(logging.DEBUG)
    before = Event.objects.order_by('-id').values_list('id', flat=True).first() or 0
    browser = _browser(doctor)
    browser.get(MOBILE_PAGE)
    _start_mobile(browser)
    code = _mobile_code()
    browser.get(MOBILE_CODE)
    browser.post(MOBILE_CODE, {'code': _wrong(code)})
    session = json.dumps(dict(browser.session), default=str)
    browser.post(MOBILE_CODE, {'code': code})
    _start_email(browser)
    email_code = _email_code()
    session += json.dumps(dict(browser.session), default=str)
    browser.post(EMAIL_CODE, {'code': email_code})
    session += json.dumps(dict(browser.session), default=str)

    secrets = (NEW_E164, '622918375', NEW_EMAIL, code, email_code, MOBILE_E164)
    for value in secrets:
        assert value not in session, value
    # The logs: only the fake provider's own code line (development and tests) holds them.
    lines = [record.getMessage() for record in caplog.records
             if not (record.name == 'signup.verification'
                     and record.getMessage().startswith('Verification code for'))]
    for value in secrets:
        assert not any(value in line for line in lines), value
    # The event log and the page records (their data decrypted).
    records = json.dumps([[event.event, event.step, event.field, event.reason, event.data]
                          for event in Event.objects.filter(id__gt=before)], default=str)
    assert 'settings_mobile' in records  # the pages were recorded
    for value in secrets:
        assert value not in records, value


def test_session_holds_only_the_pending_rows_id(doctor):
    browser = _browser(doctor)
    _start_mobile(browser)

    change = PendingContactChange.objects.get()
    assert browser.session[CHANGE_SESSION_KEY] == {'mobile': change.pk}
    assert SESSION_KEY in browser.session


# --- Review fixes (2026-11-15) ----------------------------------------------------------------

def test_a_double_submitted_start_ends_on_the_code_page(doctor, capture_codes):
    browser = _browser(doctor)

    first = _start_mobile(browser)
    second = _start_mobile(browser)

    assert first['Location'] == second['Location'] == MOBILE_CODE
    change = PendingContactChange.objects.get()  # one row: the second replaced the first
    assert browser.session[CHANGE_SESSION_KEY] == {'mobile': change.pk}
    page = browser.get(MOBILE_CODE)
    assert page.status_code == 200 and 'data-sent' in _html(page)
    # The second send met the cooldown: the same code, still valid.
    assert len(_sent_codes(capture_codes, NEW_E164)) == 1
    assert browser.post(MOBILE_CODE, {'code': _mobile_code()})['Location'] == '/en/settings/'


def test_a_start_locks_the_account_row_first(doctor):
    """Two starts of one account run one after the other (PostgreSQL: SELECT ... FOR
    UPDATE on the account before the pending row is replaced), so the second never hits
    the one-row-per-kind rule."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as queries:
        profile.start_contact_change(doctor, 'mobile', NEW_E164)

    statements = [query['sql'] for query in queries.captured_queries
                  if not query['sql'].startswith(('SAVEPOINT', 'RELEASE', 'BEGIN'))]
    assert 'FROM "accounts_user"' in statements[0]
    if connection.vendor == 'postgresql':
        assert 'FOR UPDATE' in statements[0]
    assert 'accounts_pendingcontactchange' in statements[1]


def test_a_double_clicked_confirm_lands_quietly_on_the_page(doctor):
    browser = _browser(doctor)
    _start_mobile(browser)
    stale = dict(browser.session[CHANGE_SESSION_KEY])  # the second click's session
    code = _mobile_code()
    assert browser.post(MOBILE_CODE, {'code': code})['Location'] == '/en/settings/'
    session = browser.session
    session[CHANGE_SESSION_KEY] = stale
    session.save()

    response = browser.post(MOBILE_CODE, {'code': code}, follow=True)

    assert response.redirect_chain[-1][0] == '/en/settings/'
    html = _html(response)
    assert 'This change has expired. Please start again.' not in html
    assert 'Your mobile phone number has been changed.' in html
    doctor.refresh_from_db()
    assert doctor.phone == NEW_E164 and len(_events(doctor)) == 1


def test_an_expired_change_still_says_so_after_an_earlier_change(doctor, clock):
    """The quiet landing is only for the change just written, not for a later one."""
    browser = _browser(doctor)
    _start_mobile(browser)
    browser.post(MOBILE_CODE, {'code': _mobile_code()})
    clock.advance(limits.COOLDOWN_SECONDS)
    _start_mobile(browser, number='622 918 376')
    PendingContactChange.objects.all().delete()

    response = browser.post(MOBILE_CODE, {'code': '123456'}, follow=True)

    assert 'This change has expired. Please start again.' in _html(response)


def test_notice_time_is_in_the_doctors_zone_with_its_name(doctor, settings):
    import zoneinfo
    from datetime import datetime
    from datetime import timezone as dt_timezone

    when = datetime(2026, 11, 15, 12, 0, tzinfo=dt_timezone.utc)
    default = when.astimezone(zoneinfo.ZoneInfo(settings.TIME_ZONE))
    assert profile.notice_time(doctor, when) == (
        f'{default:%Y-%m-%d %H:%M} ({settings.TIME_ZONE})')
    Profile.objects.create(user=doctor, time_zone='America/Mexico_City')
    assert profile.notice_time(doctor, when) == '2026-11-15 06:00 (America/Mexico_City)'

    message = profile.build_notice(doctor, 'mobile', NEW_E164, 'en', when)

    assert 'changed on 2026-11-15 06:00 (America/Mexico_City)' in message.body


def test_a_failing_notice_leaves_the_change_and_is_only_reported(doctor, monkeypatch):
    from django.core.mail import EmailMessage

    from accounts import login_guard

    def fail(self, *args, **kwargs):
        raise OSError(f'SMTP refused {NORMALIZED}')
    monkeypatch.setattr(EmailMessage, 'send', fail)
    reported = []
    monkeypatch.setattr(login_guard, 'report_error',
                        lambda *args: reported.append(args[1:]))
    browser = _browser(doctor)
    _start_mobile(browser)

    response = browser.post(MOBILE_CODE, {'code': _mobile_code()})

    assert response['Location'] == '/en/settings/'
    doctor.refresh_from_db()
    assert doctor.phone == NEW_E164 and len(_events(doctor)) == 1
    assert reported == [('Contact change notice error', 'contact_change.action', 'notice',
                         reported[0][3])]
    assert isinstance(reported[0][3], OSError)
    assert mail.outbox == []


def test_a_stale_staff_save_keeps_the_doctors_new_mobile(doctor, staff, rf):
    from django.contrib.admin.sites import site
    from django.utils import timezone

    from accounts.admin import UserAdmin
    from accounts.crypto import phone_index

    stale = User.objects.get(pk=doctor.pk)  # the staff form loaded before the change
    changed_at = timezone.now()
    User.objects.filter(pk=doctor.pk).update(phone=NEW_E164, phone_index=phone_index(NEW_E164),
                                             devices_valid_after=changed_at)

    class Form:  # what the staff form changed (a many-to-many field is saved apart)
        changed_data = ['first_name', 'groups']
    stale.first_name = 'Ana'
    request = rf.post('/admin/')
    request.user = staff
    UserAdmin(User, site).save_model(request, stale, Form(), change=True)

    doctor.refresh_from_db()
    assert doctor.first_name == 'Ana'
    assert doctor.phone == NEW_E164 and doctor.phone_index == phone_index(NEW_E164)
    assert doctor.devices_valid_after == changed_at
    assert _events(doctor)[-1].payload['phone'] == NEW_E164


def test_staff_admin_change_page_still_saves_its_changes(client, doctor, staff):
    client.force_login(staff)
    page = client.get(f'/admin/accounts/user/{doctor.pk}/change/')
    form = page.context['adminform'].form
    data = {}
    for name, field in form.fields.items():
        value = form[name].value()
        if field.disabled or value is None:
            continue
        if isinstance(value, list):
            data[name] = value
        elif isinstance(value, bool):
            if value:
                data[name] = 'on'
        else:
            data[name] = value
    data['last_name'] = 'Changed'
    data.update({'date_joined_0': doctor.date_joined.strftime('%Y-%m-%d'),
                 'date_joined_1': doctor.date_joined.strftime('%H:%M:%S')})
    data.pop('date_joined', None)
    if doctor.last_login:
        data.update({'last_login_0': doctor.last_login.strftime('%Y-%m-%d'),
                     'last_login_1': doctor.last_login.strftime('%H:%M:%S')})
    data.pop('last_login', None)

    response = client.post(f'/admin/accounts/user/{doctor.pk}/change/', data)

    assert response.status_code == 302, response.context['adminform'].form.errors
    doctor.refresh_from_db()
    assert doctor.last_name == 'Changed' and doctor.phone == MOBILE_E164
