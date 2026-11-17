"""The doctor's shipping addresses (epic-doctor-profile entry 7): the rows 1-18 of the
plan's I/O matrix. The service accounts.shipping, the pages accounts.views.settings_shipping*,
the erasure and the ES interface."""
import json
import logging
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.utils import timezone

from accounts import shipping
from accounts.erasure import erase_user, purge_erased
from accounts.models import ShippingAddress
from eventlog.models import Event
from integration.models import OutboxEvent
from integration.tests.snapshot_rows import put_country
from regions.models import BANNED
from signup.tests.helpers import PASSWORD

from .helpers import _html

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()
LIST = '/en/settings/shipping/'
ADD = '/en/settings/shipping/add/'


def _edit(address):
    return f'/en/settings/shipping/{address.pk}/edit/'


def _default(address):
    return f'/en/settings/shipping/{address.pk}/default/'


def _delete(address):
    return f'/en/settings/shipping/{address.pk}/delete/'


@pytest.fixture(autouse=True)
def country_map():
    for code, state in [('ES', 'eu'), ('PT', 'eu'), ('FR', 'eu'), ('AE', 'ae'),
                        ('SA', 'ae'), ('IR', BANNED)]:
        put_country(code, state)


@pytest.fixture(autouse=True)
def retention(settings):
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 30


@pytest.fixture
def doctor():
    return User.objects.create_user('maria.espuny@example.net', PASSWORD, title='mrs',
                                    first_name='María José', last_name='Espuny Marín',
                                    country='ES', phone='+34611222333')


@pytest.fixture
def other():
    return User.objects.create_user('other.doctor@example.net', PASSWORD, first_name='Ot')


@pytest.fixture
def staff():
    return User.objects.create_user('staff@example.net', PASSWORD, is_staff=True)


def _browser(user):
    browser = Client()
    browser.force_login(user)
    return browser


def _address(**changes):
    data = {'label': 'Clínica Madrid', 'recipient_name': 'Rosa Vidal',
            'clinic_name': 'Clínica Vidal', 'contact_phone': '612 34 56 78',
            'delivery_notes': 'Llamar al timbre B', 'address_line1': 'Calle Mayor 1',
            'address_line2': '2º A', 'city': 'Madrid', 'postal_code': '28001',
            'province': 'Madrid', 'country': 'ES'}
    data.update(changes)
    return data


def _make(user, **changes):
    return shipping.create(user, _address(**changes))


def _errors(response, field):
    return [str(error) for error in response.context['form'].errors.get(field, [])]


# --- 1. The first address is saved, encrypted and made the default -------------------------

def test_first_address_is_saved_as_the_default_and_listed(doctor):
    browser = _browser(doctor)
    response = browser.post(ADD, _address())
    assert response.status_code == 302 and response['Location'] == LIST
    address = ShippingAddress.objects.get()
    assert (address.user, address.is_default, address.version) == (doctor, True, 1)
    assert address.recipient_name == 'Rosa Vidal' and address.country.code == 'ES'
    html = _html(browser.get(LIST))
    assert 'data-shipping-default' in html and 'Default' in html
    assert 'Clínica Madrid' in html and 'Madrid' in html and 'Spain' in html
    # Only the label or recipient, the city and the country are on the card.
    assert 'Calle Mayor' not in html and '+34612345678' not in html
    # Without a label the card shows the recipient.
    browser.post(ADD, _address(label='', recipient_name='Pau Soler'))
    assert 'Pau Soler' in _html(browser.get(LIST))


# --- 2. Two addresses, one default ---------------------------------------------------------

def test_making_the_second_address_the_default_leaves_exactly_one(doctor, other):
    first, second = _make(doctor), _make(doctor, label='Second')
    assert [first.is_default, second.is_default] == [True, False]
    response = _browser(doctor).post(_default(second))
    assert response['Location'] == LIST
    first.refresh_from_db(), second.refresh_from_db()
    assert [first.is_default, second.is_default] == [False, True]
    assert shipping.active(doctor).filter(is_default=True).count() == 1
    assert _browser(doctor).get(_default(second)).status_code == 405
    # The database allows one default per doctor among the active ones.
    with pytest.raises(IntegrityError), transaction.atomic():
        ShippingAddress.objects.filter(pk=first.pk).update(is_default=True)
    _make(other)
    ShippingAddress.objects.filter(pk=first.pk).update(is_default=True,
                                                       archived_at=timezone.now())
    assert ShippingAddress.objects.filter(is_default=True).count() == 3


# --- 3. The country: other region, banned --------------------------------------------------

def test_a_country_of_the_other_region_is_refused_with_a_link_to_its_site(doctor, settings):
    settings.REGION_SITE_URLS = {**settings.REGION_SITE_URLS, 'ae': 'https://dev-ae.example.org'}
    response = _browser(doctor).post(ADD, _address(country='AE'))
    assert response.status_code == 200 and not ShippingAddress.objects.exists()
    assert _errors(response, 'country') == [
        'This billing country is served by MyTreatPlan dev-ae.example.org. Billing data for '
        'it cannot be saved here.']
    assert 'href="https://dev-ae.example.org"' in _html(response)


@pytest.mark.parametrize('ae', [False, True])
def test_a_banned_country_gets_signups_message_on_either_site(doctor, request, ae):
    if ae:
        request.getfixturevalue('ae_portal')
    response = _browser(doctor).post(ADD, _address(country='IR'))
    assert response.status_code == 200 and not ShippingAddress.objects.exists()
    from signup.forms import COUNTRY_BANNED_MESSAGE
    assert _errors(response, 'country') == [str(COUNTRY_BANNED_MESSAGE)]


def test_the_ae_site_refuses_a_country_of_the_eu_region(doctor, ae_portal):
    response = _browser(doctor).post(ADD, _address(country='ES'))
    assert response.status_code == 200 and not ShippingAddress.objects.exists()
    assert 'This billing country is served by MyTreatPlan' in _errors(response, 'country')[0]


# --- 4. Required fields --------------------------------------------------------------------

@pytest.mark.parametrize('field', ['postal_code', 'city', 'address_line1', 'recipient_name',
                                   'contact_phone', 'country'])
def test_a_spanish_address_needs_each_required_field(doctor, field):
    response = _browser(doctor).post(ADD, _address(**{field: ''}))
    assert response.status_code == 200 and not ShippingAddress.objects.exists()
    assert _errors(response, field) == ['This field is required.']
    with pytest.raises(shipping.ShippingInvalid) as refused:
        _make(doctor, **{field: ''})
    assert refused.value.problems == {field: 'required'}


def test_the_label_clinic_lines_province_and_notes_are_optional(doctor):
    address = _make(doctor, label='', clinic_name='', address_line2='', province='',
                    delivery_notes='')
    assert address.pk and address.label == '' and address.delivery_notes == ''


# --- 5. AE needs no postal code ------------------------------------------------------------

def test_an_ae_address_without_a_postal_code_is_saved_on_the_ae_site(doctor, ae_portal):
    response = _browser(doctor).post(ADD, _address(country='AE', postal_code='',
                                                   contact_phone='50 123 4567'))
    assert response['Location'] == LIST
    address = ShippingAddress.objects.get()
    assert address.postal_code == '' and address.contact_phone == '+971501234567'
    assert address.address_line1 == 'Calle Mayor 1'


# --- 6. The phone --------------------------------------------------------------------------

def test_the_phone_is_parsed_in_the_address_country_and_stored_in_e164(doctor):
    assert _make(doctor).contact_phone == '+34612345678'
    assert _make(doctor, contact_phone='+34 612 34 56 78').contact_phone == '+34612345678'
    # A number of another country keeps its own prefix.
    assert _make(doctor, contact_phone='+351 912 345 678').contact_phone == '+351912345678'
    response = _browser(doctor).post(ADD, _address(contact_phone='123'))
    assert _errors(response, 'contact_phone') == [
        'Enter a valid phone number, with the prefix if it is not from the address country.']
    for bad in ('612 34 56 78 ext 5', 'abc', '+34 612'):
        assert shipping.parse_phone(bad, 'ES') is None
    assert shipping.parse_phone('612 34 56 78', 'ES') == '+34612345678'
    assert shipping.parse_phone('612345678', '') is None


# --- 7. The limit --------------------------------------------------------------------------

def test_the_eleventh_active_address_is_refused_and_an_archived_one_does_not_count(doctor):
    for index in range(shipping.MAX_ADDRESSES):
        _make(doctor, label=f'A{index}')
    with pytest.raises(shipping.ShippingInvalid) as refused:
        _make(doctor)
    assert refused.value.problems == {shipping.ALL: 'limit'}
    browser = _browser(doctor)
    response = browser.post(ADD, _address())
    assert response.status_code == 302 and response['Location'] == LIST
    html = _html(browser.get(LIST))
    assert 'You can have at most 10 shipping addresses. Delete one to add another.' in html
    assert 'data-shipping-add>' not in html and 'data-shipping-limit' in html
    assert ShippingAddress.objects.count() == 10
    victim = shipping.active(doctor).last()
    shipping.mark_used(victim)
    assert shipping.remove(doctor, victim.pk) == shipping.ARCHIVED
    assert _make(doctor)
    assert shipping.active(doctor).count() == 10


# --- 8. Delete, default succession, archive ------------------------------------------------

def test_delete_confirms_then_removes_an_unused_address(doctor):
    address = _make(doctor)
    browser = _browser(doctor)
    html = _html(browser.get(_delete(address)))
    assert 'Delete this shipping address?' in html and 'Clínica Madrid' in html
    assert ShippingAddress.objects.count() == 1  # the GET changed nothing
    response = browser.post(_delete(address))
    assert response['Location'] == LIST
    assert not ShippingAddress.objects.exists()


def test_deleting_the_default_passes_it_to_the_most_recently_changed_usable_address(doctor):
    first, older, newer = _make(doctor), _make(doctor, label='B'), _make(doctor, label='C')
    ShippingAddress.objects.filter(pk=older.pk).update(updated_at=timezone.now()
                                                       - timedelta(days=2))
    ShippingAddress.objects.filter(pk=newer.pk).update(updated_at=timezone.now()
                                                       - timedelta(days=1))
    assert shipping.remove(doctor, first.pk) == shipping.DELETED
    older.refresh_from_db(), newer.refresh_from_db()
    assert (older.is_default, newer.is_default) == (False, True)


def test_a_used_address_is_archived_instead_and_hidden(doctor):
    first, second = _make(doctor), _make(doctor, label='B')
    assert shipping.mark_used(first) is True and shipping.mark_used(first) is False
    first.refresh_from_db()
    used_at = first.used_at
    assert used_at is not None
    browser = _browser(doctor)
    assert browser.post(_delete(first))['Location'] == LIST
    first.refresh_from_db(), second.refresh_from_db()
    assert first.archived_at is not None and not first.is_default and first.used_at == used_at
    assert second.is_default
    assert 'Clínica Madrid' not in _html(browser.get(LIST))
    assert list(shipping.usable_addresses(doctor)) == [second]
    assert browser.get(_edit(first)).status_code == 404
    assert browser.post(_delete(first)).status_code == 404


# --- 9. Edits in place and the stale version -----------------------------------------------

def test_an_edit_is_in_place_and_goes_the_version_up_only_when_something_changed(doctor):
    address = _make(doctor)
    browser = _browser(doctor)
    page = _html(browser.get(_edit(address)))
    assert 'name="version" value="1"' in page and 'Rosa Vidal' in page
    data = {**_address(), 'contact_phone': '+34612345678', 'version': 1}
    assert browser.post(_edit(address), data)['Location'] == LIST
    address.refresh_from_db()
    assert address.version == 1  # nothing changed
    assert browser.post(_edit(address), {**data, 'city': 'Getafe'})['Location'] == LIST
    address.refresh_from_db()
    assert (address.city, address.version, address.pk) == ('Getafe', 2, address.pk)
    assert ShippingAddress.objects.count() == 1


def test_a_stale_version_is_refused(doctor):
    address = _make(doctor)
    browser = _browser(doctor)
    data = {**_address(), 'version': 1}
    assert browser.post(_edit(address), {**data, 'city': 'Getafe'}).status_code == 302
    response = browser.post(_edit(address), {**data, 'city': 'Leganés'})
    assert response.status_code == 200
    assert 'This shipping address was changed elsewhere. Please reload the page and try ' \
        'again.' in _html(response)
    address.refresh_from_db()
    assert address.city == 'Getafe' and address.version == 2
    with pytest.raises(shipping.StaleVersion):
        shipping.update(doctor, address.pk, {'city': 'X'}, version=1)
    # A post without a version is stale too.
    response = browser.post(_edit(address), _address())
    assert 'changed elsewhere' in _html(response)


# --- 10. A country that is no longer usable ------------------------------------------------

@pytest.mark.parametrize('country', ['IR', 'AE'])
def test_an_address_whose_country_became_unusable_stays_listed_and_cannot_be_saved(
        doctor, country):
    address = _make(doctor)
    put_country('ES', BANNED if country == 'IR' else 'ae')
    browser = _browser(doctor)
    html = _html(browser.get(LIST))
    assert 'Clínica Madrid' in html and 'Cannot be used for new orders' in html
    assert 'data-shipping-make-default' not in html
    assert not shipping.is_usable(address)
    assert list(shipping.usable_addresses(doctor)) == []
    assert shipping.default_address(doctor) is None and not shipping.has_usable_address(doctor)
    with pytest.raises(shipping.ShippingInvalid) as refused:
        shipping.make_default(doctor, address.pk)
    assert refused.value.problems == {shipping.ALL: 'unusable'}
    assert 'cannot be used for new orders' in _html(browser.get(_edit(address)))
    data = {**_address(), 'version': 1}
    response = browser.post(_edit(address), data)
    assert response.status_code == 200 and _errors(response, 'country')
    address.refresh_from_db()
    assert address.version == 1
    # Changing the country saves it.
    put_country('PT', 'eu')
    response = browser.post(_edit(address), {**data, 'country': 'PT', 'contact_phone':
                                             '+34612345678'})
    assert response['Location'] == LIST
    assert shipping.is_usable(ShippingAddress.objects.get())


def test_the_new_address_takes_the_default_from_an_unusable_one(doctor):
    old = _make(doctor)
    put_country('ES', BANNED)
    new = _make(doctor, country='PT', contact_phone='+351912345678')
    old.refresh_from_db()
    assert new.is_default and not old.is_default


def test_a_country_moved_to_the_other_region_is_unusable_on_the_ae_site(doctor, ae_portal):
    address = _make(doctor, country='AE', contact_phone='+971501234567')
    assert shipping.is_usable(address) and shipping.default_address(doctor) == address
    put_country('AE', 'eu')
    assert not shipping.is_usable(address) and not shipping.has_usable_address(doctor)


# --- 11. Another doctor's address is 404 ---------------------------------------------------

def test_another_doctors_address_is_not_found(doctor, other):
    address = _make(other)
    browser = _browser(doctor)
    assert browser.get(_edit(address)).status_code == 404
    assert browser.post(_edit(address), _address()).status_code == 404
    assert browser.post(_default(address)).status_code == 404
    assert browser.get(_delete(address)).status_code == 404
    assert browser.post(_delete(address)).status_code == 404
    assert browser.get(f'/en/settings/shipping/{uuid.uuid4()}/edit/').status_code == 404
    address.refresh_from_db()
    assert address.version == 1 and address.is_default
    for call in (lambda: shipping.remove(doctor, address.pk),
                 lambda: shipping.make_default(doctor, address.pk),
                 lambda: shipping.update(doctor, address.pk, {}, version=1)):
        with pytest.raises(ShippingAddress.DoesNotExist):
            call()


# --- 12. The safe return after adding ------------------------------------------------------

def test_the_add_page_returns_to_a_path_of_this_site_and_ignores_others(doctor):
    browser = _browser(doctor)
    page = _html(browser.get(ADD + '?next=/en/cart/'))
    assert 'name="next" value="/en/cart/"' in page
    assert browser.post(ADD, {**_address(), 'next': '/en/cart/'})['Location'] == '/en/cart/'
    for bad in ('https://evil.example/x', '//evil.example/x', 'javascript:alert(1)',
                '/\\evil.com', '/%2F%2Fevil.com', '/\tevil.com'):
        assert 'name="next"' not in _html(browser.get(ADD, {'next': bad}))
        response = browser.post(ADD, {**_address(), 'next': bad})
        assert response['Location'] == LIST
        ShippingAddress.objects.all().delete()


# --- 13. Erasure ---------------------------------------------------------------------------

def test_erasing_the_account_deletes_its_addresses(doctor, other, staff):
    _make(doctor), _make(doctor, label='B')
    kept = _make(other)
    archived = _make(doctor, label='C')
    shipping.mark_used(archived)
    shipping.remove(doctor, archived.pk)
    assert erase_user(doctor, by=staff)
    assert not ShippingAddress.objects.filter(user=doctor).exists()
    assert list(ShippingAddress.objects.all()) == [kept]
    User.objects.filter(pk=doctor.pk).update(purge_after=timezone.now() - timedelta(days=1))
    assert purge_erased() == 1
    assert list(ShippingAddress.objects.all()) == [kept]


def test_the_database_cascades_with_the_user(doctor):
    _make(doctor)
    User.objects.filter(pk=doctor.pk).delete()
    assert not ShippingAddress.objects.exists()


# --- 14. No event, no value in logs, page records or the event log -------------------------

def test_a_save_emits_no_client_event_and_logs_no_value(doctor, caplog):
    caplog.set_level(logging.DEBUG)
    OutboxEvent.objects.all().delete()
    before = Event.objects.order_by('-id').values_list('id', flat=True).first() or 0
    browser = _browser(doctor)
    browser.get(ADD)
    browser.post(ADD, _address())
    address = ShippingAddress.objects.get()
    browser.get(LIST)
    browser.get(_edit(address))
    shipping.mark_used(address)
    browser.post(_edit(address), {**_address(), 'contact_phone': '+34612345678',
                                  'version': 1, 'city': 'Getafe'})
    browser.post(_default(address))
    browser.post(_delete(address))
    assert not OutboxEvent.objects.exists()
    secrets = ('Rosa Vidal', 'Clínica Vidal', '612345678', 'Calle Mayor', 'Getafe', '28001',
               'timbre', '612 34 56 78')
    lines = [record.getMessage() for record in caplog.records
             if not (record.name == 'signup.verification'
                     and record.getMessage().startswith('Verification code for'))]
    records = json.dumps([[event.event, event.step, event.field, event.reason, event.data]
                          for event in Event.objects.filter(id__gt=before)], default=str)
    assert 'settings_shipping' in records  # the pages were recorded
    for value in secrets:
        assert not any(value in line for line in lines), value
        assert value not in records, value


def test_the_model_never_shows_a_value_as_text(doctor):
    address = _make(doctor)
    assert str(address) == f'Shipping address {address.pk}'
    assert 'Rosa' not in str(address) and '612' not in str(address)


# --- 15. Staff and anonymous visitors ------------------------------------------------------

def test_staff_gets_404_on_every_page_and_anonymous_visitors_are_sent_to_login(
        doctor, staff, client):
    address = _make(doctor)
    browser = _browser(staff)
    for url in (LIST, ADD, _edit(address), _delete(address)):
        assert browser.get(url).status_code == 404
    for url in (_edit(address), _default(address), _delete(address), ADD):
        assert browser.post(url, _address()).status_code == 404
    assert ShippingAddress.objects.count() == 1
    for url in (LIST, ADD, _edit(address), _default(address), _delete(address)):
        assert client.get(url)['Location'] == f'/en/login/?next={url}'
    with pytest.raises(ValueError):
        shipping.create(staff, _address())


def test_the_tile_and_the_page_replace_coming_soon(doctor):
    html = _html(_browser(doctor).get(LIST))
    assert 'coming soon' not in html
    assert 'data-settings-tile="settings_shipping"' in html and 'aria-current="page"' in html
    assert 'data-shipping-empty' in html and 'data-shipping-add' in html


# --- 16. Delivery notes --------------------------------------------------------------------

def test_delivery_notes_are_limited_to_300_characters(doctor):
    response = _browser(doctor).post(ADD, _address(delivery_notes='x' * 301))
    assert response.status_code == 200 and not ShippingAddress.objects.exists()
    assert _errors(response, 'delivery_notes')
    with pytest.raises(shipping.ShippingInvalid) as refused:
        _make(doctor, delivery_notes='x' * 301)
    assert refused.value.problems == {'delivery_notes': 'too_long'}
    assert _make(doctor, delivery_notes='x' * 300).delivery_notes == 'x' * 300


# --- 17. Encrypted at rest -----------------------------------------------------------------

def test_every_field_but_the_country_is_ciphertext_at_rest(doctor):
    _make(doctor)
    columns = ['label', 'recipient_name', 'clinic_name', 'contact_phone', 'delivery_notes',
               'address_line1', 'address_line2', 'city', 'postal_code', 'province']
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {", ".join(columns)}, country FROM accounts_shippingaddress')
        row = cursor.fetchone()
    plain = ['Clínica Madrid', 'Rosa Vidal', 'Clínica Vidal', '+34612345678', 'Llamar',
             'Calle Mayor', '2º A', 'Madrid', '28001', 'Madrid']
    for column, value in zip(row, plain):
        assert column and value not in column
    assert row[-1] == 'ES'


# --- 18. The ES interface ------------------------------------------------------------------

def test_the_spanish_interface_is_translated_with_usted(doctor):
    browser = _browser(doctor)
    address = _make(doctor)
    html = _html(browser.get('/es/settings/shipping/'))
    for text in ('Predeterminada', 'Editar', 'Eliminar', 'Añadir una dirección de envío',
                 'Direcciones de envío', 'Adónde enviamos sus productos'):
        assert text in html, text
    html = _html(browser.get('/es/settings/shipping/add/'))
    for text in ('Etiqueta', 'Destinatario', 'Clínica', 'Teléfono de contacto',
                 'Instrucciones de entrega', 'Dirección (línea 1)', 'Ciudad',
                 'Código postal', 'Provincia', 'País', 'Guardar'):
        assert text in html, text
    html = _html(browser.get(f'/es/settings/shipping/{address.pk}/delete/'))
    assert '¿Desea eliminar esta dirección de envío?' in html
    response = browser.post('/es/settings/shipping/add/', _address(country='AE'))
    assert 'Este país de facturación está atendido por MyTreatPlan' in _html(response)
    response = browser.post('/es/settings/shipping/add/', _address(city=''))
    assert 'Este campo es obligatorio.' in _html(response)
    response = browser.post('/es/settings/shipping/add/', _address(contact_phone='1'))
    assert 'Introduzca un número de teléfono válido' in _html(response)
    response = browser.post('/es/settings/shipping/add/', _address(country='IR'))
    assert 'No podemos ofrecer MyTreatPlan' in _html(response)


# --- The service ---------------------------------------------------------------------------

def test_the_service_refuses_unknown_fields_and_non_doctors(doctor, staff):
    with pytest.raises(ValueError):
        shipping.create(doctor, {**_address(), 'is_default': True})
    with pytest.raises(ValueError):
        shipping.create(staff, _address())
    address = _make(doctor)
    with pytest.raises(ValueError):
        shipping.update(doctor, address.pk, {'version': 9}, version=1)


def test_delivery_notes_line_breaks_count_as_one_and_are_stored_as_newlines(doctor):
    notes = 'a' * 100 + '\r\n' + 'b' * 98 + '\r\n' + 'c' * 99  # 299 once normalised
    assert len(notes) == 301
    response = _browser(doctor).post(ADD, _address(delivery_notes=notes))
    assert response.status_code == 302
    stored = ShippingAddress.objects.get().delivery_notes
    assert '\r' not in stored and len(stored) == 299 and stored.count('\n') == 2
    assert shipping.clean_data({'delivery_notes': 'x\ry'})['delivery_notes'] == 'x\ny'


def test_no_default_is_made_from_an_unusable_address_and_a_new_one_takes_it(doctor):
    first = _make(doctor)
    second = _make(doctor, label='B', country='PT', contact_phone='+351912345678')
    put_country('PT', BANNED)
    assert shipping.remove(doctor, first.pk) == shipping.DELETED
    second.refresh_from_db()
    assert not second.is_default and not ShippingAddress.objects.filter(
        is_default=True).exists()
    # A new usable address takes the default when none is set.
    new = _make(doctor, label='C')
    assert new.is_default
    # ... and from an unusable current default.
    ShippingAddress.objects.filter(pk=new.pk).update(is_default=False)
    ShippingAddress.objects.filter(pk=second.pk).update(is_default=True)
    newest = _make(doctor, label='D')
    second.refresh_from_db()
    assert newest.is_default and not second.is_default
