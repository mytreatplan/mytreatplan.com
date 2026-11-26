"""The order in which the cart's add functions refuse when several checks apply at once
(epic-ordering 9.11): the doctor, the patient, the fiscal entity, (for `add_tps`) the maker, a
blocking work order, then the patient already in the cart. The codes are those of the code
before the pre-checks were shared."""
import uuid

import pytest

from accounts import makers
from cart import service
from cart.models import CartItem
from cart.tests.test_checkout import ready as checkout_ready  # noqa: F401
from patients.models import WorkOrder
from patients.tests.conftest import (BRANDED, ICE, manual_work_order)

pytestmark = pytest.mark.django_db
CREDENTIALS = {'username': 'dr-user', 'password': 'dr-password'}


def block(patient, product_id=BRANDED['medium']):
    return WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(),
                                    product_id=product_id, stage='pending_assignment',
                                    actor_type='system')


def code_of(call, *args, **kwargs):
    with pytest.raises(service.CartRefused) as caught:
        call(*args, **kwargs)
    return caught.value.code


def tps(doctor, patient, **more):
    return service.add_tps(doctor, patient, **({'maker': 'undecided', 'size': 'medium'} | more))


def vo(doctor, patient, **more):
    values = {'variant': 'digitaltwin', 'choice': 'case', 'size': 'medium',
              'address_id': str(doctor.address.pk) if hasattr(doctor, 'address') else ''}
    return service.add_virtuaortho(doctor, patient, **(values | more))


# --- add_tps ------------------------------------------------------------------------------------

def test_tps_a_foreign_patient_is_refused_before_the_missing_entity(other_doctor, patient):
    assert code_of(tps, other_doctor, patient) == service.NO_PATIENT


def test_tps_the_missing_entity_comes_before_the_maker_and_the_block(doctor, patient):
    block(patient)
    assert code_of(tps, doctor, patient, maker='nope') == service.NO_ENTITY


def test_tps_the_maker_comes_before_the_blocking_work_order(doctor, entity, patient):
    block(patient)
    assert code_of(tps, doctor, patient, maker='nope') == service.MAKER_UNKNOWN
    makers.save(doctor, 'invisalign', CREDENTIALS)
    assert code_of(tps, doctor, patient, maker='smartee') == service.MAKER_LOGIN


def test_tps_a_blocked_patient_already_in_the_cart_is_blocked(doctor, entity, patient):
    tps(doctor, patient)
    block(patient)
    assert code_of(tps, doctor, patient) == service.LIVE_WORK_ORDER


def test_tps_a_live_ice_lets_a_tps_through_to_the_cart_check_but_not_an_ice(
        doctor, entity, patient):
    tps(doctor, patient)
    block(patient, ICE)
    assert code_of(tps, doctor, patient) == service.IN_CART
    assert code_of(tps, doctor, patient, size='ice') == service.LIVE_WORK_ORDER
    assert CartItem.objects.count() == 1


# --- add_virtuaortho ----------------------------------------------------------------------------

def test_vo_a_foreign_patient_is_refused_before_the_missing_entity(
        advanced, other_doctor, patient):
    assert code_of(vo, other_doctor, patient) == service.NO_PATIENT


def test_vo_the_missing_entity_comes_before_the_block(advanced, patient):
    block(patient)
    assert code_of(vo, advanced, patient) == service.NO_ENTITY


def test_vo_a_block_comes_before_the_product_and_the_cart(advanced, entity, patient):
    tps(advanced, patient)
    block(patient)
    assert code_of(vo, advanced, patient, variant='nope') == service.LIVE_WORK_ORDER


def test_vo_a_live_ice_lets_it_through_to_the_cart_check(advanced, entity, patient):
    tps(advanced, patient)
    block(patient, ICE)
    assert code_of(vo, advanced, patient) == service.IN_CART


# --- check_work_order ---------------------------------------------------------------------------

def test_work_order_another_doctors_is_refused_before_everything_else(
        checkout_ready, patient, other_doctor):
    row = manual_work_order(checkout_ready, patient)
    assert code_of(service.check_work_order, other_doctor, row) == service.WORK_ORDER


def test_work_order_the_missing_entity_comes_before_the_block(advanced, patient):
    row = manual_work_order(advanced, patient)
    block(patient)
    assert code_of(service.check_work_order, advanced, row) == service.NO_ENTITY


def test_work_order_a_block_comes_before_the_cart(checkout_ready, patient):
    row = manual_work_order(checkout_ready, patient)
    tps(checkout_ready, patient)
    block(patient)
    assert code_of(service.check_work_order, checkout_ready, row) == service.LIVE_WORK_ORDER


def test_work_order_a_live_ice_lets_a_tps_work_order_through_to_the_cart_check(
        checkout_ready, patient):
    row = manual_work_order(checkout_ready, patient)
    tps(checkout_ready, patient)
    block(patient, ICE)
    assert code_of(service.check_work_order, checkout_ready, row) == service.IN_CART
    ice_row = manual_work_order(checkout_ready, patient, ICE)
    assert code_of(service.check_work_order, checkout_ready, ice_row) == service.LIVE_WORK_ORDER
