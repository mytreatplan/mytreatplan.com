"""The doctor area's responsive layout (epic-ordering 9.14) in Chromium against live_server:
one shared content width on a laptop (header and panel aligned), no sideways page scroll on
a phone (with a patient, an invoice and an open order so the tables render), and the
"Add a patient" dialog fitting a short laptop screen with its buttons in view."""
import pytest

pytest.importorskip('pytest_playwright')

from playwright.sync_api import expect  # noqa: E402

from accounts import fiscal  # noqa: E402
from accounts.models import User  # noqa: E402
from patients import service as patients  # noqa: E402
from payments import service as payments  # noqa: E402
from payments.tests.conftest import COMPANY  # noqa: E402
from payments.tests.test_service import line, new, pay  # noqa: E402
from subscriptions.tests.helpers import load  # noqa: E402

from .helpers import log_in, log_out, sign_up  # noqa: E402

pytestmark = [
    pytest.mark.browser,
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
    pytest.mark.usefixtures('portal_urls', 'interface_settings'),
]

PAGES = ('home/', 'patients/', 'cart/', 'settings/')


def _signed_in(page, live_server):
    sign_up(page, live_server)
    log_out(page, live_server)
    log_in(page, live_server)


def _with_orders(admin, settings):
    """A patient, a paid order (so an invoice) and an open order for the signed-up doctor;
    the open order's checkout path."""
    settings.PAYMENTS_FAKE_GATEWAY = True
    settings.PAYMENTS_TEST_ORDERS = True
    settings.ROBOTS_NOINDEX = True
    load(admin)
    doctor = User.objects.get()
    patients.create_patient(doctor, first_name='Ana', surname='Pérez',
                            actor=patients.Actor('doctor', doctor.uid))
    fiscal.create(doctor, dict(COMPANY))
    pay(new(doctor), doctor)
    return f'checkout/{new(doctor).pk}/'


def test_panel_is_as_wide_as_the_header_on_a_laptop(live_server, page, admin, settings):
    _signed_in(page, live_server)
    checkout = _with_orders(admin, settings)
    page.set_viewport_size({'width': 1440, 'height': 800})
    for path in PAGES + (checkout,):
        page.goto(f'{live_server.url}/en/{path}')
        header = page.locator('[data-doctor-header] > div').bounding_box()
        content = page.evaluate('''() => {
            const el = document.querySelector('[data-doctor-panel] > div');
            const style = getComputedStyle(el);
            return el.getBoundingClientRect().width
                - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        }''')
        assert content > 1000, path
        assert abs(content - header['width']) <= 1, path


def test_no_sideways_scroll_on_a_phone(live_server, page, admin, settings):
    _signed_in(page, live_server)
    checkout = _with_orders(admin, settings)
    page.set_viewport_size({'width': 390, 'height': 844})
    tables = {'patients/': '[data-patients]', 'invoices/': '[data-invoices]',
              checkout: '[data-order-lines]'}
    for path in PAGES + ('invoices/', checkout):
        page.goto(f'{live_server.url}/en/{path}')
        if path in tables:
            expect(page.locator(tables[path])).to_be_visible()
        assert page.evaluate(
            'document.scrollingElement.scrollWidth <= window.innerWidth'), path


def test_add_patient_dialog_fits_a_short_laptop(live_server, page):
    _signed_in(page, live_server)
    page.set_viewport_size({'width': 1366, 'height': 768})
    page.goto(f'{live_server.url}/en/patients/')
    page.locator('[data-add-patient]').click()
    dialog = page.locator('#add-patient')
    expect(dialog).to_be_visible()
    box = dialog.bounding_box()
    assert box['y'] >= 0 and box['y'] + box['height'] <= 768
    expect(page.locator('[data-save]')).to_be_in_viewport()
