"""The public legal notice (aviso legal) and contracting conditions pages: EU only, the
S.L.'s data from the region profile, the conditions text from the current CONDITIONS
LegalDocument."""
import re
import xml.etree.ElementTree as ET

import pytest
from django.utils import timezone

from conftest import publish_site_documents
from legal.models import LegalDocument

PAGES = ['legal-notice', 'conditions']
SL_MARKS = ['MyTPDSO Spain S.L.', 'B93858371', 'Labradores', 'M-894040', 'IRUS 1000477547891',
            '28760', 'Tres Cantos']
SITEMAP = 'https://staging.mytreatplan.example'


@pytest.fixture
def conditions(db):
    publish_site_documents()
    LegalDocument.objects.create(
        kind='conditions', title_en='Contracting conditions v-test',
        title_es='Condiciones de contratación v-prueba', body_en='## Section\n\nEnglish body',
        body_es='## Sección\n\nCuerpo en español', published_at=timezone.now())


def test_legal_notice_es(client):
    response = client.get('/es/legal-notice/')
    html = response.content.decode()

    assert response.status_code == 200
    assert '>Aviso legal</h1>' in html
    assert '<div class="legal-prose" lang="es">' in html
    for text in ['MyTPDSO Limited', '815526', 'Baggot', 'sayhi@mytreatplan.com',
                 'España, Portugal', *SL_MARKS, 'Registro Mercantil de Madrid, hoja M-894040']:
        assert text in html, text
    assert 'Texto provisional' in html
    assert re.findall(r'<h2>(\d+)\. ', html) == [str(n) for n in range(1, 8)]
    assert 'href="/es/terms/"' in html and 'href="/es/conditions/"' in html
    assert 'href="/es/privacy/"' in html


def test_legal_notice_en(client):
    html = client.get('/en/legal-notice/').content.decode()

    assert '>Legal notice</h1>' in html
    assert '<div class="legal-prose" lang="en">' in html
    for text in ['MyTPDSO Limited', 'Spain, Portugal', 'Madrid Commercial Register, sheet M-894040',
                 *SL_MARKS]:
        assert text in html, text
    assert 'Provisional text' in html


def test_conditions_show_the_current_document(client, conditions):
    es = client.get('/es/conditions/')
    en = client.get('/en/conditions/')

    assert es.status_code == en.status_code == 200
    es_html, en_html = es.content.decode(), en.content.decode()
    assert '>Condiciones de contratación v-prueba</h1>' in es_html
    assert '<h2>Sección</h2>' in es_html and 'Cuerpo en español' in es_html
    assert '<div class="legal-prose" lang="es">' in es_html
    assert 'The version of this document in your language' not in es_html
    assert '>Contracting conditions v-test</h1>' in en_html
    assert 'English body' in en_html and 'Cuerpo' not in en_html
    assert 'data-legal-provisional' in es_html


@pytest.mark.parametrize('lang', ['en', 'es'])
def test_conditions_without_a_published_document_is_404(client, db, lang):
    LegalDocument.objects.filter(kind='conditions').delete()

    assert client.get(f'/{lang}/conditions/').status_code == 404


@pytest.mark.parametrize('provisional, shown', [(True, True), (False, False)])
def test_conditions_notice_follows_final_setting_and_document(client, db, settings,
                                                              provisional, shown):
    settings.LEGAL_TEXTS_FINAL = True
    LegalDocument.objects.filter(kind='conditions').delete()
    LegalDocument.objects.create(
        kind='conditions', title_en='C', title_es='C', body_en='x', body_es='x',
        provisional=provisional, published_at=timezone.now())

    html = client.get('/en/conditions/').content.decode()

    assert ('data-legal-provisional' in html) is shown


@pytest.mark.parametrize('page', PAGES)
def test_ae_has_neither_page(client, ae_portal, page, conditions):
    assert client.get(f'/en/{page}/').status_code == 404


def test_footer_links_eu(client):
    es = client.get('/es/').content.decode()
    en = client.get('/en/').content.decode()
    footer = re.search(r'<footer.*?</footer>', es, re.S).group(0)

    assert footer.index('/es/privacy/') < footer.index('/es/terms/') \
        < footer.index('/es/legal-notice/') < footer.index('/es/conditions/')
    assert '>Aviso legal<' in footer and '>Condiciones de contratación<' in footer
    assert '>Legal notice<' in en and '>Contracting conditions<' in en


def test_footer_has_no_links_on_ae(client, ae_portal):
    html = client.get('/en/').content.decode()

    assert 'legal-notice' not in html and '/conditions/' not in html


def _locs(client, settings):
    settings.SITE_URL = SITEMAP
    response = client.get('/sitemap.xml')
    assert response.status_code == 200
    root = ET.fromstring(response.content)
    return {el.text for el in root.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')}


def test_sitemap_eu_lists_both_pages(client, settings):
    locs = _locs(client, settings)

    for page in PAGES:
        for lang in ('en', 'es'):
            assert f'{SITEMAP}/{lang}/{page}/' in locs


def test_sitemap_ae_lists_neither(client, ae_portal, settings):
    locs = _locs(client, settings)

    assert not any('legal-notice' in loc or 'conditions' in loc for loc in locs)


@pytest.mark.parametrize('url', ['/en/', '/en/privacy/', '/en/terms/'])
def test_ae_pages_show_no_seller_data(client, ae_portal, url):
    html = client.get(url).content.decode()

    for text in SL_MARKS:
        assert text not in html, text
