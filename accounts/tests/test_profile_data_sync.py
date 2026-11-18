"""The fiscal entities and shipping addresses in `client.updated` (epic-doctor-profile story
6.9, mytp-core 0.28.0): the rows 1-7, 12, 15 and 16 of the plan's I/O matrix (the admin site
applies the lists, rows 8-11 and 13-14, in its own tests). The services accounts.fiscal and
accounts.shipping emit a full snapshot after every write that changes what the admin site
copies, inside the write's transaction."""
import base64
import io
import json
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from accounts import client_sync, fiscal, makers, shipping
from accounts.models import FiscalEntity, ShippingAddress
from integration import outbox
from integration.models import OutboxEvent
from integration.tests.snapshot_rows import PORTAL_SEED, put_country
from mytp_core import interface
from mytp_core.contracts import (MAX_ACTIVE_SENT, MAX_ARCHIVED_SENT, MAX_EVENT_BYTES,
                                 ClientUpdated, check_central_payload)
from signup.tests.helpers import PASSWORD

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()
SECRETS = ('B12345674', 'Clínica Espuny', 'Calle Mayor', 'Rosa Vidal', '612345678')


@pytest.fixture(autouse=True)
def setup(settings):
    settings.SITE = 'dev'
    settings.REGION = 'eu'
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 30
    settings.INTERFACE_SIGNING_KEY = f'p1:{base64.b64encode(PORTAL_SEED).decode()}'
    interface.reset_signing_key()
    for code, state in [('ES', 'eu'), ('DE', 'eu'), ('PT', 'eu')]:
        put_country(code, state)
    yield
    interface.reset_signing_key()


@pytest.fixture
def doctor():
    return User.objects.create_user('maria.espuny@example.net', PASSWORD, title='mrs',
                                    first_name='María José', last_name='Espuny Marín',
                                    country='ES', phone='+34611222333')


def _company(**changes):
    data = {'kind': 'company', 'legal_name': 'Clínica Espuny S.L.', 'tax_id': 'es b-1234 5674',
            'country': 'ES', 'address_line1': 'Calle Mayor 1', 'city': 'Madrid',
            'postal_code': '28001'}
    return data | changes


def _person(**changes):
    data = {'kind': 'person', 'first_name': 'Rui', 'last_name': 'Sousa', 'tax_id': '123456789',
            'country': 'PT', 'address_line1': 'Rua Augusta 5', 'city': 'Lisboa',
            'postal_code': '1100-053'}
    return data | changes


def _address(**changes):
    data = {'label': 'Clínica Madrid', 'recipient_name': 'Rosa Vidal',
            'clinic_name': 'Clínica Vidal', 'contact_phone': '612 34 56 78',
            'delivery_notes': 'Llamar al timbre B', 'address_line1': 'Calle Mayor 1',
            'address_line2': '2º A', 'city': 'Madrid', 'postal_code': '28001',
            'province': 'Madrid', 'country': 'ES'}
    return data | changes


def events():
    return list(OutboxEvent.objects.order_by('id'))


def last(user):
    return OutboxEvent.objects.filter(aggregate_id=user.uid).latest('id').payload


def count():
    return OutboxEvent.objects.count()


def validated(payload):
    """The payload as a writer built it: the closed model and the central checks accept it."""
    assert check_central_payload(payload)
    return ClientUpdated.model_validate(payload)


# --- 1. A create sends the list -------------------------------------------------------------

def test_creating_a_fiscal_entity_sends_the_snapshot_with_it(doctor):
    entity = fiscal.create(doctor, _company(billing_email='billing@example.net'))
    assert [event.type for event in events()] == ['client.updated']
    payload = last(doctor)
    validated(payload)
    assert payload['shipping_addresses'] == []
    (item,) = payload['fiscal_entities']
    assert item['id'] == str(entity.pk) and item['kind'] == 'company'
    assert (item['legal_name'], item['tax_id'], item['country'], item['city'],
            item['billing_email']) == ('Clínica Espuny S.L.', 'ESB12345674', 'ES', 'Madrid',
                                       'billing@example.net')
    assert (item['is_default'], item['version'], item['used_at'], item['archived_at']) == (
        True, 1, None, None)
    assert item['vies_status'] == 'valid' and item['vies_checked_at'] is not None
    doctor.refresh_from_db()
    assert doctor.sync_version == 1


def test_creating_a_shipping_address_sends_the_snapshot_with_it(doctor):
    address = shipping.create(doctor, _address())
    payload = last(doctor)
    validated(payload)
    assert payload['fiscal_entities'] == []
    (item,) = payload['shipping_addresses']
    assert item['id'] == str(address.pk)
    assert (item['recipient_name'], item['contact_phone'], item['delivery_notes'],
            item['label'], item['clinic_name'], item['province']) == (
        'Rosa Vidal', '+34612345678', 'Llamar al timbre B', 'Clínica Madrid',
        'Clínica Vidal', 'Madrid')
    assert (item['is_default'], item['version']) == (True, 1)


# --- 2. Edit, default, mark_used --------------------------------------------------------------

@pytest.mark.parametrize('kind', ['fiscal', 'shipping'])
def test_edit_default_and_mark_used_each_send_a_new_version(doctor, kind):
    if kind == 'fiscal':
        service, make, field = fiscal, lambda **c: fiscal.create(doctor, _company(**c)), 'city'
        key = 'fiscal_entities'
        second = lambda: fiscal.create(doctor, _person())  # noqa: E731
    else:
        service, make, field = shipping, lambda **c: shipping.create(doctor, _address(**c)), 'city'
        key = 'shipping_addresses'
        second = lambda: shipping.create(doctor, _address(label='Otra'))  # noqa: E731
    first = make()
    other = second()
    doctor.refresh_from_db()
    before = doctor.sync_version
    service.update(doctor, first.pk, {field: 'Getafe'}, version=1)
    items = {item['id']: item for item in last(doctor)[key]}
    assert (items[str(first.pk)]['city'], items[str(first.pk)]['version']) == ('Getafe', 2)
    service.make_default(doctor, other.pk)
    items = {item['id']: item for item in last(doctor)[key]}
    assert (items[str(first.pk)]['is_default'], items[str(other.pk)]['is_default']) == (
        False, True)
    used = timezone.now()
    assert service.mark_used(first, used) is True
    items = {item['id']: item for item in last(doctor)[key]}
    assert items[str(first.pk)]['used_at'] is not None
    doctor.refresh_from_db()
    assert doctor.sync_version == before + 3
    validated(last(doctor))
    # Nothing changed: nothing is sent.
    seen = count()
    assert service.mark_used(first, used) is False
    service.make_default(doctor, other.pk)
    service.update(doctor, first.pk, {field: 'Getafe'}, version=2)
    assert count() == seen


# --- 3 and 4. Delete an unused one, archive a used one -----------------------------------------

@pytest.mark.parametrize('kind', ['fiscal', 'shipping'])
def test_removing_sends_a_snapshot_without_an_unused_one_and_archives_a_used_one(doctor, kind):
    if kind == 'fiscal':
        service, key = fiscal, 'fiscal_entities'
        a = fiscal.create(doctor, _company())
        b = fiscal.create(doctor, _person())
    else:
        service, key = shipping, 'shipping_addresses'
        a = shipping.create(doctor, _address())
        b = shipping.create(doctor, _address(label='Otra'))
    assert service.remove(doctor, b.pk) == 'deleted'
    assert [item['id'] for item in last(doctor)[key]] == [str(a.pk)]
    service.mark_used(a)
    now = timezone.now()
    assert service.remove(doctor, a.pk, now=now) == 'archived'
    (item,) = last(doctor)[key]
    assert item['id'] == str(a.pk) and item['archived_at'] is not None
    assert item['is_default'] is False and item['used_at'] is not None
    validated(last(doctor))


def test_removing_the_default_sends_the_successor_as_the_default(doctor):
    a = fiscal.create(doctor, _company())
    b = fiscal.create(doctor, _person())
    fiscal.remove(doctor, a.pk)
    (item,) = last(doctor)['fiscal_entities']
    assert (item['id'], item['is_default']) == (str(b.pk), True)


# --- 5. At most 100 archived are sent --------------------------------------------------------

def _archived_addresses(user, n, start=None):
    start = start or timezone.now() - timedelta(days=1000)
    rows = []
    for index in range(n):
        rows.append(ShippingAddress(
            user=user, recipient_name=f'R{index}', address_line1='Calle', city='Madrid',
            country='ES', contact_phone='+34612345678', used_at=start,
            archived_at=start + timedelta(minutes=index)))
    ShippingAddress.objects.bulk_create(rows)
    return rows


def test_only_the_25_most_recently_archived_are_sent(doctor):
    shipping.create(doctor, _address())
    rows = _archived_addresses(doctor, MAX_ARCHIVED_SENT + 1)
    with transaction.atomic():
        client_sync.emit(doctor)
    items = last(doctor)['shipping_addresses']
    assert len(items) == 1 + MAX_ARCHIVED_SENT
    assert items[0]['archived_at'] is None and items[0]['is_default'] is True
    sent = {item['id'] for item in items}
    assert str(rows[0].pk) not in sent  # the oldest
    assert all(str(row.pk) in sent for row in rows[1:])
    archived = [item['archived_at'] for item in items[1:]]
    assert archived == sorted(archived, reverse=True)  # the latest first
    validated(last(doctor))


def test_the_fiscal_archived_are_limited_too(doctor):
    rows = [FiscalEntity(user=doctor, kind='company', legal_name=f'C{index}', tax_id='X1',
                         country='ES', used_at=timezone.now(),
                         archived_at=timezone.now() - timedelta(minutes=index))
            for index in range(MAX_ARCHIVED_SENT + 5)]
    FiscalEntity.objects.bulk_create(rows)
    with transaction.atomic():
        client_sync.emit(doctor)
    items = last(doctor)['fiscal_entities']
    assert len(items) == MAX_ARCHIVED_SENT
    assert {item['id'] for item in items} == {
        str(row.pk) for row in sorted(rows, key=lambda row: row.archived_at)[-MAX_ARCHIVED_SENT:]}


# --- 7. The VIES job -------------------------------------------------------------------------

def test_the_vies_job_sends_the_new_status_but_not_a_mere_retry(doctor, fake_vies):
    fake_vies.status = 'unavailable'
    entity = fiscal.create(doctor, _company(country='DE', tax_id='DE136695976'))
    (item,) = last(doctor)['fiscal_entities']
    assert (item['vies_status'], item['vies_checked_at']) == ('pending', None)
    seen = count()
    entity.refresh_from_db()
    # Still unavailable: only the retry fields change, which never leave this site.
    assert fiscal.retry_due(entity.vies_next_try_at)['still_pending'] == 1
    assert count() == seen
    fake_vies.status = 'valid'
    entity.refresh_from_db()
    counts = fiscal.retry_due(entity.vies_next_try_at)
    assert counts['answered'] == 1
    assert count() == seen + 1
    (item,) = last(doctor)['fiscal_entities']
    assert item['vies_status'] == 'valid' and item['vies_checked_at'] is not None
    validated(last(doctor))
    doctor.refresh_from_db()
    assert doctor.sync_version == 2


def test_a_discarded_vies_answer_sends_nothing(doctor, fake_vies):
    fake_vies.status = 'unavailable'
    entity = fiscal.create(doctor, _company(country='DE', tax_id='DE136695976'))
    seen = count()
    FiscalEntity.objects.filter(pk=entity.pk).update(vies_status='invalid')
    fake_vies.status = 'valid'
    entity.refresh_from_db()
    assert fiscal._write_check(entity, fiscal._answer_fields('valid', timezone.now())) is False
    assert count() == seen


def test_one_entitys_failing_emit_does_not_stop_the_vies_run(doctor, fake_vies, monkeypatch,
                                                              caplog):
    fake_vies.status = 'unavailable'
    first = fiscal.create(doctor, _company(country='DE', tax_id='DE136695976'))
    other = User.objects.create_user('other.doc@example.net', PASSWORD, country='ES',
                                     phone='+34622333444')
    second = fiscal.create(other, _company(country='DE', tax_id='DE136695976'))
    first.refresh_from_db(), second.refresh_from_db()
    FiscalEntity.objects.filter(pk=first.pk).update(
        vies_next_try_at=second.vies_next_try_at - timedelta(minutes=1))
    real = client_sync.emit

    def flaky(user):
        if user.pk == doctor.pk:
            raise outbox.OutboxError('boom')
        return real(user)
    monkeypatch.setattr(client_sync, 'emit', flaky)
    fake_vies.status = 'valid'
    counts = fiscal.retry_due(second.vies_next_try_at)
    assert counts['checked'] == 2 and counts['answered'] == 1
    second.refresh_from_db(), first.refresh_from_db()
    assert second.vies_status == 'valid' and first.vies_status == 'pending'
    assert str(first.pk) in caplog.text and 'DE136695976' not in caplog.text


def test_the_active_limits_fit_the_contract_cap():
    assert fiscal.MAX_ENTITIES <= MAX_ACTIVE_SENT
    assert shipping.MAX_ADDRESSES <= MAX_ACTIVE_SENT


# --- The emit shares the write's transaction ------------------------------------------------

def test_a_failing_emit_rolls_the_write_back(doctor, monkeypatch):
    def broken(user):
        raise outbox.OutboxError('the event is larger than the limit')
    monkeypatch.setattr(client_sync, 'emit', broken)
    with pytest.raises(outbox.OutboxError):
        fiscal.create(doctor, _company())
    with pytest.raises(outbox.OutboxError):
        shipping.create(doctor, _address())
    assert not FiscalEntity.objects.exists() and not ShippingAddress.objects.exists()


def test_the_vies_write_takes_the_users_lock_first(doctor, fake_vies, monkeypatch):
    fake_vies.status = 'unavailable'
    entity = fiscal.create(doctor, _company(country='DE', tax_id='DE136695976'))
    entity.refresh_from_db()
    order = []
    real = QuerySet.select_for_update

    def spy(queryset, *args, **kwargs):
        order.append(queryset.model.__name__)
        return real(queryset, *args, **kwargs)
    monkeypatch.setattr(QuerySet, 'select_for_update', spy)
    fake_vies.status = 'valid'
    fiscal.retry_due(entity.vies_next_try_at)
    assert order.index('User') < order.index('FiscalEntity')


# --- Text the central rules refuse is a field error ------------------------------------------

def _browser(user):
    from django.test import Client
    browser = Client()
    browser.force_login(user)
    return browser


@pytest.mark.parametrize('text', ['a' * 250, 'data:text/plain;base64,QUJD,x',
                                  'https://bucket.s3.amazonaws.com/file'])
def test_a_text_the_central_rules_refuse_is_a_field_error_and_sends_nothing(doctor, text):
    browser = _browser(doctor)
    response = browser.post('/en/settings/shipping/add/', _address(delivery_notes=text))
    assert response.status_code == 200 and not ShippingAddress.objects.exists()
    assert 'delivery_notes' in response.context['form'].errors
    assert 'This text cannot be saved' in str(response.context['form'].errors['delivery_notes'])
    response = browser.post('/en/settings/fiscal/add/', _company(city=text))
    assert response.status_code == 200 and not FiscalEntity.objects.exists()
    assert 'city' in response.context['form'].errors
    assert not OutboxEvent.objects.exists()
    with pytest.raises(shipping.ShippingInvalid) as refused:
        shipping.create(doctor, _address(delivery_notes=text))
    assert refused.value.problems == {'delivery_notes': 'not_allowed'}


# --- 12. What never leaves --------------------------------------------------------------------

BANNED_WORDS = ('maker', 'login', 'password', 'username', 'photo', 'attempts', 'next_try',
                'pending_since', 'alerted', 'patient')


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_no_maker_login_photo_or_vies_retry_field_is_in_any_payload(doctor, fake_vies):
    fake_vies.status = 'unavailable'
    makers.save(doctor, 'invisalign', {'username': 'maker-user-77', 'password': 'maker-pass-88'})
    fiscal.create(doctor, _company(country='DE', tax_id='DE136695976'))
    shipping.create(doctor, _address())
    keys = set(_keys(last(doctor)))
    assert not [key for key in keys if any(word in key for word in BANNED_WORDS)]
    text = json.dumps([event.payload for event in events()], default=str)
    for value in ('maker-user-77', 'maker-pass-88'):
        assert value not in text
    item_keys = set(last(doctor)['fiscal_entities'][0])
    assert item_keys == {
        'id', 'kind', 'first_name', 'last_name', 'legal_name', 'tax_id', 'vat_id',
        'address_line1', 'address_line2', 'city', 'postal_code', 'province', 'country',
        'billing_email', 'is_default', 'used_at', 'archived_at', 'version', 'vies_status',
        'vies_checked_at'}
    assert set(last(doctor)['shipping_addresses'][0]) == {
        'id', 'label', 'recipient_name', 'clinic_name', 'contact_phone', 'delivery_notes',
        'address_line1', 'address_line2', 'city', 'postal_code', 'province', 'country',
        'is_default', 'used_at', 'archived_at', 'version'}


def test_the_outbox_and_logs_never_carry_a_value(doctor, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    fiscal.create(doctor, _company())
    shipping.create(doctor, _address())
    for value in SECRETS:
        assert value not in caplog.text


# --- 15. The largest snapshot ----------------------------------------------------------------

def test_the_largest_snapshot_is_accepted_by_the_outbox(doctor):
    # 10 active + 25 archived of each list, every text field at its maximum length with
    # 2-byte characters; 5 more archived rows exist and are not sent.
    def e(n):
        return 'é' * n

    now = timezone.now()
    fiscal_rows, address_rows = [], []
    total = MAX_ACTIVE_SENT + MAX_ARCHIVED_SENT + 5
    for index in range(total):
        archived = index >= MAX_ACTIVE_SENT
        stamp = now - timedelta(minutes=index) if archived else None
        fiscal_rows.append(FiscalEntity(
            user=doctor, kind='company', first_name=e(150), last_name=e(150),
            legal_name=e(200), tax_id=e(40), vat_id=e(40), address_line1=e(200),
            address_line2=e(200), city=e(150), postal_code=e(30), province=e(150),
            country='ES', billing_email=e(200) + '@' + e(53), used_at=now,
            archived_at=stamp, is_default=index == 0, vies_checked_at=now))
        address_rows.append(ShippingAddress(
            user=doctor, label=e(100), recipient_name=e(150), clinic_name=e(150),
            contact_phone='+34612345678', delivery_notes=e(300), address_line1=e(200),
            address_line2=e(200), city=e(150), postal_code=e(30), province=e(150),
            country='ES', used_at=now, archived_at=stamp, is_default=index == 0))
    FiscalEntity.objects.bulk_create(fiscal_rows)
    ShippingAddress.objects.bulk_create(address_rows)
    with transaction.atomic():
        client_sync.emit(doctor)
    payload = last(doctor)
    assert len(payload['fiscal_entities']) == len(payload['shipping_addresses']) == (
        MAX_ACTIVE_SENT + MAX_ARCHIVED_SENT)
    event = OutboxEvent.objects.get()
    body = interface.json_body(outbox.envelope_of(event))
    assert len(body) < MAX_EVENT_BYTES - 32, len(body)
    validated(payload)


# --- 16. The backfill ------------------------------------------------------------------------

def test_sync_clients_sends_the_lists_of_every_doctor(doctor):
    fiscal.create(doctor, _company())
    shipping.create(doctor, _address())
    bea = User.objects.create_user('bea@example.com', PASSWORD, first_name='Bea',
                                   country='ES', phone='+34622333444')
    OutboxEvent.objects.all().delete()
    out = io.StringIO()
    call_command('sync_clients', '--all', stdout=out)
    assert 'sync_clients: 2 client.updated' in out.getvalue()
    mine = OutboxEvent.objects.filter(aggregate_id=doctor.uid).get().payload
    assert len(mine['fiscal_entities']) == 1 and len(mine['shipping_addresses']) == 1
    theirs = OutboxEvent.objects.filter(aggregate_id=bea.uid).get().payload
    assert theirs['fiscal_entities'] == [] and theirs['shipping_addresses'] == []


def test_erasing_the_account_still_sends_only_the_tombstone(doctor):
    from accounts.erasure import erase_user
    staff = User.objects.create_user('staff@example.net', PASSWORD, is_staff=True)
    fiscal.create(doctor, _company())
    shipping.create(doctor, _address())
    erase_user(doctor, by=staff)
    event = OutboxEvent.objects.filter(aggregate_id=doctor.uid).latest('id')
    assert (event.type, event.payload) == ('client.erased', {})
    assert not OutboxEvent.objects.filter(aggregate_id=doctor.uid, type='client.updated'
                                          ).filter(state=OutboxEvent.PENDING).exists()
