"""The doctor's checkout pages (epic-payments entry 1): the test order, the fake gateway,
the return and failure pages, ownership and the flags. The admin site is the fake of
integration/tests/snapshot_rows.py (no network)."""
import re

import pytest
from django.core.checks import Error
from django.test import Client

from accounts import fiscal
from accounts.models import FiscalEntity
from payments import checks, hooks, service
from payments.models import Order
from payments.tests.conftest import COMPANY
from payments.tests.test_service import line
from subscriptions.tests.helpers import doctor as make_doctor

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]
TEST_ORDER = '/en/checkout/test-order/'


def browser(user):
    client = Client()
    client.force_login(user)
    return client


def html(response):
    assert response.status_code == 200
    return response.content.decode()


def start(client):
    """The test order of the first product offered; the checkout's address."""
    page = html(client.get(TEST_ORDER))
    assert 'data-test-order-form' in page
    product = re.search(r'name="product" value="([0-9a-f-]+)"', page).group(1)
    response = client.post(TEST_ORDER, {'product': product})
    assert response.status_code == 302
    return response['Location']


def pay(client, checkout, entity, order):
    return client.post(checkout, {'action': 'pay', 'entity': str(entity.pk),
                                  'gateway': 'fake', 'version': order.version,
                                  'total': str(order.total)})


def test_a_doctor_pays_a_test_order_through_the_fake_page(doctor, entity, fake_on, events):
    client = browser(doctor)
    checkout = start(client)
    order = Order.objects.get()
    page = html(client.get(checkout))
    assert order.reference in page and 'data-entity-select' in page
    assert 'value="fake"' in page and '100.00 EUR' in page
    response = pay(client, checkout, entity, order)
    assert response.status_code == 302 and response['Location'].endswith('/fake/')
    order.refresh_from_db()
    assert order.state == 'pending_payment'
    assert 'data-fake-pay' in html(client.get(response['Location']))
    done = client.post(response['Location'], {'action': 'pay', 'version': order.version})
    assert done['Location'].endswith('/return/')
    order.refresh_from_db()
    assert order.state == 'paid'
    assert 'data-order-state="paid"' in html(client.get(done['Location']))
    assert FiscalEntity.objects.get(pk=entity.pk).used_at is not None
    assert [e for e, *_ in events] == ['created', 'paid']


def test_failing_the_fake_page_keeps_the_order_pending_and_allows_a_retry(
        doctor, entity, fake_on, events):
    client = browser(doctor)
    checkout = start(client)
    order = Order.objects.get()
    fake = pay(client, checkout, entity, order)['Location']
    failed = client.post(fake, {'action': 'fail', 'version': 2})
    assert failed['Location'].endswith('/failed/')
    order.refresh_from_db()
    assert order.state == 'pending_payment'
    assert 'data-retry' in html(client.get(failed['Location']))
    retry = client.post(failed['Location'], {'action': 'retry', 'gateway': 'fake',
                                             'version': order.version})
    assert retry['Location'] == fake
    order.refresh_from_db()
    assert (order.state, order.version) == ('pending_payment', 3)
    assert [e for e, *_ in events] == ['created']


def test_a_replayed_pay_on_the_fake_page_pays_once(doctor, entity, fake_on, events):
    client = browser(doctor)
    checkout = start(client)
    order = Order.objects.get()
    fake = pay(client, checkout, entity, order)['Location']
    client.post(fake, {'action': 'pay', 'version': 2})
    again = client.post(fake, {'action': 'pay', 'version': 2})
    assert again['Location'].endswith('/return/')
    assert Order.objects.get().version == 3
    assert [e for e, *_ in events].count('paid') == 1
    assert client.get(checkout)['Location'].endswith('/return/')  # shown as paid


def test_another_doctors_order_is_a_404_on_every_page(doctor, entity, fake_on):
    checkout = start(browser(doctor))
    other = browser(make_doctor('other@example.com'))
    for suffix in ('', 'return/', 'failed/', 'fake/'):
        assert other.get(checkout + suffix).status_code == 404
    assert other.post(checkout, {'action': 'cancel', 'version': 1}).status_code == 404
    assert Order.objects.get().state == 'checkout'
    assert Client().get(checkout).status_code == 302  # login required


def test_a_staff_account_gets_a_404(doctor, entity, fake_on, django_user_model):
    checkout = start(browser(doctor))
    staff = django_user_model.objects.create_user('staff@example.com', 'x', is_staff=True)
    assert browser(staff).get(checkout).status_code == 404
    assert browser(staff).get(TEST_ORDER).status_code == 404


def test_without_a_fiscal_entity_checkout_links_to_add_one_and_back(doctor, fake_on):
    client = browser(doctor)
    checkout = start(client)
    page = html(client.get(checkout))
    assert 'data-no-entity' in page
    assert f'/en/settings/fiscal/add/?next={checkout.replace("/", "%2F")}' in page
    assert 'data-pay' not in page
    again = client.post(checkout, {'action': 'pay', 'gateway': 'fake', 'version': 1,
                                   'entity': ''}, follow=True)
    assert Order.objects.get().state == 'checkout'
    assert 'fiscal entity' in html(again).lower()


def test_choosing_an_entity_that_changes_the_price_stops_to_show_it(catalogue_loaded, fake_on):
    german = make_doctor('hans@example.com', country='DE')
    first = fiscal.create(german, {**COMPANY, 'country': 'DE', 'tax_id': 'DE136695976'})
    FiscalEntity.objects.filter(pk=first.pk).update(vies_status='invalid')
    second = fiscal.create(german, {**COMPANY, 'country': 'DE', 'tax_id': 'DE136695976',
                                    'legal_name': 'Zweite GmbH'})
    order = service.create_order(german, [line()], actor=service.doctor_actor(german),
                                 entity=FiscalEntity.objects.get(pk=first.pk))
    checkout = f'/en/checkout/{order.pk}/'
    assert str(order.total) == '123.00'
    response = browser(german).post(checkout, {
        'action': 'pay', 'entity': str(second.pk), 'gateway': 'fake', 'version': 1,
        'total': str(order.total)})
    assert response['Location'] == checkout
    order.refresh_from_db()
    assert (order.state, order.version, str(order.total)) == ('checkout', 2, '100.00')


def test_cancel_from_the_checkout(doctor, entity, fake_on, events):
    client = browser(doctor)
    checkout = start(client)
    client.post(checkout, {'action': 'cancel', 'version': 1})
    assert Order.objects.get().state == 'cancelled'
    assert 'data-order-state="cancelled"' in html(client.get(checkout))
    assert [e for e, *_ in events] == ['created', 'cancelled']


def test_a_stale_page_is_refused_with_a_message(doctor, entity, fake_on):
    client = browser(doctor)
    checkout = start(client)
    response = client.post(checkout, {'action': 'cancel', 'version': 7}, follow=True)
    assert 'changed in the meantime' in html(response)
    assert Order.objects.get().state == 'checkout'


def test_a_gateway_outside_the_offered_ones_is_refused_with_a_message(
        doctor, entity, fake_on):
    client = browser(doctor)
    checkout = start(client)
    order = Order.objects.get()
    response = client.post(checkout, {'action': 'pay', 'entity': str(entity.pk),
                                      'gateway': 'revolut', 'version': 1,
                                      'total': str(order.total)}, follow=True)
    assert 'payment methods offered' in html(response)
    assert Order.objects.get().state == 'checkout'


def test_the_test_order_page_is_a_404_when_its_flag_is_off(doctor, entity, fake_on):
    fake_on.PAYMENTS_TEST_ORDERS = False
    client = browser(doctor)
    assert client.get(TEST_ORDER).status_code == 404
    assert client.post(TEST_ORDER, {}).status_code == 404


def test_the_fake_page_is_a_404_when_its_flag_is_off(doctor, entity, fake_on):
    client = browser(doctor)
    checkout = start(client)
    fake = pay(client, checkout, entity, Order.objects.get())['Location']
    fake_on.PAYMENTS_FAKE_GATEWAY = False
    assert client.get(fake).status_code == 404


def test_a_paid_hook_failure_on_the_fake_page_leaves_the_order_pending(
        doctor, entity, fake_on):
    client = browser(doctor)
    checkout = start(client)
    fake = pay(client, checkout, entity, Order.objects.get())['Location']

    def boom(order):
        raise RuntimeError('x')

    hooks.register('paid', 'test_boom', boom)
    try:
        response = client.post(fake, {'action': 'pay', 'version': 2}, follow=True)
    finally:
        hooks.unregister('paid', 'test_boom')
    assert 'Something went wrong' in html(response)
    assert Order.objects.get().state == 'pending_payment'


# --- The startup checks ------------------------------------------------------------------------

@pytest.mark.parametrize('name, check_id', [('PAYMENTS_FAKE_GATEWAY', 'payments.E001'),
                                            ('PAYMENTS_TEST_ORDERS', 'payments.E002')])
def test_a_public_site_refuses_the_fake_flags(settings, name, check_id):
    settings.DEBUG, settings.ROBOTS_NOINDEX = False, False
    setattr(settings, name, True)
    errors = checks.check_payment_flags()
    assert [error.id for error in errors] == [check_id]
    assert all(isinstance(error, Error) for error in errors)


def test_staging_and_development_may_use_the_flags(settings):
    settings.PAYMENTS_FAKE_GATEWAY = settings.PAYMENTS_TEST_ORDERS = True
    settings.DEBUG, settings.ROBOTS_NOINDEX = False, True
    assert checks.check_payment_flags() == []
    settings.DEBUG, settings.ROBOTS_NOINDEX = True, False
    assert checks.check_payment_flags() == []


def test_the_flags_are_off_by_default():
    from django.conf import settings as real
    assert real.PAYMENTS_FAKE_GATEWAY is False and real.PAYMENTS_TEST_ORDERS is False


def test_production_settings_with_the_fake_gateway_fail_the_system_check(settings):
    from django.core.management import call_command
    from django.core.management.base import SystemCheckError
    settings.DEBUG, settings.ROBOTS_NOINDEX = False, False
    settings.PAYMENTS_FAKE_GATEWAY = True
    with pytest.raises(SystemCheckError, match='payments.E001'):
        call_command('check')


def test_the_checkout_is_in_spanish_under_es(doctor, entity, fake_on):
    client = browser(doctor)
    checkout = start(client).replace('/en/', '/es/')
    page = html(client.get(checkout))
    assert 'Facturar a' in page and 'Método de pago' in page and 'Cancelar el pedido' in page
    assert 'Bill to' not in page


def test_the_expire_orders_job_is_scheduled_every_15_minutes():
    from mytp_core import jobs
    job = jobs.load_jobs(jobs.release_jobs_file())['expireorders']
    assert (job.command, job.crontab) == ('expire_orders', '*/15 * * * *')


def test_a_failing_re_choice_after_an_entity_change_is_a_message_not_a_500(
        doctor, entity, fake_on):
    client = browser(doctor)
    checkout = start(client)
    order = Order.objects.get()
    fiscal.update(doctor, entity.pk, {'city': 'Getafe'}, version=entity.version)
    from payments.models import OrderLine
    OrderLine.objects.filter(order=order).update(product_id='33333333-3333-4333-8333-333333333333')
    response = client.post(checkout, {'action': 'pay', 'entity': str(entity.pk),
                                      'gateway': 'fake', 'version': 1,
                                      'total': str(order.total)}, follow=True)
    assert response.status_code == 200
    assert Order.objects.get().state == 'checkout'


def test_the_failure_page_does_not_pay_an_order_still_in_checkout(doctor, entity, fake_on):
    client = browser(doctor)
    checkout = start(client)
    order = Order.objects.get()
    response = client.post(checkout.replace('/checkout/', '/checkout/') + 'failed/',
                           {'action': 'retry', 'gateway': 'fake', 'version': 1})
    assert response['Location'] == checkout
    assert Order.objects.get(pk=order.pk).state == 'checkout'


def test_a_flag_on_a_public_site_refuses_startup_and_serves_nothing(
        doctor, entity, fake_on, settings):
    from django.apps import apps
    from django.core.exceptions import ImproperlyConfigured
    client = browser(doctor)
    checkout = start(client)
    fake = pay(client, checkout, entity, Order.objects.get())['Location']
    settings.ROBOTS_NOINDEX = False  # now a public site
    assert client.get(fake).status_code == 404
    assert client.get(TEST_ORDER).status_code == 404
    with pytest.raises(ImproperlyConfigured):
        apps.get_app_config('payments').ready()
