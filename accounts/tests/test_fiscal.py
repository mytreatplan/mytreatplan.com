"""The doctor's fiscal entities (epic-doctor-profile entry 5): the rows 1-22 of the plan's
I/O matrix. The service accounts.fiscal, the pages accounts.views.settings_fiscal*, the
erasure and the ES interface."""
import json
import logging
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.utils import timezone

from accounts import fiscal
from accounts.crypto import blind_index
from accounts.erasure import erase_user, purge_erased
from accounts.models import FiscalEntity
from eventlog.models import Event
from integration.models import OutboxEvent
from integration.tests.snapshot_rows import put_country
from regions.models import BANNED
from signup.tests.helpers import PASSWORD

from .helpers import _html

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()
LIST = '/en/settings/fiscal/'
ADD = '/en/settings/fiscal/add/'


def _edit(entity):
    return f'/en/settings/fiscal/{entity.pk}/edit/'


def _default(entity):
    return f'/en/settings/fiscal/{entity.pk}/default/'


def _delete(entity):
    return f'/en/settings/fiscal/{entity.pk}/delete/'


@pytest.fixture(autouse=True)
def country_map():
    """The countries the tests use, in the map (a flushed map reads every country as eu)."""
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


def _company(**changes):
    data = {'kind': 'company', 'legal_name': 'Clínica Espuny S.L.', 'tax_id': 'es b-1234 5678',
            'country': 'ES', 'address_line1': 'Calle Mayor 1', 'city': 'Madrid',
            'postal_code': '28001'}
    data.update(changes)
    return data


def _person(**changes):
    data = {'kind': 'person', 'first_name': 'Rui', 'last_name': 'Sousa', 'tax_id': '123456789',
            'country': 'PT', 'address_line1': 'Rua Augusta 5', 'city': 'Lisboa',
            'postal_code': '1100-053'}
    data.update(changes)
    return data


def _make(user, data=None, **changes):
    return fiscal.create(user, data or _company(**changes))


def _errors(response, field):
    form = response.context['form']
    return [str(error) for error in form.errors.get(field, [])]


# --- 1. A company is saved, encrypted, and made the default ---------------------------------

def test_first_company_is_saved_as_the_default_and_listed_with_a_masked_tax_id(doctor):
    browser = _browser(doctor)
    response = browser.post(ADD, _company())
    assert response.status_code == 302 and response['Location'] == LIST
    entity = FiscalEntity.objects.get()
    assert (entity.user, entity.kind, entity.is_default, entity.version) == (
        doctor, 'company', True, 1)
    assert entity.legal_name == 'Clínica Espuny S.L.' and entity.country.code == 'ES'
    html = _html(browser.get(LIST))
    assert 'data-fiscal-default' in html and 'Default' in html
    assert 'Clínica Espuny S.L.' in html
    assert '••••5678' in html
    assert 'ESB12345678' not in html and 'ESB1234' not in html


# --- 2. Two entities, one default ----------------------------------------------------------

def test_making_the_second_entity_the_default_leaves_exactly_one(doctor):
    spanish = _make(doctor)
    portuguese = _make(doctor, _person())
    assert [spanish.is_default, portuguese.is_default] == [True, False]
    response = _browser(doctor).post(_default(portuguese))
    assert response['Location'] == LIST
    spanish.refresh_from_db(), portuguese.refresh_from_db()
    assert [spanish.is_default, portuguese.is_default] == [False, True]
    assert fiscal.active(doctor).filter(is_default=True).count() == 1


def test_the_database_allows_one_default_per_doctor_among_the_active(doctor, other):
    first = _make(doctor)
    second = _make(doctor, _person())
    with pytest.raises(IntegrityError), transaction.atomic():
        FiscalEntity.objects.filter(pk=second.pk).update(is_default=True)
    # Another doctor's, and an archived one's, default flag do not count.
    _make(other)
    FiscalEntity.objects.filter(pk=second.pk).update(is_default=True, archived_at=timezone.now())
    assert FiscalEntity.objects.filter(is_default=True).count() == 3
    assert first.is_default


# --- 3, 4. The billing country: other region, banned ---------------------------------------

def test_a_country_of_the_other_region_is_refused_with_a_link_to_its_site(doctor, settings):
    settings.REGION_SITE_URLS = {**settings.REGION_SITE_URLS, 'ae': 'https://dev-ae.example.org'}
    response = _browser(doctor).post(ADD, _company(country='AE', tax_id='1'))
    assert response.status_code == 200
    assert not FiscalEntity.objects.exists()
    assert _errors(response, 'country') == [
        'This billing country is served by MyTreatPlan dev-ae.example.org. Billing data for '
        'it cannot be saved here.']
    assert 'href="https://dev-ae.example.org"' in _html(response)


@pytest.mark.parametrize('ae', [False, True])
def test_a_banned_country_gets_signups_message_on_either_site(doctor, request, ae):
    if ae:
        request.getfixturevalue('ae_portal')
    response = _browser(doctor).post(ADD, _company(country='IR'))
    assert response.status_code == 200 and not FiscalEntity.objects.exists()
    from signup.forms import COUNTRY_BANNED_MESSAGE
    assert _errors(response, 'country') == [str(COUNTRY_BANNED_MESSAGE)]


def test_the_ae_site_refuses_a_country_of_the_eu_region(doctor, ae_portal):
    response = _browser(doctor).post(ADD, _company(country='ES'))
    assert response.status_code == 200 and not FiscalEntity.objects.exists()
    assert 'This billing country is served by MyTreatPlan' in _errors(response, 'country')[0]


# --- 5. Required fields --------------------------------------------------------------------

@pytest.mark.parametrize('field', ['postal_code', 'city', 'address_line1'])
def test_a_spanish_entity_needs_its_address(doctor, field):
    response = _browser(doctor).post(ADD, _company(**{field: ''}))
    assert response.status_code == 200 and not FiscalEntity.objects.exists()
    assert _errors(response, field) == ['This field is required.']


def test_the_tax_id_and_the_country_are_required(doctor):
    response = _browser(doctor).post(ADD, _company(tax_id='  - ', country=''))
    assert _errors(response, 'tax_id') == ['This field is required.']
    assert _errors(response, 'country') == ['This field is required.']


def test_a_country_that_does_not_exist_is_refused(doctor):
    response = _browser(doctor).post(ADD, _company(country='ZZ'))
    assert response.status_code == 200 and not FiscalEntity.objects.exists()
    assert _errors(response, 'country')


# --- 6, 7, 8. The UAE and the VAT id -------------------------------------------------------

def test_ae_needs_only_the_names_the_tax_id_and_the_country_and_keeps_the_vat_id(
        doctor, ae_portal):
    browser = _browser(doctor)
    page = _html(browser.get(ADD + '?x=1'))
    assert 'data-fiscal-vat' in page
    response = browser.post(ADD, {'kind': 'company', 'legal_name': 'Dubai Dental LLC',
                                  'tax_id': '100 2003-0040', 'vat_id': ' 10020030040003 ',
                                  'country': 'AE'})
    assert response['Location'] == LIST
    entity = FiscalEntity.objects.get()
    assert (entity.tax_id, entity.vat_id) == ('10020030040', '10020030040003')
    assert entity.vat_id_index == blind_index('10020030040003')
    assert (entity.address_line1, entity.city, entity.postal_code) == ('', '', '')
    # It round-trips on the edit page.
    html = _html(browser.get(_edit(entity)))
    assert 'value="10020030040003"' in html
    assert '<div data-fiscal-vat>' in html and '<div hidden data-fiscal-vat>' not in html


def test_another_ae_country_has_one_tax_id_and_needs_an_address(doctor, ae_portal):
    browser = _browser(doctor)
    initial = {'kind': 'person', 'first_name': 'Sara', 'last_name': 'Khan', 'tax_id': '300',
               'country': 'SA', 'vat_id': '999'}
    response = browser.post(ADD, initial)
    assert response.status_code == 200 and not FiscalEntity.objects.exists()
    for field in ('address_line1', 'city', 'postal_code'):
        assert _errors(response, field) == ['This field is required.']
    # The VAT id field is there but hidden for SA; a forged value is never stored.
    assert '<div hidden data-fiscal-vat>' in _html(response)
    response = browser.post(ADD, {**initial, 'address_line1': 'Olaya St 1', 'city': 'Riyadh',
                                  'postal_code': '12211'})
    assert response['Location'] == LIST
    entity = FiscalEntity.objects.get()
    assert (entity.vat_id, entity.vat_id_index) == ('', '')


def test_a_forged_vat_id_for_a_country_other_than_ae_is_dropped(doctor):
    response = _browser(doctor).post(ADD, _company(vat_id='ESB99999999'))
    assert response['Location'] == LIST
    entity = FiscalEntity.objects.get()
    assert (entity.vat_id, entity.vat_id_index) == ('', '')
    with pytest.raises(FiscalEntity.DoesNotExist):
        FiscalEntity.objects.get(vat_id_index=blind_index('ESB99999999'))


def test_the_vat_field_is_hidden_on_the_add_page_for_a_country_other_than_ae(doctor):
    html = _html(_browser(doctor).get(ADD))
    assert '<div hidden data-fiscal-vat>' in html
    assert 'NIF / CIF / VAT' in html


# --- 9. The tax id: normalised, indexed, ciphertext at rest --------------------------------

def test_the_tax_id_is_normalised_indexed_and_encrypted_at_rest(doctor):
    entity = _make(doctor)
    entity.refresh_from_db()
    assert entity.tax_id == 'ESB12345678'
    assert entity.tax_id_index == blind_index('ESB12345678')
    # The index is not unique: another doctor may have the same tax id.
    twin = User.objects.create_user('twin@example.net', PASSWORD)
    assert _make(twin).tax_id_index == entity.tax_id_index
    assert FiscalEntity.objects.filter(tax_id_index=blind_index('ESB12345678')).count() == 2
    with connection.cursor() as cursor:
        cursor.execute('SELECT tax_id, legal_name, address_line1, city, postal_code '
                       'FROM accounts_fiscalentity')
        rows = cursor.fetchall()
    assert len(rows) == 2
    for raw in rows:
        for column, plain in zip(raw, ['ESB12345678', 'Clínica', 'Calle Mayor', 'Madrid',
                                       '28001']):
            assert column and plain not in column


# --- 10. The names depend on the kind ------------------------------------------------------

def test_a_person_needs_both_names_and_a_company_a_legal_name(doctor):
    browser = _browser(doctor)
    response = browser.post(ADD, _person(last_name=''))
    assert _errors(response, 'last_name') == ['This field is required.']
    response = browser.post(ADD, _person(first_name=''))
    assert _errors(response, 'first_name') == ['This field is required.']
    response = browser.post(ADD, _company(legal_name=''))
    assert _errors(response, 'legal_name') == ['This field is required.']
    assert not FiscalEntity.objects.exists()


def test_the_other_kinds_fields_are_ignored(doctor):
    _browser(doctor).post(ADD, _company(first_name='Ignored', last_name='Ignored'))
    entity = FiscalEntity.objects.get()
    assert (entity.first_name, entity.last_name) == ('', '')
    _browser(doctor).post(ADD, _person(legal_name='Ignored S.L.'))
    assert FiscalEntity.objects.get(kind='person').legal_name == ''


def test_a_value_too_long_is_refused_and_a_bad_billing_email_too(doctor):
    response = _browser(doctor).post(ADD, _company(city='x' * 151, billing_email='nope'))
    assert response.status_code == 200 and not FiscalEntity.objects.exists()
    assert _errors(response, 'city')
    assert _errors(response, 'billing_email') == ['Enter a valid email address.']
    response = _browser(doctor).post(ADD, _company(billing_email='billing@example.net'))
    assert response['Location'] == LIST
    assert FiscalEntity.objects.get().billing_email == 'billing@example.net'


# --- 11. The limit -------------------------------------------------------------------------

def test_the_eleventh_active_entity_is_refused_and_archived_ones_do_not_count(doctor):
    created = [_make(doctor, _company(tax_id=f'B{n}')) for n in range(fiscal.MAX_ENTITIES)]
    browser = _browser(doctor)
    page = browser.get(ADD)
    assert page.status_code == 302 and page['Location'] == LIST
    assert 'at most 10 fiscal entities' in _html(browser.get(LIST))
    assert 'data-fiscal-add' not in _html(browser.get(LIST))
    response = browser.post(ADD, _company(tax_id='B11'), follow=True)
    assert response.redirect_chain[-1][0] == LIST
    assert FiscalEntity.objects.count() == fiscal.MAX_ENTITIES
    with pytest.raises(fiscal.FiscalInvalid) as refused:
        _make(doctor, _company(tax_id='B11'))
    assert refused.value.problems == {fiscal.ALL: 'limit'}
    # An archived entity (used on an order) leaves room.
    fiscal.mark_used(created[3])
    assert fiscal.remove(doctor, created[3].pk) == fiscal.ARCHIVED
    assert _make(doctor, _company(tax_id='B11')).tax_id == 'B11'
    assert FiscalEntity.objects.count() == fiscal.MAX_ENTITIES + 1
    assert fiscal.active(doctor).count() == fiscal.MAX_ENTITIES


# --- 12, 13, 14. Delete --------------------------------------------------------------------

def test_delete_asks_first_then_deletes_an_unused_entity(doctor):
    keep, drop = _make(doctor), _make(doctor, _person())
    browser = _browser(doctor)
    page = browser.get(_delete(drop))
    assert page.status_code == 200 and 'data-confirm-delete' in _html(page)
    assert FiscalEntity.objects.count() == 2
    response = browser.post(_delete(drop))
    assert response['Location'] == LIST
    assert list(FiscalEntity.objects.all()) == [keep] and keep.is_default


def test_deleting_the_default_passes_it_to_the_most_recently_changed(doctor):
    default = _make(doctor)
    older = _make(doctor, _person())
    newer = _make(doctor, _person(tax_id='987654321'))
    # `older` was changed last: the new default, not the newest created.
    fiscal.update(doctor, newer.pk, {'city': 'Porto'}, version=1)
    fiscal.update(doctor, older.pk, {'city': 'Faro'}, version=1)
    assert fiscal.remove(doctor, default.pk) == fiscal.DELETED
    older.refresh_from_db(), newer.refresh_from_db()
    assert (older.is_default, newer.is_default) == (True, False)


def test_deleting_the_only_entity_leaves_no_default(doctor):
    only = _make(doctor)
    fiscal.remove(doctor, only.pk)
    assert not FiscalEntity.objects.exists()
    assert not fiscal.has_usable_entity(doctor) and fiscal.default_entity(doctor) is None


def test_deleting_a_used_entity_archives_it(doctor):
    used = _make(doctor)
    other_entity = _make(doctor, _person())
    fiscal.mark_used(used, timezone.now())
    browser = _browser(doctor)
    assert browser.post(_delete(used))['Location'] == LIST
    used.refresh_from_db(), other_entity.refresh_from_db()
    assert used.archived_at is not None and not used.is_default
    assert other_entity.is_default
    assert list(fiscal.usable_entities(doctor)) == [other_entity]
    assert 'Clínica Espuny' not in _html(browser.get(LIST))
    # An archived entity can no longer be opened, changed or deleted.
    for url in (_edit(used), _default(used), _delete(used)):
        assert browser.post(url).status_code == 404
    assert browser.get(_edit(used)).status_code == 404


# --- 15. Edit ------------------------------------------------------------------------------

def test_an_edit_is_saved_in_place_and_the_version_goes_up(doctor):
    entity = _make(doctor)
    fiscal.mark_used(entity)
    browser = _browser(doctor)
    page = browser.get(_edit(entity))
    assert page.status_code == 200
    form = page.context['form']
    data = {name: form.initial[name] for name in fiscal.FIELDS} | {'version': 1}
    data['city'] = 'Getafe'
    assert browser.post(_edit(entity), data)['Location'] == LIST
    entity.refresh_from_db()
    assert (entity.city, entity.version, entity.pk) == ('Getafe', 2, entity.pk)
    assert FiscalEntity.objects.count() == 1 and entity.used_at is not None
    # Saving without a change does not move the version.
    data['version'] = 2
    browser.post(_edit(entity), data)
    entity.refresh_from_db()
    assert entity.version == 2


def test_a_stale_version_is_refused(doctor):
    entity = _make(doctor)
    browser = _browser(doctor)
    data = {name: getattr(entity, name) for name in fiscal.FIELDS if name != 'country'}
    data.update(country='ES', version=1, city='Tab one')
    assert browser.post(_edit(entity), data)['Location'] == LIST
    data.update(city='Tab two')  # a second tab still on version 1
    response = browser.post(_edit(entity), data)
    assert response.status_code == 200
    assert 'changed elsewhere' in _html(response)
    entity.refresh_from_db()
    assert (entity.city, entity.version) == ('Tab one', 2)
    # A post without any version counts as stale too.
    del data['version']
    assert browser.post(_edit(entity), data).status_code == 200
    with pytest.raises(fiscal.StaleVersion):
        fiscal.update(doctor, entity.pk, {'city': 'x'}, version=1)


def test_a_change_of_kind_clears_the_other_kinds_names(doctor):
    entity = _make(doctor)
    fiscal.update(doctor, entity.pk, {'kind': 'person', 'first_name': 'Ana',
                                      'last_name': 'López'}, version=1)
    entity.refresh_from_db()
    assert (entity.kind, entity.legal_name, entity.display_name) == ('person', '', 'Ana López')


# --- 16. A country that changed since ------------------------------------------------------

def _stored_in(user, country, default=True):
    """An entity as it was saved when `country` was fine (no service: the rules have since
    changed)."""
    return FiscalEntity.objects.create(user=user, kind='company', legal_name='Old Co',
                                       tax_id='X1', country=country, is_default=default)


@pytest.mark.parametrize('country', ['IR', 'AE'])
def test_an_entity_whose_country_is_no_longer_allowed_stays_listed_but_unusable(
        doctor, country):
    entity = _stored_in(doctor, country)
    good = _make(doctor, _person())
    assert good.is_default  # a new entity takes the default from an unusable one
    FiscalEntity.objects.filter(pk=good.pk).update(is_default=False)
    FiscalEntity.objects.filter(pk=entity.pk).update(is_default=True)
    browser = _browser(doctor)
    html = _html(browser.get(LIST))
    assert 'Old Co' in html and 'Cannot be used for new orders' in html
    assert list(fiscal.usable_entities(doctor)) == [good]
    assert fiscal.default_entity(doctor) is None and fiscal.has_usable_entity(doctor)
    assert not fiscal.is_usable(entity)
    # Saving it again is refused unless the country is changed.
    page = browser.get(_edit(entity))
    assert 'cannot be used for new orders' in _html(page)
    data = {'kind': 'company', 'legal_name': 'Old Co', 'tax_id': 'X1', 'country': country,
            'address_line1': 'Calle 1', 'city': 'Madrid', 'postal_code': '28001',
            'version': 1}
    response = browser.post(_edit(entity), data)
    assert response.status_code == 200 and _errors(response, 'country')
    entity.refresh_from_db()
    assert entity.version == 1 and entity.address_line1 == ''
    response = browser.post(_edit(entity), {**data, 'country': 'ES'})
    assert response['Location'] == LIST
    entity.refresh_from_db()
    assert entity.country.code == 'ES' and fiscal.is_usable(entity)


def test_a_country_moved_to_the_other_region_is_unusable_on_the_ae_site(doctor, ae_portal):
    entity = _stored_in(doctor, 'AE')
    assert fiscal.is_usable(entity) and fiscal.default_entity(doctor) == entity
    put_country('AE', 'eu')
    assert not fiscal.is_usable(entity) and not fiscal.has_usable_entity(doctor)


# --- 17. Another doctor's entity is 404 ----------------------------------------------------

def test_another_doctors_entity_is_not_found(doctor, other):
    entity = _make(other)
    browser = _browser(doctor)
    assert browser.get(_edit(entity)).status_code == 404
    assert browser.post(_edit(entity), _company()).status_code == 404
    assert browser.post(_default(entity)).status_code == 404
    assert browser.get(_delete(entity)).status_code == 404
    assert browser.post(_delete(entity)).status_code == 404
    assert browser.get(f'/en/settings/fiscal/{uuid.uuid4()}/edit/').status_code == 404
    entity.refresh_from_db()
    assert entity.version == 1 and entity.is_default
    with pytest.raises(FiscalEntity.DoesNotExist):
        fiscal.remove(doctor, entity.pk)
    with pytest.raises(FiscalEntity.DoesNotExist):
        fiscal.make_default(doctor, entity.pk)
    with pytest.raises(FiscalEntity.DoesNotExist):
        fiscal.update(doctor, entity.pk, {}, version=1)


def test_make_default_and_delete_never_change_by_get(doctor):
    _make(doctor)
    second = _make(doctor, _person())
    assert _browser(doctor).get(_default(second)).status_code == 405
    second.refresh_from_db()
    assert not second.is_default


# --- 18. The safe return after adding ------------------------------------------------------

def test_the_add_page_returns_to_a_path_of_this_site_and_ignores_others(doctor):
    browser = _browser(doctor)
    page = _html(browser.get(ADD + '?next=/en/cart/'))
    assert 'name="next" value="/en/cart/"' in page
    assert browser.post(ADD, {**_company(), 'next': '/en/cart/'})['Location'] == '/en/cart/'
    for bad in ('https://evil.example/x', '//evil.example/x', 'javascript:alert(1)',
                '/\\evil.com', '/%2F%2Fevil.com', '/\tevil.com', '/en/\ncart/',
                '/\t/evil.com', '\n//evil.com'):
        page = _html(browser.get(ADD, {'next': bad}))
        assert 'name="next"' not in page
        response = browser.post(ADD, {**_person(tax_id=str(uuid.uuid4().int)[:9]),
                                      'next': bad})
        assert response['Location'] == LIST
        FiscalEntity.objects.all().delete()  # stay under the limit
    # Also from the query string of the post.
    response = browser.post(ADD + '?next=/en/cart/', _person(tax_id='55555555'))
    assert response['Location'] == '/en/cart/'


# --- 19. Erasure ---------------------------------------------------------------------------

def test_erasing_the_account_deletes_its_fiscal_entities(doctor, other, staff):
    _make(doctor), _make(doctor, _person())
    kept = _make(other)
    archived = _make(doctor, _company(tax_id='ARCH'))
    fiscal.mark_used(archived)
    fiscal.remove(doctor, archived.pk)
    assert erase_user(doctor, by=staff)
    assert not FiscalEntity.objects.filter(user=doctor).exists()
    assert list(FiscalEntity.objects.all()) == [kept]
    # The purge of the erased account is unaffected.
    User.objects.filter(pk=doctor.pk).update(purge_after=timezone.now() - timedelta(days=1))
    assert purge_erased() == 1
    assert list(FiscalEntity.objects.all()) == [kept]


def test_the_database_cascades_with_the_user(doctor):
    _make(doctor)
    User.objects.filter(pk=doctor.pk).delete()
    assert not FiscalEntity.objects.exists()


# --- 20. No event, no value in logs, page records or the event log -------------------------

def test_a_save_emits_no_client_event_and_logs_no_value(doctor, caplog):
    caplog.set_level(logging.DEBUG)
    OutboxEvent.objects.all().delete()
    before = Event.objects.order_by('-id').values_list('id', flat=True).first() or 0
    browser = _browser(doctor)
    browser.get(ADD)
    browser.post(ADD, _company(billing_email='billing@example.net'))
    entity = FiscalEntity.objects.get()
    browser.get(LIST)
    browser.get(_edit(entity))
    fiscal.mark_used(entity)
    data = {name: getattr(entity, name) for name in fiscal.FIELDS if name != 'country'}
    browser.post(_edit(entity), {**data, 'country': 'ES', 'version': 1, 'city': 'Getafe'})
    browser.post(_default(entity))
    browser.post(_delete(entity))
    assert not OutboxEvent.objects.exists()
    secrets = ('ESB12345678', 'B12345678', 'Clínica Espuny', 'Calle Mayor', 'Getafe',
               '28001', 'billing@example.net')
    lines = [record.getMessage() for record in caplog.records
             if not (record.name == 'signup.verification'
                     and record.getMessage().startswith('Verification code for'))]
    records = json.dumps([[event.event, event.step, event.field, event.reason, event.data]
                          for event in Event.objects.filter(id__gt=before)], default=str)
    assert 'settings_fiscal' in records  # the pages were recorded
    for value in secrets:
        assert not any(value in line for line in lines), value
        assert value not in records, value


def test_the_model_never_shows_a_value_as_text(doctor):
    entity = _make(doctor)
    assert str(entity) == f'Fiscal entity {entity.pk}'
    assert 'Clínica' not in str(entity) and 'ESB' not in str(entity)
    assert entity.masked_tax_id == '••••5678'
    assert FiscalEntity(tax_id='AB').masked_tax_id == '••'


# --- 21. Staff and anonymous visitors ------------------------------------------------------

def test_staff_gets_404_on_every_page_and_anonymous_visitors_are_sent_to_login(
        doctor, staff, client):
    entity = _make(doctor)
    browser = _browser(staff)
    for url in (LIST, ADD, _edit(entity), _delete(entity)):
        assert browser.get(url).status_code == 404
    for url in (_edit(entity), _default(entity), _delete(entity), ADD):
        assert browser.post(url, _company()).status_code == 404
    assert FiscalEntity.objects.count() == 1
    for url in (LIST, ADD, _edit(entity), _default(entity), _delete(entity)):
        assert client.get(url)['Location'] == f'/en/login/?next={url}'
    with pytest.raises(ValueError):
        fiscal.create(staff, _company())


def test_the_tile_and_the_page_replace_coming_soon(doctor):
    html = _html(_browser(doctor).get(LIST))
    assert 'coming soon' not in html
    assert 'data-settings-tile="settings_fiscal"' in html and 'aria-current="page"' in html
    assert 'data-fiscal-empty' in html and 'data-fiscal-add' in html


# --- 22. The ES interface ------------------------------------------------------------------

def test_the_spanish_interface_is_translated_with_usted(doctor):
    browser = _browser(doctor)
    entity = _make(doctor)
    html = _html(browser.get('/es/settings/fiscal/'))
    for text in ('Predeterminada', 'Editar', 'Eliminar', 'Añadir una entidad fiscal',
                 'Datos fiscales', 'Las personas o empresas con las que factura'):
        assert text in html, text
    html = _html(browser.get('/es/settings/fiscal/add/'))
    for text in ('Persona física', 'Empresa', 'Razón social', 'NIF / CIF / VAT',
                 'Dirección (línea 1)', 'Ciudad', 'Código postal', 'Provincia',
                 'País de facturación', 'Email de facturación', 'Guardar'):
        assert text in html, text
    html = _html(browser.get(f'/es/settings/fiscal/{entity.pk}/delete/'))
    assert '¿Desea eliminar esta entidad fiscal?' in html
    response = browser.post('/es/settings/fiscal/add/', _company(country='AE'))
    assert 'Este país de facturación está atendido por MyTreatPlan' in _html(response)
    response = browser.post('/es/settings/fiscal/add/', _company(city=''))
    assert 'Este campo es obligatorio.' in _html(response)
    response = browser.post('/es/settings/fiscal/add/', _company(country='IR'))
    assert 'No podemos ofrecer MyTreatPlan' in _html(response)


def test_the_ae_site_labels_the_two_ids(doctor, ae_portal):
    html = _html(_browser(doctor).get('/es/settings/fiscal/add/'))
    assert 'data-label-ae="Identificación fiscal"' in html
    assert 'Número de IVA' in html


# --- The service ---------------------------------------------------------------------------

def test_the_service_refuses_unknown_fields_and_checks_the_country_itself(doctor):
    with pytest.raises(ValueError):
        fiscal.create(doctor, _company(user=1))
    with pytest.raises(ValueError):
        fiscal.update(doctor, _make(doctor).pk, {'is_default': True}, version=1)
    with pytest.raises(fiscal.FiscalInvalid) as refused:
        fiscal.create(doctor, _company(country='AE'))
    assert refused.value.problems == {'country': 'country_other_region'}
    with pytest.raises(fiscal.FiscalInvalid) as refused:
        fiscal.create(doctor, _company(country='IR', tax_id=''))
    assert refused.value.problems == {'tax_id': 'required', 'country': 'country_banned'}
    # No value in the exception text.
    assert 'Clínica' not in str(refused.value)


def test_mark_used_is_idempotent_and_never_moves_the_version(doctor):
    entity = _make(doctor)
    first = timezone.now() - timedelta(days=2)
    assert fiscal.mark_used(entity, first) is True
    assert fiscal.mark_used(entity, timezone.now()) is False
    entity.refresh_from_db()
    assert entity.used_at == first and entity.version == 1


def test_usable_entities_put_the_default_first(doctor):
    _make(doctor)
    second = _make(doctor, _person())
    fiscal.make_default(doctor, second.pk)
    assert [e.pk for e in fiscal.usable_entities(doctor)][0] == second.pk
    assert fiscal.default_entity(doctor) == second
    assert fiscal.has_usable_entity(doctor)


def test_only_doctors_have_fiscal_entities(staff):
    with pytest.raises(ValueError):
        fiscal.create(staff, _company())


# --- Review follow-ups ----------------------------------------------------------------------

def test_an_unusable_entity_cannot_be_made_the_default(doctor):
    good = _make(doctor, _person())
    banned = _stored_in(doctor, 'IR', default=False)
    with pytest.raises(fiscal.FiscalInvalid):
        fiscal.make_default(doctor, banned.pk)
    assert _browser(doctor).post(_default(banned)).status_code == 404
    good.refresh_from_db(), banned.refresh_from_db()
    assert (good.is_default, banned.is_default) == (True, False)


def test_a_removed_default_passes_to_a_usable_entity_first(doctor):
    default = _make(doctor)
    unusable = _stored_in(doctor, 'IR', default=False)
    usable = _make(doctor, _person())
    # The unusable one is the most recently changed.
    FiscalEntity.objects.filter(pk=usable.pk).update(
        updated_at=timezone.now() - timedelta(days=1))
    fiscal.remove(doctor, default.pk)
    unusable.refresh_from_db(), usable.refresh_from_db()
    assert (usable.is_default, unusable.is_default) == (True, False)
    # With no usable one left, any active entity takes it.
    fiscal.remove(doctor, usable.pk)
    unusable.refresh_from_db()
    assert unusable.is_default


def test_a_new_entity_takes_the_default_from_an_unusable_one(doctor):
    old = _stored_in(doctor, 'IR')
    new = _make(doctor, _person())
    old.refresh_from_db()
    assert (new.is_default, old.is_default) == (True, False)


def test_a_region_refusal_at_the_service_step_still_names_and_links_the_site(
        doctor, settings, monkeypatch):
    settings.REGION_SITE_URLS = {**settings.REGION_SITE_URLS, 'ae': 'https://dev-ae.example.org'}
    from accounts.forms import FiscalEntityForm
    form = FiscalEntityForm(_company(country='AE'))
    form.cleaned_data = {'country': 'AE'}
    form.add_service_problems({'country': 'country_other_region'})
    assert 'MyTreatPlan dev-ae.example.org.' in str(form.errors['country'][0])
    assert form.other_site_url == 'https://dev-ae.example.org'
