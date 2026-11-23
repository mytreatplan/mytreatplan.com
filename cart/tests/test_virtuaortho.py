"""The case wizard's VirtuaOrtho branch (epic-ordering 9.4): one test per row of the plan's
matrix, on the service and on the pages."""
import uuid
from decimal import Decimal

import pytest
from django.test import Client

from accounts import shipping
from cart import service
from cart.models import CartItem
from patients import service as patients_service
from patients.models import WorkOrder
from patients.tests.conftest import (ICE, ODB_ONE, ODB_TWO, RETAINERS_3, SPARK_ONE, SPARK_TWO,
                                     VO_ONE, VO_TWIN)
from subscriptions.tests import helpers

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]
ADDRESS = {'label': 'Clínica Madrid', 'recipient_name': 'Rosa Vidal',
           'clinic_name': 'Clínica Vidal', 'contact_phone': '612 34 56 78',
           'address_line1': 'Calle Mayor 1', 'city': 'Madrid', 'postal_code': '28001',
           'province': 'Madrid', 'country': 'ES'}


@pytest.fixture
def ready(advanced, entity, patient):
    advanced.address = shipping.create(advanced, ADDRESS)
    return patient


def add(doctor, patient, **more):
    values = {'variant': 'digitaltwin', 'choice': 'case', 'size': 'medium',
              'add_on_codes': [], 'quantity': 1,
              'address_id': str(doctor.address.pk)} | more
    return service.add_virtuaortho(doctor, patient, **values)


def refused(doctor, patient, **more):
    with pytest.raises(service.CartRefused) as caught:
        add(doctor, patient, **more)
    assert not CartItem.objects.exists()
    return caught.value.code


def live_order(patient, product_id, stage='pending_assignment'):
    return WorkOrder.objects.create(patient=patient, order_line_id=uuid.uuid4(),
                                    product_id=product_id, stage=stage, actor_type='system')


def test_happy_path_digitaltwin_medium_with_byte_sync(advanced, ready):
    item = add(advanced, ready, add_on_codes=['byte-sync'])
    assert str(item.product_id) == VO_TWIN['medium'] and item.add_on_codes == ['BYTE-SYNC']
    assert (item.maker, item.quantity, item.shipping_address_id) == (
        '', 1, advanced.address.pk)


@pytest.mark.parametrize('size', ['short', 'medium', 'long'])
@pytest.mark.parametrize('variant, ids', [('one', VO_ONE), ('digitaltwin', VO_TWIN)])
def test_one_and_digitaltwin_in_every_size(advanced, ready, variant, ids, size):
    assert str(add(advanced, ready, variant=variant, size=size).product_id) == ids[size]


def test_junior_on_medium_one_and_digitaltwin_but_not_short_or_long(advanced, ready):
    assert add(advanced, ready, variant='one', add_on_codes=['JUNIOR-1']).add_on_codes == [
        'JUNIOR-1']
    CartItem.objects.all().delete()
    for size in ('short', 'long'):
        assert refused(advanced, ready, size=size, add_on_codes=['JUNIOR-1']) == service.ADD_ON


def test_odb_under_digitaltwin_gets_odb_dt_by_itself(advanced, ready):
    item = add(advanced, ready, choice='odb', size=ODB_TWO)
    assert str(item.product_id) == ODB_TWO and item.add_on_codes == ['ODB-DT']


def test_odb_under_digitaltwin_does_not_take_it_twice(advanced, ready):
    assert add(advanced, ready, choice='odb', size=ODB_ONE,
               add_on_codes=['ODB-DT']).add_on_codes == ['ODB-DT']


def test_odb_under_one_has_no_odb_dt(advanced, ready):
    item = add(advanced, ready, variant='one', choice='odb', size=ODB_ONE)
    assert item.add_on_codes == []
    CartItem.objects.all().delete()
    assert refused(advanced, ready, variant='one', choice='odb', size=ODB_ONE,
                   add_on_codes=['ODB-DT']) == service.ADD_ON


def test_odb_with_byte_sync_is_refused(advanced, ready):
    assert refused(advanced, ready, choice='odb', size=ODB_TWO,
                   add_on_codes=['BYTE-SYNC']) == service.ADD_ON
    assert refused(advanced, ready, variant='one', choice='odb', size=ODB_TWO,
                   add_on_codes=['BYTE-SYNC']) == service.ADD_ON


def test_spark_prices_the_aligners(advanced, ready):
    from catalogue import resolver
    from subscriptions import service as subscriptions
    item = add(advanced, ready, variant='one', choice='spark', size=SPARK_TWO, quantity='6',
               add_on_codes=['BYTE-SYNC'])
    assert (str(item.product_id), item.quantity) == (SPARK_TWO, 6)
    held = subscriptions.effective(advanced).subscription
    priced = resolver.price(resolver.product(SPARK_TWO), held,
                            [resolver.add_on('BYTE-SYNC')], 6)
    assert priced.amount == Decimal('45.00') * 6 + Decimal('120.00')


@pytest.mark.parametrize('quantity', [3, 10, 0, -1, 'six', '', None])
def test_spark_outside_its_range_is_refused(advanced, ready, quantity):
    assert refused(advanced, ready, variant='one', choice='spark', size=SPARK_ONE,
                   quantity=quantity) == service.QUANTITY


def test_a_quantity_on_a_product_not_per_unit_is_refused(advanced, ready):
    assert refused(advanced, ready, quantity=5) == service.QUANTITY


def test_spark_under_digitaltwin_is_refused(advanced, ready):
    assert refused(advanced, ready, choice='spark', size=SPARK_ONE,
                   quantity=6) == service.NOT_OFFERED


def test_no_usable_address_is_refused(advanced, entity, patient):
    with pytest.raises(service.CartRefused) as caught:
        service.add_virtuaortho(advanced, patient, variant='digitaltwin', choice='case',
                                size='medium', add_on_codes=[], quantity=1, address_id='')
    assert caught.value.code == service.NO_ADDRESS and not CartItem.objects.exists()


def test_another_doctors_archived_or_unknown_address_is_refused(advanced, ready):
    helpers.doctor('carlos2@example.com')
    other = helpers.doctor('carlos3@example.com')
    theirs = shipping.create(other, ADDRESS)
    assert refused(advanced, ready, address_id=str(theirs.pk)) == service.ADDRESS
    assert refused(advanced, ready, address_id=str(uuid.uuid4())) == service.ADDRESS
    assert refused(advanced, ready, address_id='not-an-id') == service.ADDRESS
    assert refused(advanced, ready, address_id='') == service.ADDRESS
    second = shipping.create(advanced, ADDRESS | {'label': 'Otra'})
    shipping.mark_used(second)
    shipping.remove(advanced, second.pk)
    assert shipping.usable_addresses(advanced).count() == 1
    assert refused(advanced, ready, address_id=str(second.pk)) == service.ADDRESS


def test_not_offered_to_a_basic_or_a_rest_of_eu_doctor(advanced, other_doctor):
    # `advanced` loaded the VirtuaOrtho catalogue; the Basic doctor has none of it.
    assert service.offered_virtuaortho(other_doctor) == []
    germany = helpers.doctor('hans@example.com', country='DE')
    assert service.offered_virtuaortho(germany) == []
    assert service.variants_offered(service.offered_virtuaortho(advanced)) == [
        'one', 'digitaltwin']


def test_a_basic_doctor_with_everything_else_is_refused_as_not_offered(advanced, ready,
                                                                      other_doctor):
    from accounts import fiscal
    from patients.tests.conftest import service as patients
    fiscal.create(other_doctor, {'kind': 'company', 'legal_name': 'Otra S.L.',
                                 'tax_id': 'B12345674', 'country': 'ES',
                                 'address_line1': 'Calle 1', 'city': 'Madrid',
                                 'postal_code': '28001'})
    mine = patients.create_patient(other_doctor, first_name='Eva',
                                   actor=patients.Actor('doctor', other_doctor.uid))
    address = shipping.create(other_doctor, ADDRESS)
    other_doctor.address = address
    assert refused(other_doctor, mine, variant='one') == service.NOT_OFFERED


def test_retainers_need_a_finished_treatment(advanced, ready):
    assert refused(advanced, ready, choice='retainers') == service.NOT_FINISHED
    live_order(ready, VO_TWIN['medium'], stage='cancelled')
    assert refused(advanced, ready, choice='retainers') == service.NOT_FINISHED
    # An approved ICE or TPS is not a finished VirtuaOrtho treatment.
    live_order_ice = WorkOrder.objects.create(
        patient=ready, order_line_id=uuid.uuid4(), product_id=ICE, stage='approved',
        actor_type='system')
    assert refused(advanced, ready, choice='retainers') == service.NOT_FINISHED
    live_order_ice.delete()


@pytest.mark.parametrize('product_id', [VO_TWIN['short'], VO_ONE['long'], SPARK_ONE])
def test_retainers_after_an_approved_one_digitaltwin_or_spark(advanced, ready, product_id):
    live_order(ready, product_id, stage='approved')
    item = add(advanced, ready, choice='retainers')
    assert str(item.product_id) == RETAINERS_3 and item.quantity == 1


def test_retainers_are_not_listed_until_finished(advanced, ready):
    client = Client()
    client.force_login(advanced)
    url = f'/en/start-a-case/{ready.code}/'
    assert 'value="one:retainers"' not in client.get(url).content.decode()
    live_order(ready, VO_ONE['short'], stage='approved')
    assert 'value="one:retainers"' in client.get(url).content.decode()


def test_a_live_ice_work_order_may_take_a_virtuaortho_item(advanced, ready):
    live_order(ready, ICE)
    assert str(add(advanced, ready).product_id) == VO_TWIN['medium']


def test_any_other_live_work_order_refuses(advanced, ready):
    live_order(ready, VO_ONE['short'])
    assert refused(advanced, ready) == service.LIVE_WORK_ORDER


def test_one_item_per_patient_and_an_unknown_choice(advanced, ready):
    add(advanced, ready)
    with pytest.raises(service.CartRefused) as caught:
        add(advanced, ready, size='short')
    assert caught.value.code == service.IN_CART
    CartItem.objects.all().delete()
    assert refused(advanced, ready, choice='nothing') == service.NOT_OFFERED
    assert refused(advanced, ready, variant='both') == service.NOT_OFFERED
    assert refused(advanced, ready, size='xl') == service.NOT_OFFERED
    assert refused(advanced, ready, choice='odb', size='unknown') == service.NOT_OFFERED


def test_a_tps_service_is_unchanged(advanced, ready):
    from accounts import makers
    makers.save(advanced, 'invisalign', {'username': 'u', 'password': 'p'})
    item = service.add_tps(advanced, ready, maker='invisalign', size='medium')
    assert (item.quantity, item.shipping_address_id) == (1, None)


def reload_catalogue(admin, change):
    from patients.tests.conftest import virtuaortho_catalogue
    from integration.tests.snapshot_rows import pull
    section = virtuaortho_catalogue()
    change(section)
    assert pull(admin, 3, section) == ('applied', 3)


def test_an_empty_size_does_not_pick_an_unsized_product(advanced, ready, admin):
    reload_catalogue(admin, lambda section: [
        row.pop('size') for row in section['products'] if row['id'] == VO_TWIN['short']])
    assert refused(advanced, ready, size='') == service.NOT_OFFERED
    assert refused(advanced, ready, size='short') == service.NOT_OFFERED


# --- The pages -------------------------------------------------------------------------------

def browser(user):
    client = Client()
    client.force_login(user)
    return client


def step2(patient, language='en'):
    return f'/{language}/start-a-case/{patient.code}/'


def post(user, patient, **data):
    return browser(user).post(step2(patient), {'line': 'virtuaortho', **data})


@pytest.fixture
def basic(advanced, other_doctor):
    """A Basic doctor of the same catalogue, ready to order but not offered VirtuaOrtho."""
    from accounts import fiscal
    fiscal.create(other_doctor, {'kind': 'company', 'legal_name': 'Otra S.L.',
                                 'tax_id': 'B12345674', 'country': 'ES',
                                 'address_line1': 'Calle 1', 'city': 'Madrid',
                                 'postal_code': '28001'})
    other_doctor.address = shipping.create(other_doctor, ADDRESS)
    other_doctor.patient = patients_service.create_patient(
        other_doctor, first_name='Eva',
        actor=patients_service.Actor('doctor', other_doctor.uid))
    return other_doctor


def test_the_branch_is_hidden_when_not_offered(basic):
    doctor, patient = basic, basic.patient
    response = browser(doctor).get(step2(patient))
    assert response.status_code == 200 and 'data-case-form' in response.content.decode()
    assert 'data-virtuaortho-form' not in response.content.decode()


def test_a_tampered_post_without_the_offer_is_refused(basic):
    doctor, patient = basic, basic.patient
    response = post(doctor, patient, product='one:case:medium',
                    address_id=str(doctor.address.pk))
    assert not CartItem.objects.exists()
    assert response.status_code == 200 and 'data-error' in response.content.decode()
    assert 'data-virtuaortho-form' not in response.content.decode()


def test_step_two_lists_the_products_with_prices_and_the_addresses(advanced, ready):
    html = browser(advanced).get(step2(ready)).content.decode()
    assert 'data-virtuaortho-form' in html and 'data-shipping-block' not in html
    for text in ('VirtuaOrtho One', 'VirtuaOrtho DigitalTwin', 'value="one:case:short"',
                 f'value="digitaltwin:odb:{ODB_TWO}"', f'value="one:spark:{SPARK_ONE}"', 'per aligner',
                 'Clínica Madrid · Madrid'):
        assert text in html
    assert '1,200.00' in html or '1200' in html
    # ODB under DigitalTwin shows the price with ODB DigitalTwin (559 + 320).
    assert '879' in html


def test_without_an_address_a_block_links_to_add_one_and_back(advanced, entity, patient):
    from urllib.parse import quote
    html = browser(advanced).get(step2(patient)).content.decode()
    assert 'data-shipping-block' in html
    link = f'/en/settings/shipping/add/?next={quote(step2(patient), safe="")}'
    assert f'href="{link}"' in html
    saved = browser(advanced).post(link, {**ADDRESS, 'next': step2(patient)})
    assert saved.status_code == 302 and saved['Location'] == step2(patient)
    response = post(advanced, patient, product='one:case:short', address_id='')
    assert 'data-error' in response.content.decode()


def test_a_post_without_javascript_adds_the_item_and_the_cart_shows_it(advanced, ready):
    response = post(advanced, ready, product='digitaltwin:case:medium',
                    add_on='BYTE-SYNC', address_id=str(advanced.address.pk))
    assert response.status_code == 302 and response['Location'] == '/en/cart/'
    html = browser(advanced).get('/en/cart/').content.decode()
    assert 'data-item-address' in html and 'Clínica Madrid' in html
    assert 'data-item-maker' not in html and 'VirtuaOrtho DigitalTwin' in html
    assert 'Byte Sync' in html and 'data-item-price' in html
    assert '1,320.00 EUR' in html   # 1200 + 120


def test_spark_in_the_page_shows_the_aligners_and_the_total(advanced, ready):
    response = post(advanced, ready, product=f'one:spark:{SPARK_ONE}',
                    **{f'quantity_{SPARK_ONE}': '5'}, address_id=str(advanced.address.pk))
    assert response.status_code == 302
    html = browser(advanced).get('/en/cart/').content.decode()
    assert 'data-item-aligners' in html and '5 aligners' in html and '250' in html


def test_a_refused_post_shows_the_message_and_adds_nothing(advanced, ready):
    response = post(advanced, ready, product=f'one:spark:{SPARK_ONE}',
                    **{f'quantity_{SPARK_ONE}': '10'}, address_id=str(advanced.address.pk))
    assert response.status_code == 200 and not CartItem.objects.exists()
    assert 'data-error' in response.content.decode()


def test_step_two_is_in_spanish_with_the_virtuaortho_branch(advanced, ready):
    html = browser(advanced).get(step2(ready, 'es')).content.decode()
    assert 'Dirección de envío' in html and 'Añadir al carrito' in html
    assert 'Shipping address' not in html


def test_the_cart_in_spanish_shows_the_address(advanced, ready):
    post(advanced, ready, product=f'one:spark:{SPARK_TWO}',
         **{f'quantity_{SPARK_TWO}': '4'}, address_id=str(advanced.address.pk))
    html = browser(advanced).get('/es/cart/').content.decode()
    assert 'Enviar a' in html and '4 alineadores' in html


def test_a_patient_of_another_doctor_is_a_404(advanced, ready, other_doctor):
    assert browser(other_doctor).get(step2(ready)).status_code == 404
    assert patients_service.patient_of(other_doctor, ready.code) is None


def test_each_variants_rows_have_their_own_heading_and_values(advanced, ready):
    html = browser(advanced).get(step2(ready)).content.decode()
    assert html.count('data-vo-group') == 2
    for value in ('one:case:medium', 'digitaltwin:case:medium', f'one:spark:{SPARK_ONE}',
                  f'digitaltwin:odb:{ODB_TWO}', f'one:odb:{ODB_TWO}'):
        assert f'value="{value}"' in html
    assert 'name="variant"' not in html and f'digitaltwin:spark:{SPARK_ONE}' not in html


def test_a_post_of_a_digitaltwin_row_without_javascript_adds_the_digitaltwin_product(
        advanced, ready):
    post(advanced, ready, product='digitaltwin:case:medium',
         address_id=str(advanced.address.pk))
    assert str(CartItem.objects.get().product_id) == VO_TWIN['medium']
    CartItem.objects.all().delete()
    post(advanced, ready, product='one:case:medium', address_id=str(advanced.address.pk))
    assert str(CartItem.objects.get().product_id) == VO_ONE['medium']


def test_a_refused_virtuaortho_post_ticks_the_add_ons_in_its_form_only(advanced, ready):
    response = post(advanced, ready, product=f'one:spark:{SPARK_ONE}', add_on='BYTE-SYNC',
                    **{f'quantity_{SPARK_ONE}': '10'}, address_id=str(advanced.address.pk))
    lines = [line for line in response.content.decode().splitlines()
             if 'data-add-on="BYTE-SYNC"' in line]
    assert len(lines) == 2 and ' checked' not in lines[0] and ' checked' in lines[1]


def test_a_refused_tps_post_does_not_tick_the_virtuaortho_add_ons(advanced, ready):
    response = browser(advanced).post(step2(ready), {
        'kind': 'maker', 'maker': 'invisalign', 'size': 'medium', 'add_on': 'BYTE-SYNC'})
    lines = [line for line in response.content.decode().splitlines()
             if 'data-add-on="BYTE-SYNC"' in line]
    assert len(lines) == 2 and ' checked' in lines[0] and ' checked' not in lines[1]


def test_the_digitaltwin_odb_row_is_dropped_without_its_odb_dt(advanced, ready, admin):
    reload_catalogue(admin, lambda section: [
        row.update(active=False) for row in section['add_ons'] if row['code'] == 'ODB-DT'])
    html = browser(advanced).get(step2(ready)).content.decode()
    assert f'value="digitaltwin:odb:{ODB_TWO}"' not in html
    assert f'value="one:odb:{ODB_TWO}"' in html
    assert refused(advanced, ready, choice='odb', size=ODB_TWO) == service.ADD_ON
