"""The payments tests' fixtures: the configuration snapshot from the fake admin site
(billing's catalogue of subscriptions/tests/helpers.py: TPS branded at 100.00 EUR, Iberia's
Spanish company exempt with Redsys and bank transfer, the rest of the EU's Irish company at
23 % with a reverse-charge text), the country map, a doctor with a fiscal entity, the
fake gateway on, and a recorder of the four hooks."""
import pytest

from accounts import fiscal
from integration.tests.snapshot_rows import put_country
from payments import hooks
from subscriptions.tests.conftest import *  # noqa: F401,F403  (autouse interface settings)
from subscriptions.tests.helpers import doctor as make_doctor, load


@pytest.fixture(autouse=True)
def country_map():
    for code, state in [('ES', 'eu'), ('PT', 'eu'), ('DE', 'eu'), ('IE', 'eu'),
                        ('AE', 'ae'), ('SA', 'ae')]:
        put_country(code, state)


@pytest.fixture
def catalogue_loaded(admin):
    load(admin)


@pytest.fixture
def fake_on(settings):
    settings.PAYMENTS_FAKE_GATEWAY = True
    settings.PAYMENTS_TEST_ORDERS = True
    settings.ROBOTS_NOINDEX = True  # a staging site: the flags are refused on a public one
    return settings


COMPANY = {'kind': 'company', 'legal_name': 'Clínica Espuny S.L.', 'tax_id': 'B12345674',
           'country': 'ES', 'address_line1': 'Calle Mayor 1', 'city': 'Madrid',
           'postal_code': '28001'}


@pytest.fixture
def doctor(catalogue_loaded):
    return make_doctor()


@pytest.fixture
def entity(doctor):
    return fiscal.create(doctor, dict(COMPANY))


@pytest.fixture
def events():
    """Records each hook call as (event, order id, state); removed afterwards."""
    seen = []
    names = []
    for event in hooks.EVENTS:
        name = f'test_{event}'
        hooks.register(event, name, lambda order, event=event: seen.append(
            (event, order.pk, order.state)))
        names.append((event, name))
    yield seen
    for event, name in names:
        hooks.unregister(event, name)
