"""The doctor's maker logins (epic-doctor-profile entry 8): the rows 1-19 of the plan's
I/O matrix. The service accounts.makers, the pages accounts.views.settings_maker*, the
regional reveal seam accounts.makers_remote, the erasure and the ES interface."""
import json
import logging
import re
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.utils import timezone

from accounts import client_sync, makers, makers_remote
from accounts.erasure import erase_user, purge_erased
from accounts.models import MakerLogin
from eventlog.models import Event
from integration.models import OutboxEvent
from signup.tests.helpers import PASSWORD

from .helpers import _html

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()
LIST = '/en/settings/maker/'
ADD_OTHER = '/en/settings/maker/other/add/'
USERNAME = 'dr.espuny.aux77'
SECRET = 'S3cr3t-Pa55word!zq'


def _save_url(maker):
    return f'/en/settings/maker/{maker}/'


def _delete_url(maker):
    return f'/en/settings/maker/{maker}/delete/'


def _other_url(login_id):
    return f'/en/settings/maker/other/{login_id}/'


def _other_delete(login_id):
    return f'/en/settings/maker/other/{login_id}/delete/'


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


@pytest.fixture(autouse=True)
def retention(settings):
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 30


def _browser(user):
    browser = Client()
    browser.force_login(user)
    return browser


def _creds(**changes):
    return {'username': USERNAME, 'password': SECRET, **changes}


def _make(user, maker='invisalign', **changes):
    return makers.save(user, maker, _creds(**changes))


def _errors(response, field):
    return [str(error) for error in response.context['form'].errors.get(field, [])]


def _no_secret(html, *values):
    for value in values or (USERNAME, SECRET):
        for part in (value, value[:6], value[-6:]):
            assert part not in html, part


# --- 1, 2. A save: encrypted, only the date is shown ---------------------------------------

def test_a_doctor_saves_an_invisalign_login_and_the_page_shows_only_the_date(doctor):
    browser = _browser(doctor)
    response = browser.post(_save_url('invisalign'), _creds())
    assert response.status_code == 302 and response['Location'] == LIST
    login = MakerLogin.objects.get()
    assert (login.user, login.maker, login.username, login.password) == (
        doctor, 'invisalign', USERNAME, SECRET)
    html = _html(browser.get(LIST))
    assert 'Saved on' in html and str(login.saved_at.year) in html
    assert html.count('data-maker-status="saved"') == 1
    assert html.count('data-maker-status="not-saved"') == 4
    assert re.search(r'data-maker="invisalign".*?Replace', html, re.S)
    _no_secret(html)
    _no_secret(_html(browser.get(_save_url('invisalign'))))
    _no_secret(_html(browser.get(_delete_url('invisalign'))))


def test_no_page_renders_a_value_after_an_error_either(doctor):
    browser = _browser(doctor)
    _make(doctor)
    for url, data in [(_save_url('invisalign'), {'username': USERNAME, 'password': ''}),
                      (_save_url('spark'), {'username': '', 'password': SECRET}),
                      (_save_url('spark'), {'username': USERNAME + 'x' * 300,
                                            'password': SECRET}),
                      (ADD_OTHER, {'other_name': '', 'username': USERNAME,
                                   'password': SECRET})]:
        html = _html(browser.post(url, data))
        _no_secret(html)
        assert 'value="' + USERNAME not in html


# --- 3. A missing field --------------------------------------------------------------------

def test_a_missing_username_or_password_is_a_field_error_with_both_inputs_empty(doctor):
    browser = _browser(doctor)
    for data, field in [({'username': USERNAME, 'password': ''}, 'password'),
                        ({'username': '', 'password': SECRET}, 'username'),
                        ({'username': '   ', 'password': SECRET}, 'username')]:
        response = browser.post(_save_url('invisalign'), data)
        assert response.status_code == 200
        assert _errors(response, field) == ['This field is required.']
        html = _html(response)
        assert re.search(r'<input[^>]*name="password"[^>]*>', html)
        for tag in re.findall(r'<input[^>]*name="(?:username|password)"[^>]*>', html):
            assert 'value=' not in tag
    assert not MakerLogin.objects.exists()


def test_the_secret_inputs_ask_the_browser_not_to_fill_or_save(doctor):
    html = _html(_browser(doctor).get(_save_url('invisalign')))
    password = re.search(r'<input[^>]*name="password"[^>]*>', html).group(0)
    username = re.search(r'<input[^>]*name="username"[^>]*>', html).group(0)
    assert 'type="password"' in password and 'autocomplete="new-password"' in password
    assert 'autocomplete="off"' in username
    assert 'autocomplete="off"' in html  # the form itself
    assert 'auxiliary profile' in html and 'data-maker-hint' in html


# --- 4. Replace ----------------------------------------------------------------------------

def test_a_replace_overwrites_the_old_values_and_updates_the_date(doctor):
    _make(doctor)
    MakerLogin.objects.update(saved_at=timezone.now() - timedelta(days=30))
    old = MakerLogin.objects.get().saved_at
    with connection.cursor() as cursor:
        cursor.execute('SELECT username, password FROM accounts_makerlogin')
        raw_before = cursor.fetchone()
    response = _browser(doctor).post(_save_url('invisalign'),
                                     {'username': 'new.user', 'password': 'N3w-pass'})
    assert response.status_code == 302
    login = MakerLogin.objects.get()  # still one row
    assert (login.username, login.password) == ('new.user', 'N3w-pass')
    assert login.saved_at > old
    with connection.cursor() as cursor:
        cursor.execute('SELECT username, password FROM accounts_makerlogin')
        raw_after = cursor.fetchone()
    assert raw_after[0] != raw_before[0] and raw_after[1] != raw_before[1]


def test_a_write_after_the_account_was_erased_creates_nothing(doctor, staff):
    stale = User.objects.get(pk=doctor.pk)  # read before the erasure
    assert erase_user(doctor, by=staff)
    with pytest.raises(ValueError):
        makers.save(stale, 'invisalign', _creds())
    with pytest.raises(ValueError):
        makers.add_other(stale, 'Clear Aligner X', _creds())
    assert not MakerLogin.objects.exists()


# --- 5. Delete -----------------------------------------------------------------------------

def test_a_confirmed_delete_removes_the_row(doctor):
    _make(doctor)
    browser = _browser(doctor)
    assert 'Delete this maker login?' in _html(browser.get(_delete_url('invisalign')))
    assert MakerLogin.objects.count() == 1  # the GET deletes nothing
    assert browser.post(_delete_url('invisalign'))['Location'] == LIST
    assert not MakerLogin.objects.exists()
    html = _html(browser.get(LIST))
    assert html.count('data-maker-status="not-saved"') == 5
    assert browser.get(_delete_url('invisalign')).status_code == 404


# --- 6, 7. Other makers --------------------------------------------------------------------

def test_an_other_login_is_saved_listed_replaced_and_deleted(doctor):
    browser = _browser(doctor)
    response = browser.post(ADD_OTHER, _creds(other_name='Clear Aligner X'))
    assert response['Location'] == LIST
    login = MakerLogin.objects.get()
    assert (login.maker, login.other_name) == ('other', 'Clear Aligner X')
    html = _html(browser.get(LIST))
    assert 'Clear Aligner X' in html and 'data-maker-other="%s"' % login.pk in html
    _no_secret(html)
    assert browser.post(_other_url(login.pk), {'username': 'u2', 'password': 'p2'}
                        )['Location'] == LIST
    login.refresh_from_db()
    assert (login.username, login.password, login.other_name) == ('u2', 'p2',
                                                                  'Clear Aligner X')
    assert MakerLogin.objects.count() == 1
    _no_secret(_html(browser.get(_other_url(login.pk))), 'u2', 'p2')
    assert browser.post(_other_delete(login.pk))['Location'] == LIST
    assert not MakerLogin.objects.exists()


def test_an_other_login_needs_a_name_that_is_unique_ignoring_case_and_short(doctor):
    browser = _browser(doctor)
    response = browser.post(ADD_OTHER, _creds(other_name=''))
    assert _errors(response, 'other_name') == ['This field is required.']
    browser.post(ADD_OTHER, _creds(other_name='Clear Aligner X'))
    response = browser.post(ADD_OTHER, _creds(other_name='clear aligner x'))
    assert response.status_code == 200 and _errors(response, 'other_name')
    assert 'name="other_name" value="clear aligner x"' in _html(response)  # name kept
    response = browser.post(ADD_OTHER, _creds(other_name='N' * 61))
    assert _errors(response, 'other_name')
    assert MakerLogin.objects.count() == 1
    assert browser.post(ADD_OTHER, _creds(other_name='N' * 60)).status_code == 302
    with pytest.raises(makers.MakerInvalid) as refused:
        makers.add_other(doctor, 'CLEAR ALIGNER X', _creds())
    assert refused.value.problems == {'other_name': 'duplicate'}
    # Another doctor may use the same name.


def test_another_doctor_may_use_the_same_other_name(doctor, other):
    makers.add_other(doctor, 'Clear Aligner X', _creds())
    makers.add_other(other, 'Clear Aligner X', _creds())
    assert MakerLogin.objects.count() == 2


def test_a_sixth_other_login_is_refused(doctor):
    browser = _browser(doctor)
    for index in range(5):
        makers.add_other(doctor, f'Maker {index}', _creds())
    page = _html(browser.get(LIST))
    assert 'data-maker-other-limit' in page and 'data-maker-other-add' not in page
    response = browser.get(ADD_OTHER)
    assert response['Location'] == LIST
    assert 'at most 5 logins' in _html(browser.get(LIST))
    response = browser.post(ADD_OTHER, _creds(other_name='Maker 6'))
    assert response['Location'] == LIST
    with pytest.raises(makers.MakerInvalid) as refused:
        makers.add_other(doctor, 'Maker 6', _creds())
    assert refused.value.problems == {'__all__': 'limit'}
    assert MakerLogin.objects.count() == 5


# --- 8. Unknown maker, two rows for one named maker ----------------------------------------

def test_an_unknown_maker_is_404_and_a_named_maker_never_has_two_rows(doctor):
    browser = _browser(doctor)
    for maker in ('nobody', 'other'):
        assert browser.get(_save_url(maker)).status_code == 404
        assert browser.post(_save_url(maker), _creds()).status_code == 404
        assert browser.get(_delete_url(maker)).status_code == 404
    assert not MakerLogin.objects.exists()
    with pytest.raises(ValueError):
        makers.save(doctor, 'nobody', _creds())
    with pytest.raises(ValueError):
        makers.save(doctor, 'invisalign', _creds(), other_name='X')
    _make(doctor)
    now = timezone.now()
    with pytest.raises(IntegrityError), transaction.atomic():
        MakerLogin.objects.create(user=doctor, maker='invisalign', username='a',
                                  password='b', saved_at=now)
    with pytest.raises(IntegrityError), transaction.atomic():
        MakerLogin.objects.create(user=doctor, maker='other', other_name='Q', username='a',
                                  password='b', saved_at=now)
        MakerLogin.objects.create(user=doctor, maker='other', other_name='q', username='a',
                                  password='b', saved_at=now)
    # The same maker for another doctor is fine.


# --- 9. Another doctor's login -------------------------------------------------------------

def test_another_doctors_login_id_is_404(doctor, other):
    mine = makers.add_other(doctor, 'Clear Aligner X', _creds())
    browser = _browser(other)
    for url in (_other_url(mine), _other_delete(mine)):
        assert browser.get(url).status_code == 404
        assert browser.post(url, _creds()).status_code == 404
    assert browser.get(_other_url(uuid.uuid4())).status_code == 404
    # A named maker's login is not another doctor's either: each doctor has their own.
    _make(doctor)
    assert browser.get(_delete_url('invisalign')).status_code == 404
    assert MakerLogin.objects.count() == 2
    with pytest.raises(MakerLogin.DoesNotExist):
        makers.remove(other, mine)
    # A named maker's row is not reachable through the "other" pages.
    named = MakerLogin.objects.get(maker='invisalign')
    assert _browser(doctor).get(_other_url(named.pk)).status_code == 404


# --- 10. Staff and anonymous visitors ------------------------------------------------------

def test_staff_gets_404_and_anonymous_visitors_are_sent_to_login(doctor, staff, client):
    login = makers.add_other(doctor, 'Clear Aligner X', _creds())
    urls = (LIST, ADD_OTHER, _save_url('invisalign'), _delete_url('invisalign'),
            _other_url(login), _other_delete(login))
    browser = _browser(staff)
    for url in urls:
        assert browser.get(url).status_code == 404, url
        if url != LIST:  # the list takes GET only
            assert browser.post(url, _creds(other_name='Z')).status_code == 404, url
    assert MakerLogin.objects.count() == 1
    for url in urls:
        assert client.get(url)['Location'] == f'/en/login/?next={url}'
    with pytest.raises(ValueError):
        makers.save(staff, 'invisalign', _creds())


# --- 11. The safe next ---------------------------------------------------------------------

def test_the_save_page_returns_to_a_path_of_this_site_and_ignores_others(doctor):
    browser = _browser(doctor)
    page = _html(browser.get(_save_url('invisalign') + '?next=/en/cart/'))
    assert 'name="next" value="/en/cart/"' in page
    assert browser.post(_save_url('invisalign'),
                        {**_creds(), 'next': '/en/cart/'})['Location'] == '/en/cart/'
    for bad in ('https://evil.example/x', '//evil.example/x', 'javascript:alert(1)',
                '/\\evil.com', '/%2F%2Fevil.com', '/\tevil.com'):
        assert 'name="next"' not in _html(browser.get(_save_url('invisalign'), {'next': bad}))
        response = browser.post(_save_url('invisalign'), {**_creds(), 'next': bad})
        assert response['Location'] == LIST


# --- 12. What epic-ordering reads ----------------------------------------------------------

def test_has_login_and_saved_makers(doctor, other):
    assert makers.has_login(doctor, 'invisalign') is False
    assert makers.saved_makers(doctor) == []
    _make(doctor, 'smartee')
    _make(doctor, 'invisalign')
    makers.add_other(doctor, 'Clear Aligner X', _creds())
    assert makers.has_login(doctor, 'invisalign') is True
    assert makers.has_login(doctor, 'spark') is False
    assert makers.has_login(doctor, 'other') is False  # never blocks, never counts
    assert makers.has_login(doctor, 'nobody') is False
    assert makers.saved_makers(doctor) == ['invisalign', 'smartee']
    assert makers.has_login(other, 'invisalign') is False
    assert makers.saved_makers(other) == []


def test_logins_are_metadata_only(doctor):
    _make(doctor)
    makers.add_other(doctor, 'Clear Aligner X', _creds())
    rows = makers.logins(doctor)
    assert [row['maker'] for row in rows] == ['invisalign', 'other']
    for row in rows:
        assert set(row) == {'id', 'maker', 'other_name', 'saved_at'}


# --- 13. Erasure ---------------------------------------------------------------------------

def test_erasing_the_account_deletes_its_logins(doctor, other, staff):
    _make(doctor), _make(doctor, 'spark')
    makers.add_other(doctor, 'Clear Aligner X', _creds())
    kept = _make(other)
    assert erase_user(doctor, by=staff)
    assert not MakerLogin.objects.filter(user=doctor).exists()
    assert list(MakerLogin.objects.values_list('pk', flat=True)) == [kept]
    User.objects.filter(pk=doctor.pk).update(purge_after=timezone.now() - timedelta(days=1))
    assert purge_erased() == 1
    assert MakerLogin.objects.count() == 1


def test_the_database_cascades_with_the_user(doctor):
    _make(doctor)
    User.objects.filter(pk=doctor.pk).delete()
    assert not MakerLogin.objects.exists()


# --- 14. No event, no log, no record -------------------------------------------------------

def test_a_write_leaves_no_trace_in_events_logs_or_records(doctor, caplog):
    caplog.set_level(logging.DEBUG)
    OutboxEvent.objects.all().delete()
    before = Event.objects.order_by('-id').values_list('id', flat=True).first() or 0
    browser = _browser(doctor)
    browser.get(LIST)
    browser.get(_save_url('invisalign'))
    browser.post(_save_url('invisalign'), _creds())
    browser.post(_save_url('invisalign'), {'username': 'u2', 'password': 'Other-Pass-2'})
    browser.post(_save_url('invisalign'), {'username': '', 'password': 'Err-Pass-3'})
    browser.post(ADD_OTHER, _creds(other_name='Clear Aligner X'))
    browser.post(_delete_url('invisalign'))
    # The payload and the outbox hold no maker login key or value.
    from accounts.client_sync import payload_of
    payload = json.dumps(payload_of(doctor), default=str)
    assert 'maker' not in payload and 'password' not in payload
    with transaction.atomic():
        client_sync.emit(doctor)
    texts = [json.dumps(event.payload, default=str) for event in OutboxEvent.objects.all()]
    assert texts and not any('maker' in text.lower() for text in texts)
    secrets = (USERNAME, SECRET, 'u2', 'Other-Pass-2', 'Err-Pass-3')
    lines = [record.getMessage() + str(record.args) for record in caplog.records
             if not (record.name == 'signup.verification'
                     and record.getMessage().startswith('Verification code for'))]
    records = json.dumps([[event.event, event.step, event.field, event.reason, event.data]
                          for event in Event.objects.filter(id__gt=before)], default=str)
    assert 'settings_maker' in records  # the pages were recorded
    for value in secrets:
        assert not any(value in line for line in lines), value
        assert value not in records, value
        assert all(value not in text for text in texts), value


def test_the_model_never_shows_a_value_as_text(doctor):
    login = MakerLogin.objects.get(pk=_make(doctor))
    assert str(login) == f'Maker login {login.pk}'
    assert USERNAME not in repr(login) and SECRET not in str(login)


# --- 15. Ciphertext at rest ----------------------------------------------------------------

def test_username_and_password_are_ciphertext_at_rest(doctor):
    _make(doctor)
    with connection.cursor() as cursor:
        cursor.execute('SELECT username, password, maker FROM accounts_makerlogin')
        username, password, maker = cursor.fetchone()
    assert username and password
    assert USERNAME not in username and SECRET not in password
    assert maker == 'invisalign'


# --- 16-18. The reveal seam ----------------------------------------------------------------

def test_the_seam_loads_by_id_and_answers_none_for_a_missing_one(doctor):
    login_id = _make(doctor)
    record = makers_remote.load(login_id)
    assert record is not None and record.pk == login_id
    assert makers_remote.load(uuid.uuid4()) is None
    assert makers_remote.load(None) is None
    assert makers_remote.load('not-a-uuid') is None
    assert makers_remote.RESOURCE == 'maker_login'
    # A login in another region is not in this region's database: the same None.
    MakerLogin.objects.all().delete()
    assert makers_remote.load(login_id) is None


def test_the_seam_assignee_is_nobody_yet(doctor):
    record = makers_remote.load(_make(doctor))
    assert makers_remote.assignee(record) is None


def test_the_seam_builds_only_the_requested_fields_decrypted(doctor):
    record = makers_remote.load(_make(doctor))
    assert makers_remote.build(record, ['username', 'password']) == {
        'username': USERNAME, 'password': SECRET}
    assert makers_remote.build(record, ['maker']) == {'maker': 'invisalign'}
    assert makers_remote.build(record, []) == {}
    with pytest.raises(ValueError):
        makers_remote.build(record, ['username', 'user_id'])


def test_the_seam_version_changes_with_a_replace(doctor):
    login_id = _make(doctor)
    first = makers_remote.version(makers_remote.load(login_id))
    _make(doctor, password='Another-Pass-1')
    assert makers_remote.version(makers_remote.load(login_id)) > first


# --- 19. The ES interface ------------------------------------------------------------------

def test_the_spanish_interface_is_translated_with_usted(doctor):
    browser = _browser(doctor)
    html = _html(browser.get('/es/settings/maker/'))
    for text in ('Datos de fabricantes', 'No guardado', 'Otros fabricantes',
                 'Añadir otro fabricante', 'Sus accesos', 'Guardar'):
        assert text in html, text
    _make(doctor)
    html = _html(browser.get('/es/settings/maker/'))
    assert 'Guardado el' in html and 'Sustituir' in html and 'Eliminar' in html
    html = _html(browser.get('/es/settings/maker/invisalign/'))
    for text in ('Usuario o email', 'Contraseña', 'Le recomendamos crear un perfil auxiliar',
                 'Introduzca de nuevo'):
        assert text in html, text
    html = _html(browser.get('/es/settings/maker/other/add/'))
    assert 'Nombre del fabricante' in html
    assert '¿Desea eliminar este acceso de fabricante?' in _html(
        browser.get('/es/settings/maker/invisalign/delete/'))
    assert 'Este campo es obligatorio.' in _html(
        browser.post('/es/settings/maker/spark/', {'username': '', 'password': 'x'}))
    makers.add_other(doctor, 'Clear Aligner X', _creds())
    response = browser.post('/es/settings/maker/other/add/',
                            _creds(other_name='clear aligner x'))
    assert 'Ya tiene guardado un acceso' in _html(response)


def test_the_tile_and_the_page_replace_coming_soon(doctor):
    html = _html(_browser(doctor).get(LIST))
    assert 'coming soon' not in html
    assert 'data-settings-tile="settings_maker"' in html and 'aria-current="page"' in html
    assert 'data-maker-other-empty' in html and 'data-maker-other-add' in html
    assert [name for name in ('invisalign', 'spark', 'angel', 'solventum', 'smartee')
            if f'data-maker="{name}"' in html] == ['invisalign', 'spark', 'angel',
                                                   'solventum', 'smartee']


def test_the_service_refuses_unknown_fields_and_non_doctors(doctor, staff):
    with pytest.raises(ValueError):
        makers.save(doctor, 'invisalign', {'username': 'a', 'password': 'b', 'x': 'c'})
    with pytest.raises(ValueError):
        makers.save(staff, 'invisalign', _creds())
    with pytest.raises(makers.MakerInvalid) as refused:
        makers.save(doctor, 'invisalign', {'username': '', 'password': ''})
    assert refused.value.problems == {'username': 'required', 'password': 'required'}
    assert str(refused.value) and USERNAME not in str(refused.value)
