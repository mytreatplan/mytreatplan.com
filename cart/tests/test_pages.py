"""The VirtuaOrtho and Planning Services product pages (epic-ordering 9.5): one test per row of
the plan's matrix."""
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from subscriptions.tests import helpers

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]
VO, PLANNING = '/{}/virtuaortho/', '/{}/planning-services/'


def get(user, path, lang='en'):
    client = Client()
    client.force_login(user)
    response = client.get(path.format(lang))
    assert response.status_code == 200
    return response.content.decode()


def test_virtuaortho_iberia_advanced_lists_groups_prices_and_add_ons(advanced):
    html = get(advanced, VO)
    assert 'data-vo-group="VirtuaOrtho One"' in html
    assert 'data-vo-group="VirtuaOrtho DigitalTwin"' in html
    one, twin = html.split('data-vo-group="VirtuaOrtho DigitalTwin"')
    assert '1,200.00 EUR' in one and 'Byte Sync' in one and '+120.00 EUR' in one
    assert 'Spark one arch' in one and 'per aligner' in one
    # Spark under One only; ODB under DigitalTwin carries ODB DigitalTwin in its price
    assert 'Spark' not in twin
    assert 'includes ODB DigitalTwin' in twin and '879.00 EUR' in twin
    assert 'After a finished treatment' in html
    assert 'data-not-included' not in html
    assert re.search(r'href="/en/start-a-case/"[^>]*data-start-a-case', html)


def test_virtuaortho_rest_of_eu_advanced_is_not_included(advanced):
    germany = helpers.doctor('hans@example.com', country='DE')
    html = get(germany, VO)
    assert 'data-not-included' in html and 'not included in your subscription' in html
    assert 'data-start-a-case' not in html


def test_planning_basic_lists_ice_and_both_tps(advanced, other_doctor):
    # `other_doctor` holds Basic: the ICE and the TPS sizes, no VirtuaOrtho.
    html = get(other_doctor, PLANNING)
    assert 'data-not-included' in get(other_doctor, VO)
    assert html.count('data-product-row') >= 4
    for text in ('Initial case evaluation', 'Treatment planning by the maker',
                 'In-house printing treatment planning', 'Short', 'Medium', 'Long'):
        assert text in html, text
    assert 'data-add-on' in html and re.search(r'\d,?\d*\.\d\d EUR', html)
    assert 'data-not-included' not in html


def test_no_offers_both_pages_say_not_included(advanced, other_doctor, monkeypatch):
    from subscriptions import service as subscriptions
    monkeypatch.setattr(subscriptions, 'offers_for', lambda *args, **kwargs: [])
    for path in (VO, PLANNING):
        assert 'data-not-included' in get(other_doctor, path)


def test_spanish_page_in_spanish(advanced):
    html = get(advanced, VO, 'es')
    assert 'Después de un tratamiento terminado' in html and 'Iniciar un caso' in html
    assert 'por alineador' in html and 'Tamaños' in html
    assert 'Servicios de Planificación' in get(advanced, PLANNING, 'es')


def test_staff_gets_404(advanced):
    staff = get_user_model().objects.create_user('staff@example.com', 'violet-orbit-canyon-42',
                                                 is_staff=True)
    client = Client()
    client.force_login(staff)
    for path in (VO, PLANNING):
        assert client.get(path.format('en')).status_code == 404


def test_anonymous_goes_to_login(advanced):
    for path in (VO, PLANNING):
        response = Client().get(path.format('en'))
        assert response.status_code == 302 and '/login/' in response['Location']
