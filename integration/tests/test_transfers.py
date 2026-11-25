"""Pending bank transfers on the admin site (epic-ordering 9.18; mytp_core 0.34.0, resource
`pending_transfer`): the list read (signed, each order in the access log, the role's
fields, no patient data, no-store), the confirm and cancel writes (one service call as
`admin_actor`, the order's own total, one invoice, stale, idempotent, no permission) and
their refusals. The admin site is played by its signing key (a fixed, non-secret test
seed); no test reaches the network."""
import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from integration import transfers
from integration.models import RemoteAccess, RemoteWrite
from integration.tests.snapshot_rows import ADMIN
from invoices.models import Invoice
from mytp_core import interface
from mytp_core.contracts import PendingTransferData, RemoteList, RemoteRecord
from payments.models import Order, OrderChange, Payment
from payments.tests.conftest import *  # noqa: F401,F403  (the doctor, catalogue, hooks)
from payments.tests.test_bank_transfer import (BANK, attempt, backdate, bank, start_transfer,
                                               switch_to_card)
from payments.tests.test_service import new

__all__ = ['bank']

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls', 'admin_site')]

URL = '/api/v1/remote/pending-transfers'
ME = '6f1c2b8a-3d4e-4f5a-9b6c-7d8e9f0a1b2c'
TRY_ME = 'f0000001-0000-4000-8000-000000000001'
FIELDS = ['client_uid', 'company_code', 'last_reminder_at', 'pending_at', 'reference',
          'state', 'total']


@pytest.fixture
def admin_site(interface_settings):
    """admindev is the admin site of this region (the remote reads and writes)."""
    interface_settings.INTERFACE_ADMIN_SITE = 'admindev'
    return interface_settings


def scope(role='financial_manager', sub=ME, regions=('ae', 'eu')):
    return {'sub': sub, 'role': role, 'regions': list(regions)}


def _headers(method, raw, extra):
    token = interface.sign_request(method, URL, raw, audience='dev', issuer='admindev',
                                   signing_key=ADMIN, extra=extra)
    return {'HTTP_AUTHORIZATION': interface.authorization_header(token),
            'REMOTE_ADDR': '203.0.113.9', 'HTTP_USER_AGENT': 'mytp-admindev-test'}


def read(client, **kwargs):
    return client.get(URL, **_headers('GET', b'', scope(**kwargs)))


def body(order, state='paid', expected=None, key=None):
    return {'idempotency_key': key or str(uuid.uuid4()),
            'expected_version': order.version if expected is None else expected,
            'object_id': str(order.pk), 'changes': {'state': state}}


def post(client, request, **kwargs):
    raw = interface.json_body(request)
    return client.generic('POST', URL, raw, content_type='application/json',
                          **_headers('POST', raw, scope(**kwargs)))


def rows(**filters):
    return list(RemoteAccess.objects.filter(**filters).order_by('at', 'id'))


@pytest.fixture
def pending(doctor, entity, bank):
    """Two orders waiting for a transfer (one reminded), a paid one and one switched to
    card (none of these two listed)."""
    first = start_transfer(doctor)
    backdate(first, 9)
    Payment.objects.filter(order=first).update(
        reminded_at=timezone.now() - timedelta(days=2))
    second = start_transfer(doctor)
    order = new(doctor)
    Order.objects.filter(pk=order.pk).update(state=Order.PAID)
    switch_to_card(start_transfer(doctor))
    first.refresh_from_db()
    second.refresh_from_db()
    return first, second


# --- List -------------------------------------------------------------------------------

def test_the_list_has_the_pending_transfer_orders_oldest_first(client, doctor, pending):
    first, second = pending
    response = read(client)
    assert response.status_code == 200
    assert 'no-store' in response['Cache-Control']
    answer = response.json()
    RemoteList.model_validate(answer)
    assert (answer['resource'], answer['region'], answer['fields'], answer['truncated']) == (
        'pending_transfer', 'eu', FIELDS, False)
    assert [r['object_id'] for r in answer['records']] == [str(first.pk), str(second.pk)]
    # The order switched to card is pending_payment too, without an open transfer.
    switched = Order.objects.get(state=Order.PENDING_PAYMENT, gateway='fake')
    assert not switched.payments.filter(gateway='bank_transfer', state='open').exists()
    assert str(switched.pk) not in [r['object_id'] for r in answer['records']]
    record = answer['records'][0]
    assert record['version'] == first.version
    reminded = Payment.objects.get(order=first).reminded_at
    assert record['data'] == {
        'reference': first.reference, 'client_uid': str(doctor.uid),
        'company_code': first.company_code,
        'total': {'amount': str(first.total), 'currency': first.currency},
        'pending_at': interface_time(first.pending_at),
        'last_reminder_at': interface_time(reminded), 'state': 'pending_payment'}
    assert answer['records'][1]['data']['last_reminder_at'] is None
    PendingTransferData.model_validate(record['data'])


def interface_time(value):
    from mytp_core.contracts import utc_text
    return utc_text(value)


def test_every_listed_order_is_in_the_access_log(client, pending):
    first, second = pending
    read(client, role='commercial_regional_manager', regions=('eu',))
    logged = rows()
    assert sorted(r.object_id for r in logged) == sorted([str(first.pk), str(second.pk)])
    for row in logged:
        assert (row.operation, row.outcome, row.actor_type, str(row.actor_uuid), row.role,
                row.resource, row.fields, row.access_reason, row.source_site) == (
            'read', 'granted', 'admin', ME, 'commercial_regional_manager',
            'pending_transfer', FIELDS, 'list', 'admindev')


def test_an_empty_list_is_logged_once(client, db):
    response = read(client)
    assert response.status_code == 200
    assert response.json()['records'] == []
    (row,) = rows()
    assert (row.outcome, row.object_id, row.access_reason) == ('granted', '', 'list')


def test_no_patient_data_in_the_answer(client, pending):
    text = read(client).content.decode()
    assert 'patient' not in text.lower()
    for record in json.loads(text)['records']:
        assert set(record['data']) == set(FIELDS)


@pytest.mark.parametrize('role, regions, reason', [
    ('clinical_manager', ('eu',), 'role'),
    ('clinical_user', ('eu',), 'role'),
    ('commercial_regional_manager', ('ae',), 'region'),
])
def test_the_list_is_refused_without_access(client, pending, role, regions, reason):
    response = read(client, role=role, regions=regions)
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'forbidden'
    (row,) = rows()
    assert (row.outcome, row.reason) == ('refused', reason)


def test_no_list_when_the_access_log_cannot_be_written(client, pending, monkeypatch):
    from integration import access_log

    def broken(*args, **kwargs):
        raise access_log.AccessLogUnavailable('down')
    monkeypatch.setattr(access_log, 'record_granted_list', broken)
    response = read(client)
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'access_log_unavailable'
    assert b'records' not in response.content


def test_a_long_list_is_truncated(client, pending, monkeypatch):
    monkeypatch.setattr('integration.remote.MAX_LIST_RECORDS', 1)
    monkeypatch.setattr('mytp_core.contracts.remote.MAX_LIST_RECORDS', 1)
    answer = read(client).json()
    assert answer['truncated'] is True and len(answer['records']) == 1
    assert len(rows()) == 1


def test_another_signed_peer_cannot_list(client, pending):
    from integration.tests.test_remote import DEV_AE
    token = interface.sign_request('GET', URL, b'', audience='dev', issuer='dev-ae',
                                   signing_key=DEV_AE, extra=scope())
    response = client.get(URL, HTTP_AUTHORIZATION=interface.authorization_header(token))
    assert response.status_code == 401


def test_an_unsigned_request_is_refused(client, pending):
    assert client.get(URL).status_code == 401


# --- Confirm ----------------------------------------------------------------------------

def test_a_financial_manager_confirms_the_payment(client, pending, events):
    order, _ = pending
    response = post(client, body(order))
    assert response.status_code == 200
    assert 'no-store' in response['Cache-Control']
    answer = response.json()
    RemoteRecord.model_validate(answer)
    assert answer['object_id'] == str(order.pk)
    assert answer['version'] == order.version + 1
    assert answer['data']['state'] == 'paid'
    order.refresh_from_db()
    assert order.state == 'paid' and order.paid_at is not None
    assert attempt(order).state == 'confirmed'
    assert Invoice.objects.filter(order=order).count() == 1
    assert [e for e, *_ in events].count('paid') == 1
    change = OrderChange.objects.get(order=order, event='paid')
    assert (change.actor_type, str(change.actor_uuid)) == ('admin', ME)
    assert (order.actor_type, str(order.actor_uuid)) == ('admin', ME)
    (row,) = rows()
    assert (row.operation, row.outcome, row.access_reason, row.object_id) == (
        'write', 'granted', 'write:state', str(order.pk))
    # The order left the list.
    assert str(order.pk) not in [r['object_id'] for r in read(client).json()['records']]


def test_confirm_uses_the_orders_own_total(client, pending, monkeypatch):
    order, _ = pending
    seen = []
    real = transfers.service.mark_transfer_paid

    def spy(target, amount, **kwargs):
        seen.append(amount)
        return real(target, amount, **kwargs)
    monkeypatch.setattr(transfers.service, 'mark_transfer_paid', spy)
    assert post(client, body(order)).status_code == 200
    assert seen == [order.total]


def test_the_same_form_twice_is_applied_once(client, pending, events):
    order, _ = pending
    request = body(order)
    first = post(client, request)
    second = post(client, request)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert [e for e, *_ in events].count('paid') == 1
    assert Invoice.objects.filter(order=order).count() == 1
    assert RemoteWrite.objects.count() == 1


def test_a_stale_version_is_refused_and_nothing_applied(client, pending, events):
    order, _ = pending
    response = post(client, body(order, expected=order.version - 1))
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'stale_version'
    assert response.json()['current_version'] == order.version
    order.refresh_from_db()
    assert order.state == 'pending_payment' and attempt(order).state == 'open'
    assert not events and not RemoteWrite.objects.exists()


def test_an_order_paid_meanwhile_by_another_staff_user_is_stale(client, pending, events):
    order, _ = pending
    listed = order.version
    assert post(client, body(order), sub=str(uuid.uuid4())).status_code == 200
    response = post(client, body(order, expected=listed))
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'stale_version'
    assert [e for e, *_ in events].count('paid') == 1


@pytest.mark.parametrize('role', ['commercial_manager', 'commercial_regional_manager'])
def test_commercial_roles_cannot_confirm_or_cancel(client, pending, role):
    order, _ = pending
    for state in ('paid', 'cancelled'):
        response = post(client, body(order, state), role=role)
        assert response.status_code == 403
    order.refresh_from_db()
    assert order.state == 'pending_payment'
    assert {(r.outcome, r.reason) for r in rows()} == {('refused', 'field')}


@pytest.mark.parametrize('changes', [
    {'state': 'pending_payment'}, {'state': 'paid', 'total': '1.00'}, {'total': '1.00'},
])
def test_any_other_change_is_refused(client, pending, changes):
    order, _ = pending
    request = body(order)
    request['changes'] = changes
    response = post(client, request)
    assert response.status_code in (400, 403)
    order.refresh_from_db()
    assert order.state == 'pending_payment'


def test_a_write_must_name_its_order(client, pending):
    order, _ = pending
    request = body(order)
    del request['object_id']
    assert post(client, request).status_code == 404
    request['object_id'] = str(uuid.uuid4())
    assert post(client, request).status_code == 404


# --- Cancel -----------------------------------------------------------------------------

def test_a_financial_manager_cancels_the_order(client, doctor, pending, events, admin):
    from catalogue import resolver
    from catalogue.models import BillingCompany
    from integration.tests.snapshot_rows import pull, snapshot_writes, voucher
    from subscriptions.models import VoucherRedemption
    from subscriptions.tests.helpers import catalogue
    assert pull(admin, 2, catalogue(), vouchers=[voucher(TRY_ME, 'TRYME23456')]) == (
        'applied', 2)
    with snapshot_writes():  # the pull replaced the company: its bank details again
        BillingCompany.objects.filter(code='ES').update(**BANK)
    order = new(doctor, voucher=resolver.voucher_by_code('TRYME23456'))
    order = transfers.service.start_payment(order, 'bank_transfer',
                                            expected_version=order.version,
                                            actor=transfers.service.doctor_actor(doctor))
    assert VoucherRedemption.objects.get(order_id=order.pk).state == 'reserved'
    response = post(client, body(order, 'cancelled'), role='financial_manager')
    assert response.status_code == 200
    assert VoucherRedemption.objects.get(order_id=order.pk).state == 'released'
    assert response.json()['data']['state'] == 'cancelled'
    order.refresh_from_db()
    assert order.state == 'cancelled' and attempt(order).state == 'closed'
    assert [e for e, *_ in events].count('cancelled') == 1
    change = OrderChange.objects.get(order=order, event='cancelled')
    assert (change.actor_type, str(change.actor_uuid)) == ('admin', ME)
    assert not Invoice.objects.filter(order=order).exists()


def test_a_cancelled_order_cannot_be_confirmed_afterwards(client, pending, events):
    order, _ = pending
    listed = order.version
    assert post(client, body(order, 'cancelled')).status_code == 200
    response = post(client, body(order, 'paid', expected=listed))
    assert response.status_code == 409
    order.refresh_from_db()
    assert order.state == 'cancelled'


def test_an_order_no_longer_waiting_for_a_transfer_is_stale(client, pending):
    order, _ = pending
    # Closed without a version change (as by hand): no longer a pending transfer.
    Payment.objects.filter(order=order).update(state=Payment.CLOSED)
    response = post(client, body(order))
    assert response.status_code == 409
    order.refresh_from_db()
    assert order.state == 'pending_payment'


@pytest.mark.parametrize('state', ['paid', 'cancelled'])
def test_an_order_that_is_not_a_pending_transfer_is_refused(client, doctor, pending,
                                                            events, state):
    checkout = new(doctor)  # a doctor's order still in checkout
    switched = Order.objects.get(state=Order.PENDING_PAYMENT, gateway='fake')
    for order in (checkout, switched):
        response = post(client, body(order, state))
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'stale_version'
        before = order.state
        order.refresh_from_db()
        assert order.state == before
    assert not [e for e, *_ in events if e in ('paid', 'cancelled')]
