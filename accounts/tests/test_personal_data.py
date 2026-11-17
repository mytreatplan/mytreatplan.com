"""The settings area and its personal-data page (epic-doctor-profile entry 2): the rows of
the plan's I/O matrix (the service accounts.profile, the page accounts.views.personal_data,
the sections not built yet, the client event, the consents, the home card). The browser
journey is in browser/test_personal_data.py; the erasure rows in test_erasure.py."""
import re
from datetime import timedelta

import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.utils import timezone, translation

from accounts import doctor_area, profile
from accounts.models import Profile
from integration.models import OutboxEvent
from legal.models import LegalChoice, LegalDocument
from mytp_core.contracts import ClientUpdated, check_central_payload
from signup.tests.helpers import PASSWORD

from .helpers import _html

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()
WHATSAPP = LegalDocument.Kind.WHATSAPP_CONTACT
COMMERCIAL = LegalDocument.Kind.COMMERCIAL_CONSENT
PAGE = '/en/settings/'
SECTIONS = [('settings_maker', 'settings/maker/', 'Maker data', 'Datos fabricante'),
            ('settings_password', 'settings/password/', 'Change password', 'Cambio de clave')]


@pytest.fixture
def doctor():
    return User.objects.create_user('maria.espuny@example.net', PASSWORD, title='mrs',
                                    first_name='María José', last_name='Espuny Marín',
                                    country='ES', phone='+34611222333')


def _browser(user):
    browser = Client()
    browser.force_login(user)
    return browser


def _initial(user, **changes):
    """The page's form as it is shown (what an untouched POST sends), with `changes`
    (None leaves a field out)."""
    data = {'language': 'en', 'city': '', 'time_zone': django_settings.TIME_ZONE,
            'title': user.title, 'profession': '', 'first_name': user.first_name,
            'middle_name': user.middle_name, 'last_name': user.last_name,
            'landline_prefix': user.country.code if user.country else '', 'landline': '',
            'consent_token': _token(user)}
    data.update(changes)
    return {key: value for key, value in data.items() if value is not None}


def _token(user):
    """The consent token the page shows now (the consents as they are stored)."""
    from accounts.forms import PersonalDataForm

    return PersonalDataForm(user_pk=user.pk,
                            consent_state=profile.consents_of(user)).current_token()


def _events(user):
    return list(OutboxEvent.objects.filter(aggregate_id=user.uid).order_by('id'))


def _choices(user, kind):
    return list(LegalChoice.objects.filter(user=user, document__kind=kind)
                .order_by('decided_at', 'id').values_list('accepted', flat=True))


# --- Open the page ------------------------------------------------------------------------

def test_page_shows_the_form_the_read_only_data_and_the_tiles(doctor, fake_bucket):
    response = _browser(doctor).get(PAGE)

    assert response.status_code == 200
    html = _html(response)
    form = response.context['form']
    assert form['title'].value() == 'mrs' and form['first_name'].value() == 'María José'
    assert form['city'].value() == '' and form['profession'].value() == ''
    assert form['landline'].value() == ''
    assert form['time_zone'].value() == django_settings.TIME_ZONE
    assert form['language'].value() == 'en'
    # Read-only: shown, never fields.
    for name in ('country', 'phone', 'email', 'alternative_email'):
        assert name not in form.fields
        assert f'data-readonly="{name}"' in html
    assert 'value="Spain"' in html and 'value="+34611222333"' in html
    assert 'value="maria.espuny@example.net"' in html
    assert 'opening a ticket in the support area' in html
    assert re.search(r'href="/en/support/"[^>]*>click here</a>', html)
    # The five tiles, "My details" current; member since; back to the home.
    for name in ('settings', 'settings_maker', 'settings_password', 'settings_fiscal',
                 'settings_shipping'):
        assert f'data-settings-tile="{name}"' in html, name
    assert re.search(r'aria-label="My details" aria-current="page" '
                     r'data-settings-tile="settings"', html)
    assert html.count('aria-current="page" data-settings-tile') == 1
    assert doctor.date_joined.strftime('%Y·%m·%d') in html
    assert re.search(r'href="/en/home/"[^>]*data-back-home', html)
    assert 'data-cancel' in html and re.search(r'href="/en/home/"[^>]*data-cancel', html)
    # The gear is current. The photo block (entry 4) shows the initials and links to the
    # upload page (on a site with storage); the upload itself is not on this page.
    assert re.search(r'href="/en/settings/" class="[^"]*" aria-label="Settings" '
                     r'aria-current="page"', html)
    assert 'type="file"' not in html
    assert re.search(r'href="/en/settings/photo/"[^>]*data-photo-upload>Upload a photo<', html)
    assert 'data-photo-delete' not in html and 'data-photo-pending' not in html
    assert not Profile.objects.exists()


def test_page_in_spanish_uses_the_formal_texts(doctor):
    html = _html(_browser(doctor).get('/es/settings/'))

    for text in ('Revise y/o modifique sus datos', 'Fecha de alta',
                 'Volver a Panel de Control', 'Si necesita modificar su usuario de acceso',
                 'Doy mi permiso para que contacten conmigo vía WhatsApp.',
                 'Sí, deseo recibir información comercial de MyTreatPlan y sus asociados',
                 'Cancelar', 'Guardar', 'Franja horaria', 'Profesión', 'Teléfono fijo',
                 'Email alternativo'):
        assert text in html, text


def test_time_zone_offers_every_standard_zone_and_language_the_site_languages(doctor):
    import zoneinfo

    form = _browser(doctor).get(PAGE).context['form']

    assert [value for value, _label in form.fields['time_zone'].choices] == sorted(
        zoneinfo.available_timezones())
    assert form.fields['language'].choices == list(django_settings.LANGUAGES)


def test_anonymous_visitor_is_sent_to_login(client):
    for path in ['settings/', 'settings/fiscal/'] + [section[1] for section in SECTIONS]:
        response = client.get(f'/en/{path}')
        assert response['Location'] == f'/en/login/?next=/en/{path}'


# --- Save ---------------------------------------------------------------------------------

def test_save_stores_the_values_and_emits_one_client_updated(doctor):
    response = _browser(doctor).post(PAGE, _initial(
        doctor, title='dr', city='Madrid', time_zone='Atlantic/Canary',
        profession='orthodontist'))

    assert response.status_code == 302 and response['Location'] == PAGE
    doctor.refresh_from_db()
    stored = Profile.objects.get(user=doctor)
    assert doctor.title == 'dr'
    assert (stored.city, stored.time_zone, stored.profession) == (
        'Madrid', 'Atlantic/Canary', 'orthodontist')
    events = _events(doctor)
    assert [event.type for event in events] == ['client.updated']
    payload = events[0].payload
    assert (payload['title'], payload['city'], payload['time_zone'],
            payload['profession']) == ('dr', 'Madrid', 'Atlantic/Canary', 'orthodontist')
    # Untouched defaults are not stored: blank in the event.
    assert payload['language'] == '' and payload['landline'] == ''
    ClientUpdated.model_validate(payload)
    check_central_payload(payload)
    # No consent in the event.
    assert not {'commercial_consent', 'whatsapp_contact'} & set(payload)
    page = _browser(doctor).get(PAGE)
    assert 'Your details have been saved.' not in _html(page)  # shown once, already read


def test_success_message_is_shown_after_the_save(doctor):
    browser = _browser(doctor)
    response = browser.post(PAGE, _initial(doctor, city='Madrid'), follow=True)

    assert 'Your details have been saved.' in _html(response)


def test_city_is_encrypted_in_the_database(doctor):
    _browser(doctor).post(PAGE, _initial(doctor, city='Secret-City',
                                         landline='910 00 00 00'))
    # The alternative email is written by a code-confirmed change (test_contact_change.py).
    Profile.objects.filter(user=doctor).update(alternative_email='clinic@example.com')
    with connection.cursor() as cursor:
        cursor.execute('SELECT city, landline, alternative_email FROM accounts_profile')
        row = cursor.fetchone()
    for stored, plain in zip(row, ('Secret-City', '+34910000000', 'clinic@example.com')):
        assert stored and plain not in stored


def test_nothing_changed_records_no_event_and_no_consent(doctor):
    browser = _browser(doctor)
    browser.post(PAGE, _initial(doctor))

    assert _events(doctor) == []
    assert not LegalChoice.objects.filter(user=doctor).exists()
    assert not Profile.objects.exists()
    # Once a value is stored, posting it again changes nothing either.
    browser.post(PAGE, _initial(doctor, city='Madrid'))
    browser.post(PAGE, _initial(doctor, city='Madrid'))
    assert len(_events(doctor)) == 1


def test_names_are_editable(doctor):
    _browser(doctor).post(PAGE, _initial(doctor, first_name='Ana', middle_name='M.',
                                         last_name='Ruiz'))
    doctor.refresh_from_db()

    assert (doctor.first_name, doctor.middle_name, doctor.last_name) == ('Ana', 'M.', 'Ruiz')
    assert _events(doctor)[0].payload['first_name'] == 'Ana'


def test_required_names_and_title(doctor):
    response = _browser(doctor).post(PAGE, _initial(doctor, first_name='', title=''))

    assert response.status_code == 200
    assert set(response.context['form'].errors) == {'first_name', 'title'}


def test_language_change_redirects_to_the_page_in_it(doctor):
    response = _browser(doctor).post(PAGE, _initial(doctor, language='es'))

    assert response['Location'] == '/es/settings/'
    assert Profile.objects.get(user=doctor).language == 'es'
    assert _events(doctor)[0].payload['language'] == 'es'
    page = _browser(doctor).get('/es/settings/')
    assert page.context['form']['language'].value() == 'es'


def test_language_not_of_the_site_is_a_field_error(doctor):
    response = _browser(doctor).post(PAGE, _initial(doctor, language='fr'))

    assert 'language' in response.context['form'].errors


def test_save_lands_in_the_stored_language_even_when_untouched(doctor):
    Profile.objects.create(user=doctor, language='es')
    browser = _browser(doctor)
    assert browser.get(PAGE).context['form']['language'].value() == 'es'

    response = browser.post(PAGE, _initial(doctor, language='es', city='Madrid'))

    assert response['Location'] == '/es/settings/'


def test_one_language_site_stays_on_its_language_after_a_save(doctor, settings):
    settings.LANGUAGES = [('en', 'English')]
    Profile.objects.create(user=doctor, language='es')  # stored before, e.g. on .com

    response = _browser(doctor).post(PAGE, _initial(doctor, language=None, city='Dubai'))

    assert response['Location'] == '/en/settings/'


def test_one_language_site_has_no_language_field(doctor, settings):
    settings.LANGUAGES = [('en', 'English')]
    browser = _browser(doctor)
    response = browser.get(PAGE)

    assert 'language' not in response.context['form'].fields
    assert 'name="language"' not in _html(response)
    browser.post(PAGE, _initial(doctor, language='es', city='Dubai'))
    assert Profile.objects.get(user=doctor).language == ''


def test_forged_read_only_fields_are_ignored(doctor):
    _browser(doctor).post(PAGE, _initial(doctor, country='FR', phone='+33612345678',
                                         email='forged@example.com', city='Madrid',
                                         phone_index='x', is_staff='on'))
    doctor.refresh_from_db()

    assert (doctor.country.code, doctor.phone, doctor.email, doctor.is_staff) == (
        'ES', '+34611222333', 'maria.espuny@example.net', False)
    assert Profile.objects.get(user=doctor).city == 'Madrid'


def test_service_refuses_read_only_fields(doctor):
    for field in ('country', 'phone', 'email', 'is_staff'):
        with pytest.raises(ValueError):
            profile.update_personal_data(doctor, {field: 'x'})
    with pytest.raises(ValueError):
        profile.update_personal_data(doctor, {}, {'terms': True})
    assert _events(doctor) == []


@pytest.mark.parametrize('prefix, number, stored', [
    ('ES', '910 00 00 00', '+34910000000'),
    ('ES', '+34 910000000', '+34910000000'),
    ('FR', '01 23 45 67 89', '+33123456789'),
])
def test_landline_is_stored_in_e164(doctor, prefix, number, stored):
    _browser(doctor).post(PAGE, _initial(doctor, landline_prefix=prefix, landline=number))

    assert Profile.objects.get(user=doctor).landline == stored
    assert _events(doctor)[0].payload['landline'] == stored
    form = _browser(doctor).get(PAGE).context['form']
    assert form['landline_prefix'].value() == prefix


@pytest.mark.parametrize('prefix, number, field', [
    ('ES', '12', 'landline'),
    ('ES', '910 00 00 00 ext 5', 'landline'),
    ('FR', '00 00', 'landline'),
    ('', '910 00 00 00', 'landline_prefix'),
])
def test_invalid_landline_is_a_field_error_and_saves_nothing(doctor, prefix, number, field):
    response = _browser(doctor).post(PAGE, _initial(doctor, landline_prefix=prefix,
                                                    landline=number, city='Madrid'))

    assert response.status_code == 200
    assert field in response.context['form'].errors
    assert not Profile.objects.exists() and _events(doctor) == []


def test_alternative_email_is_no_longer_a_field_of_this_page(doctor):
    """6.3: the contact email changes only after a code (test_contact_change.py); a
    posted value is ignored and the row is read-only with a "Change" link."""
    Profile.objects.create(user=doctor, alternative_email='clinic@example.com')
    browser = _browser(doctor)
    browser.post(PAGE, _initial(doctor, alternative_email='MARIA.ESPUNY@example.net',
                                city='Madrid'))

    assert Profile.objects.get(user=doctor).alternative_email == 'clinic@example.com'
    html = _html(browser.get(PAGE))
    assert 'value="clinic@example.com" readonly' in html
    assert 'href="/en/settings/email/"' in html and 'href="/en/settings/mobile/"' in html


def test_time_zone_not_in_the_list_is_a_field_error(doctor):
    response = _browser(doctor).post(PAGE, _initial(doctor, time_zone='Mars/Olympus'))

    assert 'time_zone' in response.context['form'].errors


def test_staff_account_saves_without_an_event():
    staff = User.objects.create_superuser('staff.settings@example.net', PASSWORD,
                                          first_name='Sam', last_name='Staff', title='mr')
    browser = _browser(staff)
    assert browser.get(PAGE).status_code == 200
    browser.post(PAGE, _initial(staff, city='Dublin'))

    assert Profile.objects.get(user=staff).city == 'Dublin'
    assert _events(staff) == []


# --- Consents -----------------------------------------------------------------------------

def test_consents_tick_untick_and_back_append_choices_without_an_event(doctor):
    browser = _browser(doctor)
    browser.post(PAGE, _initial(doctor, commercial_consent='on', whatsapp_contact='on'))
    assert _choices(doctor, COMMERCIAL) == [True] and _choices(doctor, WHATSAPP) == [True]
    first = LegalChoice.objects.filter(user=doctor).order_by('id').first()

    browser.post(PAGE, _initial(doctor))  # both unticked
    browser.post(PAGE, _initial(doctor, commercial_consent='on'))

    assert _choices(doctor, COMMERCIAL) == [True, False, True]
    assert _choices(doctor, WHATSAPP) == [True, False]
    assert _events(doctor) == []
    # Append-only: the first row untouched; each on the current document, with the time.
    again = LegalChoice.objects.get(pk=first.pk)
    assert (again.accepted, again.decided_at) == (first.accepted, first.decided_at)
    for choice in LegalChoice.objects.filter(user=doctor):
        assert choice.document == LegalDocument.objects.current(choice.document.kind)
        assert timezone.now() - choice.decided_at < timedelta(minutes=1)
    form = browser.get(PAGE).context['form']
    assert form['commercial_consent'].value() is True
    assert form['whatsapp_contact'].value() is False


def test_a_stale_tab_editing_the_city_does_not_reaccept_a_withdrawn_consent(doctor):
    browser = _browser(doctor)
    browser.post(PAGE, _initial(doctor, commercial_consent='on'))
    # Tab A is opened now: it shows the consent ticked.
    tab_a = _initial(doctor, commercial_consent='on', city='Madrid')
    # Tab B withdraws it.
    browser.post(PAGE, _initial(doctor))
    assert _choices(doctor, COMMERCIAL) == [True, False]

    response = browser.post(PAGE, tab_a)  # tab A saves its city, the box still ticked

    assert response.status_code == 302
    assert Profile.objects.get(user=doctor).city == 'Madrid'
    assert _choices(doctor, COMMERCIAL) == [True, False]


@pytest.mark.parametrize('token', [None, '', 'forged', 'e30:1tamper:wrongsignature'])
def test_a_missing_or_forged_consent_token_refuses_the_save(doctor, token):
    data = _initial(doctor, city='Madrid', commercial_consent='on', consent_token=token)

    response = _browser(doctor).post(PAGE, data)

    assert response.status_code == 200
    errors = response.context['form'].non_field_errors()
    assert 'This page has expired. Please reload it and try again.' in errors
    assert not Profile.objects.exists() and _events(doctor) == []
    assert not LegalChoice.objects.filter(user=doctor).exists()
    # The page now carries a fresh token and the stored (unticked) state.
    assert response.context['form']['commercial_consent'].value() is False


def test_another_accounts_consent_token_is_refused(doctor):
    other = User.objects.create_user('other@example.net', PASSWORD, title='dr',
                                     first_name='O', last_name='T')
    data = _initial(doctor, city='Madrid', consent_token=_token(other))

    response = _browser(doctor).post(PAGE, data)

    assert response.status_code == 200 and not Profile.objects.exists()


def test_a_consent_text_published_since_the_page_was_shown_refuses_the_save(doctor):
    data = _initial(doctor, city='Madrid', whatsapp_contact='on')
    LegalDocument.objects.create(kind=WHATSAPP, title_en='WhatsApp v2', title_es='v2',
                                 body_en='New', body_es='Nuevo', published_at=timezone.now())

    response = _browser(doctor).post(PAGE, data)

    assert response.status_code == 200
    assert ('The consent texts have been updated. Please review them again and save.'
            in response.context['form'].non_field_errors())
    assert not LegalChoice.objects.filter(user=doctor).exists()
    assert not Profile.objects.exists() and _events(doctor) == []
    # Reloaded, the new version is shown and a save records the choice on it.
    browser = _browser(doctor)
    browser.post(PAGE, _initial(doctor, whatsapp_contact='on'))
    choice = LegalChoice.objects.get(user=doctor)
    assert choice.document.title_en == 'WhatsApp v2' and choice.accepted


def test_terms_box_without_an_acceptance_has_no_date_reference(doctor):
    html = _html(_browser(doctor).get(PAGE))

    assert 'ro-terms-date' not in html
    assert re.search(r'<input type="checkbox" id="ro-terms"[^>]*disabled>', html)


def test_unticked_consent_without_an_earlier_choice_records_nothing(doctor):
    _browser(doctor).post(PAGE, _initial(doctor))

    assert not LegalChoice.objects.filter(user=doctor).exists()


def test_a_kind_without_a_published_document_is_not_shown(doctor):
    LegalDocument.objects.filter(kind=WHATSAPP).update(published_at=None)
    browser = _browser(doctor)
    response = browser.get(PAGE)

    assert 'whatsapp_contact' not in response.context['form'].fields
    assert 'commercial_consent' in response.context['form'].fields
    browser.post(PAGE, _initial(doctor, whatsapp_contact='on'))
    assert not LegalChoice.objects.filter(user=doctor).exists()


def test_whatsapp_document_is_provisional_with_both_texts():
    document = LegalDocument.objects.current(WHATSAPP)

    assert document.provisional and document.version == 1
    assert 'I agree to be contacted by MyTreatPlan via WhatsApp.' in document.body_en
    assert 'Doy mi permiso para que contacten conmigo vía WhatsApp.' in document.body_es
    assert WHATSAPP not in LegalDocument.REQUIRED_KINDS
    assert WHATSAPP not in LegalDocument.SIGNUP_KINDS


def test_terms_box_is_ticked_locked_with_the_date_and_never_changed(doctor):
    accepted = timezone.now() - timedelta(days=3)
    for kind in LegalDocument.REQUIRED_KINDS:
        LegalChoice.objects.create(user=doctor, document=LegalDocument.objects.current(kind),
                                   accepted=True, decided_at=accepted)
    browser = _browser(doctor)
    html = _html(browser.get(PAGE))

    assert re.search(r'<input type="checkbox" id="ro-terms"[^>]*checked disabled', html)
    assert f'accepted on {timezone.localtime(accepted):%Y-%m-%d}' in html
    assert 'aria-describedby="ro-terms-date"' in html
    assert 'href="/en/privacy/"' in html and 'href="/en/terms/"' in html
    browser.post(PAGE, _initial(doctor, terms='', conditions=''))
    assert LegalChoice.objects.filter(user=doctor).count() == 2


# --- Unbuilt sections ---------------------------------------------------------------------

@pytest.mark.parametrize('name, path, en, es', SECTIONS)
def test_unbuilt_section_is_coming_soon_inside_the_settings_area(doctor, name, path, en, es):
    browser = _browser(doctor)
    for lang, title, soon in [('en', en, 'This section is coming soon.'),
                              ('es', es, 'Esta sección estará disponible próximamente.')]:
        response = browser.get(f'/{lang}/{path}')
        assert response.status_code == 200
        html = _html(response)
        assert re.search(rf'<h1[^>]*>{re.escape(title)}</h1>', html)
        assert soon in html
        assert re.search(rf'aria-current="page" data-settings-tile="{name}"', html)
        assert html.count('data-settings-tile=') == 5
        assert re.search(rf'href="/{lang}/settings/" class="[^"]*" aria-label="[^"]+" '
                         r'aria-current="page"', html)


def test_unknown_settings_section_is_404(doctor, rf):
    from django.http import Http404

    from accounts.views import settings_coming_soon

    request = rf.get('/en/settings/nowhere/')
    request.user = doctor
    with pytest.raises(Http404):
        settings_coming_soon(request, section='nowhere')
    assert _browser(doctor).get('/en/settings/nowhere/').status_code == 404


# --- The client event and the home card ---------------------------------------------------

def test_payload_carries_blank_fields_without_a_profile(doctor):
    from accounts.client_sync import payload_of

    payload = payload_of(doctor)
    for name in Profile.SYNCED_FIELDS:
        assert payload[name] == ''
    ClientUpdated.model_validate(payload)


@pytest.mark.parametrize('lang, profession, line', [
    ('en', 'orthodontist', 'Orthodontist · Madrid'),
    ('es', 'orthodontist', 'Ortodoncista · Madrid'),
    ('es', 'general_dentist', 'Dentista general · Madrid'),
])
def test_home_card_shows_profession_and_city(doctor, lang, profession, line):
    Profile.objects.create(user=doctor, profession=profession, city='Madrid')
    html = _html(_browser(doctor).get(f'/{lang}/home/'))

    assert re.search(rf'data-profile-line>{re.escape(line)}<', html)


@pytest.mark.parametrize('fields', [{}, {'city': 'Madrid'}, {'profession': 'orthodontist'}])
def test_home_card_falls_back_to_the_country(doctor, fields):
    if fields:
        Profile.objects.create(user=doctor, **fields)

    with translation.override('en'):
        assert doctor_area.profile_line(doctor) == 'Spain'
