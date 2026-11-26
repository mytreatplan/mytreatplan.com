"""The cart service (epic-ordering 9.3): one test per row of the plan's matrix and the Always
rules."""
import uuid

import pytest
from django.contrib.auth import get_user_model

from accounts import makers
from cart import service
from cart.models import CartItem
from patients import service as patients_service
from patients.models import WorkOrder
from patients.tests.conftest import BRANDED, ICE, IN_HOUSE

pytestmark = pytest.mark.django_db
CREDENTIALS = {'username': 'dr-user', 'password': 'dr-password'}


def add(doctor, patient, **more):
    values = {'maker': 'invisalign', 'size': 'medium', 'in_house': False,
              'add_on_codes': []} | more
    return service.add_tps(doctor, patient, **values)


def refused(doctor, patient, **more):
    with pytest.raises(service.CartRefused) as caught:
        add(doctor, patient, **more)
    assert not CartItem.objects.exists()
    return caught.value.code


@pytest.fixture
def ready(doctor, entity, patient):
    makers.save(doctor, 'invisalign', CREDENTIALS)
    return patient


def live_order(patient, product_id, stage='pending_assignment'):
    return WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(),
                                    product_id=product_id, stage=stage, actor_type='system')


def test_happy_path_stores_the_item(doctor, ready):
    item = add(doctor, ready, add_on_codes=['junior-2'])
    assert (item.doctor_id, item.patient_id) == (doctor.pk, ready.pk)
    assert str(item.product_id) == BRANDED['medium']
    assert item.add_on_codes == ['JUNIOR-2'] and item.maker == 'invisalign'
    assert service.items_of(doctor) == [item]


@pytest.mark.parametrize('size', ['short', 'medium', 'long'])
@pytest.mark.parametrize('in_house', [False, True])
def test_branded_and_in_house_in_every_size(doctor, ready, size, in_house):
    item = add(doctor, ready, size=size, in_house=in_house)
    assert str(item.product_id) == (IN_HOUSE if in_house else BRANDED)[size]
    assert (item.maker, item.other_name) == (('', '') if in_house else ('invisalign', ''))


def test_one_arch_is_offered_on_in_house_only(doctor, ready):
    assert add(doctor, ready, in_house=True, add_on_codes=['ONE-ARCH']).add_on_codes == ['ONE-ARCH']
    CartItem.objects.all().delete()
    assert refused(doctor, ready, add_on_codes=['ONE-ARCH']) == service.ADD_ON


def test_missing_login_refuses_a_named_maker(doctor, ready):
    assert refused(doctor, ready, maker='smartee') == service.MAKER_LOGIN


def test_not_decided_and_other_never_block(doctor, entity, patient):
    assert add(doctor, patient, maker='undecided').maker == 'undecided'
    second = patients_service.create_patient(doctor, first_name='Eva',
                                             actor=patients_service.Actor('doctor', doctor.uid))
    item = add(doctor, second, maker='other', other_name='  Acme Aligners ')
    assert (item.maker, item.other_name) == ('other', 'Acme Aligners')


@pytest.mark.parametrize('name', ['', '   ', 'x' * 61])
def test_other_needs_a_name_of_at_most_60_characters(doctor, entity, patient, name):
    assert refused(doctor, patient, maker='other', other_name=name) == service.OTHER_NAME


def test_an_unknown_maker_is_refused(doctor, entity, patient):
    assert refused(doctor, patient, maker='nobody') == service.MAKER_UNKNOWN


def test_junior_is_not_offered_on_short(doctor, ready):
    assert refused(doctor, ready, size='short', add_on_codes=['JUNIOR-1']) == service.ADD_ON


def test_unknown_or_repeated_add_ons_are_refused(doctor, ready):
    assert refused(doctor, ready, add_on_codes=['NOPE']) == service.ADD_ON
    assert refused(doctor, ready, add_on_codes=['JUNIOR-1', 'junior-1']) == service.ADD_ON


def test_a_live_non_ice_work_order_refuses(doctor, ready):
    live_order(ready, BRANDED['medium'])
    assert refused(doctor, ready) == service.LIVE_WORK_ORDER


def test_a_live_ice_work_order_allows_a_tps(doctor, ready):
    live = live_order(ready, ICE)
    assert patients_service.blocking_work_order(ready) == live
    assert patients_service.is_live_ice(live)
    assert add(doctor, ready).patient_id == ready.pk


def test_a_live_work_order_whose_product_left_the_catalogue_refuses(doctor, ready):
    live = live_order(ready, uuid.uuid4())
    assert not patients_service.is_live_ice(live)
    assert refused(doctor, ready) == service.LIVE_WORK_ORDER


def test_a_closed_work_order_does_not_block(doctor, ready):
    live_order(ready, BRANDED['medium'], stage='approved')
    assert patients_service.blocking_work_order(ready) is None
    assert add(doctor, ready)


def test_a_size_not_offered_is_refused(doctor, ready):
    assert refused(doctor, ready, size='huge') == service.NOT_OFFERED
    assert refused(doctor, ready, size='') == service.NOT_OFFERED


def test_nothing_is_offered_without_the_products(doctor, ready, admin):
    from integration.tests.snapshot_rows import pull
    from patients.tests.conftest import ice_catalogue
    section = ice_catalogue()
    gone = {*BRANDED.values(), *IN_HOUSE.values()}
    section['products'] = [row for row in section['products'] if row['id'] not in gone]
    for row in section['subscriptions'] + section.get('manual_subscriptions', []):
        row['products'] = [pid for pid in row['products'] if pid not in gone]
    assert pull(admin, 2, section) == ('applied', 2)
    assert refused(doctor, ready) == service.NOT_OFFERED


def test_another_doctors_or_inactive_patient_is_refused(doctor, other_doctor, entity, ready):
    makers.save(other_doctor, 'invisalign', CREDENTIALS)
    assert refused(other_doctor, ready) == service.NO_PATIENT
    ready.status = 'inactive'
    ready.save()
    assert refused(doctor, ready) == service.NO_PATIENT


def test_one_service_per_patient(doctor, ready):
    add(doctor, ready)
    with pytest.raises(service.CartRefused) as caught:
        add(doctor, ready, size='long')
    assert caught.value.code == service.IN_CART and CartItem.objects.count() == 1


def test_no_usable_fiscal_entity_refuses(doctor, patient):
    makers.save(doctor, 'invisalign', CREDENTIALS)
    assert refused(doctor, patient) == service.NO_ENTITY


def test_staff_is_refused(catalogue_loaded, ready):
    staff = get_user_model().objects.create_user('staff@example.com', 'violet-orbit-canyon-42',
                                                 is_staff=True)
    assert refused(staff, ready) == service.NOT_A_DOCTOR


def test_remove_is_the_doctors_own(doctor, other_doctor, ready):
    item = add(doctor, ready)
    assert not service.remove(other_doctor, item.pk)
    assert CartItem.objects.count() == 1
    assert service.remove(doctor, item.pk) and not service.remove(doctor, item.pk)
    assert not CartItem.objects.exists()


def test_ice_with_a_maker_keeps_the_maker(doctor, ready):
    item = add(doctor, ready, size='ice', add_on_codes=[])
    assert str(item.product_id) == ICE and item.maker == 'invisalign'


def test_ice_in_house_has_no_maker(doctor, ready):
    item = add(doctor, ready, size='ice', in_house=True)
    assert str(item.product_id) == ICE and (item.maker, item.other_name) == ('', '')


def test_ice_follows_the_maker_rules(doctor, entity, patient):
    assert refused(doctor, patient, size='ice', maker='smartee') == service.MAKER_LOGIN
    assert refused(doctor, patient, size='ice', maker='other', other_name=' ') == service.OTHER_NAME
    assert add(doctor, patient, size='ice', maker='undecided').maker == 'undecided'


def test_ice_with_other_and_a_name_is_added(doctor, entity, patient):
    item = add(doctor, patient, size='ice', maker='other', other_name='Acme')
    assert (item.maker, item.other_name) == ('other', 'Acme')


@pytest.mark.parametrize('product_id', [ICE, BRANDED['medium']])
def test_ice_is_refused_with_any_live_work_order(doctor, ready, product_id):
    live_order(ready, product_id)
    assert refused(doctor, ready, size='ice') == service.LIVE_WORK_ORDER


def test_ice_not_offered_is_refused(doctor, ready, admin):
    from integration.tests.snapshot_rows import pull
    from patients.tests.conftest import ice_catalogue
    section = ice_catalogue()
    section['products'] = [row for row in section['products'] if row['id'] != ICE]
    for row in section['subscriptions'] + section.get('manual_subscriptions', []):
        row['products'] = [pid for pid in row['products'] if pid != ICE]
    assert pull(admin, 2, section) == ('applied', 2)
    assert service.offered_ice(doctor) is None
    assert refused(doctor, ready, size='ice') == service.NOT_OFFERED


def test_ice_refuses_a_tps_only_add_on(doctor, ready):
    assert refused(doctor, ready, size='ice', add_on_codes=['JUNIOR-1']) == service.ADD_ON
