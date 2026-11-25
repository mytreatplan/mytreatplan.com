"""The case wizard in Chromium against live_server (epic-ordering 9.16): the branded TPS path and
the VirtuaOrtho DigitalTwin path from the patient to the cart, with what the script shows and
hides, and Back keeping the choices."""
import re

import pytest

pytest.importorskip('pytest_playwright')

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from accounts import makers, shipping  # noqa: E402
from cart.models import CartItem  # noqa: E402
from cart.tests.test_virtuaortho import ADDRESS  # noqa: E402
from patients.tests.conftest import BRANDED, VO_TWIN  # noqa: E402

pytestmark = [
    pytest.mark.browser,
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
    pytest.mark.usefixtures('portal_urls'),
]


@pytest.fixture
def signed_in(live_server, page, advanced, entity, patient):
    """An Iberia Advanced doctor with a fiscal entity, a saved Invisalign login and a shipping
    address, signed in (a session cookie, not the login pages), and her patient."""
    makers.save(advanced, 'invisalign', {'username': 'dr-user', 'password': 'dr-password'})
    advanced.address = shipping.create(advanced, ADDRESS)
    client = Client()
    client.force_login(advanced)
    page.context.add_cookies([{'name': settings.SESSION_COOKIE_NAME,
                               'value': client.cookies[settings.SESSION_COOKIE_NAME].value,
                               'url': live_server.url}])
    return page, advanced, patient


def _continue(page):
    page.locator('[data-continue]').click()


def test_the_branded_tps_path_ends_in_the_cart(live_server, signed_in):
    page, doctor, patient = signed_in
    base = f'{live_server.url}/en/start-a-case/{patient.code}'
    page.goto(f'{live_server.url}/en/start-a-case/')
    page.locator('[data-choose]').click()
    expect(page).to_have_url(f'{base}/product/')
    expect(page.locator('[data-step-number]')).to_have_text('Step 1 of 3')
    page.locator('[data-card="tps"]').click()
    _continue(page)

    expect(page).to_have_url(f'{base}/maker/')
    expect(page.locator('[data-maker-choices]')).to_be_hidden()  # in-house or brand first
    page.locator('[data-card="brand"]').click()
    expect(page.locator('[data-maker-choices]')).to_be_visible()
    page.locator('[data-maker-select]').select_option('smartee')
    expect(page.locator('[data-missing-login="smartee"]')).to_be_visible()
    expect(page.locator('[data-missing-login="spark"]')).to_be_hidden()
    page.locator('[data-maker-select]').select_option('invisalign')
    expect(page.locator('[data-missing-login]').first).to_be_hidden()
    _continue(page)

    expect(page).to_have_url(f'{base}/options/')
    expect(page.locator('[data-size-row]').first).to_have_attribute('data-size-row', 'ice')
    page.locator('[data-size-row="medium"]').click()
    expect(page.locator('[data-add-on="JUNIOR-2"]')).to_be_visible()
    page.locator('[data-size-row="short"]').click()
    expect(page.locator('[data-add-on="JUNIOR-2"]')).to_be_hidden()
    page.locator('[data-size-row="medium"]').click()
    page.locator('[data-add-on="JUNIOR-2"] input').check()

    page.locator('[data-back]').click()  # Back keeps the choice
    expect(page).to_have_url(f'{base}/maker/')
    expect(page.locator('[data-maker-select]')).to_have_value('invisalign')
    _continue(page)
    expect(page).to_have_url(f'{base}/options/')  # nothing was sent from here: choose again
    page.locator('[data-size-row="medium"]').click()
    page.locator('[data-add-on="JUNIOR-2"] input').check()
    page.locator('[data-add-to-cart]').click()

    expect(page).to_have_url(f'{live_server.url}/en/cart/')
    expect(page.locator(f'[data-cart-item="{patient.code}"]')).to_be_visible()
    item = CartItem.objects.get()
    assert (str(item.product_id), item.add_on_codes, item.maker) == (
        BRANDED['medium'], ['JUNIOR-2'], 'invisalign')


def test_the_virtuaortho_digitaltwin_path_ends_in_the_cart_with_the_default_address(
        live_server, signed_in):
    page, doctor, patient = signed_in
    base = f'{live_server.url}/en/start-a-case/{patient.code}'
    page.goto(f'{base}/product/')
    page.locator('[data-card="vo"]').click()
    _continue(page)

    expect(page).to_have_url(f'{base}/vo_type/')
    page.locator('[data-card="digitaltwin"]').click()
    _continue(page)

    expect(page).to_have_url(f'{base}/options/')
    page.locator('[data-vo-row="case:medium"] input').check()
    page.locator('[data-add-on="BYTE-SYNC"] input').check()
    expect(page.locator('[data-address-select]')).to_have_value(str(doctor.address.pk))
    page.locator('[data-add-to-cart-virtuaortho]').click()

    expect(page).to_have_url(f'{live_server.url}/en/cart/')
    expect(page.locator('[data-item-address]')).to_contain_text('Clínica Madrid')
    item = CartItem.objects.get()
    assert (str(item.product_id), item.add_on_codes, item.shipping_address_id) == (
        VO_TWIN['medium'], ['BYTE-SYNC'], doctor.address.pk)
    assert re.search(r'1,320\.00', page.content())
