"""An ICE for a patient (epic-ordering 9.1): the order's refusals, the paid hook, the work
order, the test-order page and the invoice's patient name."""
import re
import uuid

import pytest
from django.test import Client

from catalogue import pricing, resolver
from invoices import patients as invoice_patients, pdf
from invoices.models import Invoice
from patients import service
from patients.models import Patient, WorkOrder
from payments import hooks, service as payments
from payments.models import Order, OrderLine
from patients.tests.conftest import ICE
from integration.tests.snapshot_rows import TPS

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]


@pytest.fixture(autouse=True)
def lookup_registered():
    """The patients app's lookup, as `ready()` registers it (other tests unregister it)."""
    invoice_patients.unregister()
    invoice_patients.register(service.names_for_invoice)
    yield
    invoice_patients.unregister()
    invoice_patients.register(service.names_for_invoice)


def actor(user):
    return payments.doctor_actor(user)


def ice_line(code, **kwargs):
    return resolver.OrderLine(resolver.product(ICE), patient_code=code, **kwargs)


def order_ice(user, code):
    return payments.create_order(user, [ice_line(code)], actor=actor(user))


def pay(order, user):
    order = payments.start_payment(order, 'fake', expected_version=order.version,
                                   actor=actor(user))
    return payments.mark_paid(order, expected_version=order.version,
                              actor=payments.SYSTEM_ACTOR)


def refused(user, lines):
    with pytest.raises(pricing.OrderRefused) as error:
        payments.create_order(user, lines, actor=actor(user))
    assert error.value.reason == pricing.PATIENT_REFUSED
    assert not Order.objects.exists()


# --- Creating the order -------------------------------------------------------------------------

def test_an_ice_for_an_own_patient_keeps_the_code_on_its_line(doctor, entity, fake_on, patient):
    order = order_ice(doctor, patient.code)
    assert order.lines.get().patient_code == patient.code
    assert order.total == 50


def test_an_ice_without_a_patient_is_refused(doctor, entity, fake_on):
    refused(doctor, [ice_line('')])


def test_an_ice_for_another_doctors_patient_is_refused(doctor, other_doctor, entity, fake_on,
                                                       patient):
    refused(other_doctor, [ice_line(patient.code)])


def test_an_ice_for_an_unknown_or_erased_patient_is_refused(doctor, entity, fake_on, patient):
    refused(doctor, [ice_line('EU-ZZZZZZZZ')])
    Patient.objects.filter(pk=patient.pk).update(status='erased')
    refused(doctor, [ice_line(patient.code)])


def test_an_ice_for_a_patient_with_a_live_case_is_refused(doctor, entity, fake_on, patient):
    WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(),
                             stage='pending_assignment', actor_type='system')
    refused(doctor, [ice_line(patient.code)])


def test_a_patient_on_another_product_is_allowed_since_9_6(doctor, entity, fake_on, patient):
    order = payments.create_order(
        doctor, [resolver.OrderLine(resolver.product(TPS), patient_code=patient.code)],
        actor=actor(doctor))
    assert order.lines.get().patient_code == patient.code


def test_an_ice_in_quantity_two_is_still_refused(doctor, entity, fake_on, patient):
    with pytest.raises(pricing.OrderRefused) as error:
        payments.create_order(doctor, [ice_line(patient.code, quantity=2)],
                              actor=actor(doctor))
    assert error.value.reason == pricing.QUANTITY_REFUSED
    assert not Order.objects.exists()


def test_a_patient_named_twice_is_refused(doctor, entity, fake_on, patient):
    refused(doctor, [ice_line(patient.code), ice_line(patient.code)])


def test_the_patient_survives_choosing_the_fiscal_entity_again(doctor, entity, fake_on,
                                                               patient):
    order = order_ice(doctor, patient.code)
    order = payments.choose_entity(order, entity, expected_version=order.version,
                                   actor=actor(doctor))
    assert order.lines.get().patient_code == patient.code


# --- Paying ----------------------------------------------------------------------------------------

def test_a_paid_ice_creates_one_work_order_in_pending_assignment(doctor, entity, fake_on,
                                                                 patient):
    order = pay(order_ice(doctor, patient.code), doctor)
    line = order.lines.get()
    work = WorkOrder.objects.get()
    assert (work.patient, work.order_line_id, work.stage, work.version) == (
        patient, line.pk, 'pending_assignment', 1)
    assert (work.product_id, work.code, work.actor_type) == (line.product_id, 'ICE-1', 'system')


def test_a_replayed_paid_hook_adds_nothing(doctor, entity, fake_on, patient):
    order = pay(order_ice(doctor, patient.code), doctor)
    hooks.fire('paid', order)
    service.create_work_orders_for_order(order)
    assert WorkOrder.objects.count() == 1


def test_a_second_live_case_at_payment_rolls_the_payment_back(doctor, entity, fake_on, patient):
    first = order_ice(doctor, patient.code)
    second = order_ice(doctor, patient.code)  # no live case yet: allowed
    second = payments.start_payment(second, 'fake', expected_version=second.version,
                                    actor=actor(doctor))
    pay(first, doctor)
    with pytest.raises(payments.HookFailed):
        payments.mark_paid(second, expected_version=second.version,
                           actor=payments.SYSTEM_ACTOR)
    second.refresh_from_db()
    assert second.state == 'pending_payment'
    assert WorkOrder.objects.count() == 1 and not Invoice.objects.filter(order=second).exists()


def test_a_line_naming_no_patient_of_the_buyer_cancels_the_order_at_payment(doctor, entity,
                                                                      fake_on, patient):
    order = order_ice(doctor, patient.code)
    OrderLine.objects.filter(order=order).update(patient_code='EU-ZZZZZZZZ')
    with pytest.raises(payments.PatientBusy):
        payments.start_payment(order, 'fake', expected_version=order.version,
                               actor=actor(doctor))
    order.refresh_from_db()
    assert order.state == 'cancelled' and not WorkOrder.objects.exists()


def test_a_paid_order_without_patients_has_no_work_order(doctor, entity, fake_on):
    pay(payments.create_order(doctor, [resolver.OrderLine(resolver.product(TPS))],
                              actor=actor(doctor)), doctor)
    assert not WorkOrder.objects.exists()


# --- The invoice's patient name ---------------------------------------------------------------------

def paid_invoice(doctor, patient):
    order = pay(order_ice(doctor, patient.code), doctor)
    return Invoice.objects.get(order_id=order.pk)


def test_the_invoice_shows_the_name_next_to_the_code(doctor, entity, fake_on, patient):
    html = pdf.render_html(paid_invoice(doctor, patient), doctor)
    assert f'Ana Pérez ({patient.code})' in html


def test_the_invoice_shows_the_reference_when_there_is_no_name(doctor, entity, fake_on):
    patient = service.create_patient(doctor, reference='P-12', actor=service.Actor(
        'doctor', doctor.uid))
    assert f'P-12 ({patient.code})' in pdf.render_html(paid_invoice(doctor, patient), doctor)


def test_an_erased_patient_shows_the_code_only(doctor, entity, fake_on, patient):
    invoice = paid_invoice(doctor, patient)
    Patient.objects.filter(pk=patient.pk).update(status='erased')
    html = pdf.render_html(invoice, doctor)
    assert f'{patient.code}<' in html and 'Ana' not in html


def test_the_lookup_gives_only_the_doctors_own_names(doctor, other_doctor, patient):
    assert service.names_for_invoice(doctor, [patient.code, 'EU-NOBODY00']) == {
        patient.code: 'Ana Pérez'}
    assert service.names_for_invoice(other_doctor, [patient.code]) == {}
    assert invoice_patients.names_for(doctor, [patient.code]) == {patient.code: 'Ana Pérez'}


def test_the_patients_app_registers_the_lookup_and_the_hook_at_start_up():
    """What `PatientsConfig.ready()` leaves behind, without the autouse fixture: it is set
    aside here and `ready()` is run again on clean registries."""
    from django.apps import apps
    invoice_patients.unregister()
    hooks.unregister('paid', 'work_orders')
    for event, name in (('created', 'ice_reserve'), ('paid', 'ice_consume'),
                        ('cancelled', 'ice_release'), ('expired', 'ice_release')):
        hooks.unregister(event, name)
    from storage import purposes
    purposes.unregister('patient_portrait')
    apps.get_app_config('patients').ready()
    assert invoice_patients._lookup is service.names_for_invoice
    assert ('work_orders', service.create_work_orders_for_order) in hooks.receivers('paid')
    from patients import ice
    assert ('ice_reserve', ice.reserve) in hooks.receivers('created')
    assert ('ice_consume', ice.consume) in hooks.receivers('paid')
    assert ('ice_release', ice.release) in hooks.receivers('cancelled')
    assert ('ice_release', ice.release) in hooks.receivers('expired')


# --- The test-order page --------------------------------------------------------------------------------

TEST_ORDER = '/en/checkout/test-order/'


def browser(user):
    client = Client()
    client.force_login(user)
    return client


def test_the_test_order_page_offers_the_doctors_patients_only(doctor, other_doctor, entity,
                                                              fake_on, patient):
    page = browser(doctor).get(TEST_ORDER).content.decode()
    assert 'data-patient-select' in page and patient.code in page
    other = browser(other_doctor).get(TEST_ORDER).content.decode()
    assert patient.code not in other and 'data-patient-select' not in other


def test_a_doctor_orders_and_pays_an_ice_for_a_patient_on_the_test_page(
        doctor, entity, fake_on, patient):
    client = browser(doctor)
    response = client.post(TEST_ORDER, {'product': str(ICE), 'patient': patient.code})
    assert response.status_code == 302 and '/checkout/' in response['Location']
    order = Order.objects.get()
    assert order.lines.get().patient_code == patient.code
    pay(order, doctor)
    assert WorkOrder.objects.get().stage == 'pending_assignment'


def test_the_test_page_refuses_another_doctors_patient_with_a_message(
        doctor, other_doctor, entity, fake_on, patient):
    response = browser(other_doctor).post(
        TEST_ORDER, {'product': str(ICE), 'patient': patient.code}, follow=True)
    assert 'Choose one of your patients' in response.content.decode()
    assert not Order.objects.exists()
