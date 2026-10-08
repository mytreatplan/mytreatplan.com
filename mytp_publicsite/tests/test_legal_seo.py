"""Story 1.4: Privacy and Terms pages, SEO basics (robots, sitemap, canonical, Open Graph,
JSON-LD), the Irish company in the footer, portal links, and the draft legal texts guard."""
import json
import re
import xml.etree.ElementTree as ET

import pytest
from django.core.checks import Error, run_checks
from django.test import override_settings

from mytp_publicsite.checks import DRAFT_LEGAL_TEXTS, check_legal_texts_final

SITE = 'https://staging.mytreatplan.example'
LEGAL_PAGES = ['/en/privacy/', '/en/terms/', '/es/privacy/', '/es/terms/']
SPANISH_NOTE = 'La versión en español de este documento se está preparando'


def _html(client, url):
    response = client.get(url)
    assert response.status_code == 200, url
    return response.content.decode()


# --- Legal pages ---------------------------------------------------------------------

@pytest.mark.parametrize('url, title, sections', [
    ('/en/privacy/', 'Privacy Policy', 14),
    ('/en/terms/', 'Website Terms &amp; Conditions', 16),
    ('/es/privacy/', 'Política de privacidad', 14),
    ('/es/terms/', 'Términos y condiciones del sitio web', 16),
])
def test_legal_page_has_title_and_numbered_headings(client, url, title, sections):
    html = _html(client, url)

    assert f'<h1 class="' in html and f'>{title}</h1>' in html
    headings = re.findall(r'<h2>(\d+)\. ', html)
    assert headings == [str(n) for n in range(1, sections + 1)]
    assert 'Last updated: 31 August 2026' in html
    assert 'max-w-legal' in html  # 760px measure


@pytest.mark.parametrize('url', ['/en/privacy/', '/en/terms/'])
def test_english_legal_pages_have_no_spanish_note(client, url):
    assert SPANISH_NOTE not in _html(client, url)


@pytest.mark.parametrize('url', ['/es/privacy/', '/es/terms/'])
def test_spanish_legal_pages_show_english_text_with_a_note(client, url):
    html = _html(client, url)

    assert SPANISH_NOTE in html
    assert '<div class="legal-prose" lang="en">' in html
    assert '1. ' in html and 'MyTPDSO L.L.C-FZ' in html  # the English draft body


@pytest.mark.parametrize('page', ['privacy', 'terms'])
def test_spanish_body_is_used_once_it_exists(client, tmp_path, settings, page):
    """Adding legal/_<page>_body_es.html is enough: /es/ shows it, without the note."""
    legal_dir = tmp_path / 'publicsite' / 'legal'
    legal_dir.mkdir(parents=True)
    (legal_dir / f'_{page}_body_es.html').write_text('<h2>1. Texto final en español</h2>',
                                                    encoding='utf-8')
    settings.TEMPLATES = [{**settings.TEMPLATES[0],
                           'DIRS': [tmp_path, *settings.TEMPLATES[0]['DIRS']]}]

    html = _html(client, f'/es/{page}/')
    assert '<div class="legal-prose" lang="es">' in html
    assert 'Texto final en español' in html
    assert 'MyTPDSO L.L.C-FZ' not in html
    assert SPANISH_NOTE not in html
    # English is unaffected.
    english = _html(client, f'/en/{page}/')
    assert '<div class="legal-prose" lang="en">' in english
    assert 'Texto final' not in english


def test_terms_link_to_privacy_in_the_current_language(client):
    assert 'href="/es/privacy/"' in _html(client, '/es/terms/')
    assert 'href="/en/privacy/"' in _html(client, '/en/terms/')


# --- Footer ---------------------------------------------------------------------------

@pytest.mark.parametrize('url, lang', [('/en/', 'en'), ('/es/', 'es'), ('/en/privacy/', 'en'),
                                       ('/es/terms/', 'es')])
def test_footer_has_legal_links_and_irish_company(client, url, lang):
    html = _html(client, url)
    footer = re.search(r'<footer.*?</footer>', html, re.S).group(0)

    assert f'href="/{lang}/privacy/"' in footer
    assert f'href="/{lang}/terms/"' in footer
    assert 'MyTPDSO Limited' in footer
    assert '815526' in footer
    assert '19 Baggot Street Lower, Dublin 2, D02 X658' in footer
    assert '<strong>MyTreatPlan</strong>' in footer
    assert 'MytreatPlan' not in html
    assert 'L.L.C.-FZ' not in footer and 'Meydan' not in footer


def test_spanish_footer_is_translated(client):
    footer = re.search(r'<footer.*?</footer>', _html(client, '/es/'), re.S).group(0)

    for text in ['Política de privacidad', 'Términos y condiciones', 'Irlanda',
                 'Número de registro mercantil']:
        assert text in footer, text


# --- robots.txt and sitemap.xml -------------------------------------------------------

@override_settings(SITE_URL=SITE)
def test_robots_txt(client):
    response = client.get('/robots.txt')

    assert response.status_code == 200
    assert response['Content-Type'].startswith('text/plain')
    lines = response.content.decode().splitlines()
    assert 'User-agent: *' in lines
    assert 'Allow: /' in lines
    assert f'Sitemap: {SITE}/sitemap.xml' in lines


def test_robots_txt_answers_head(client):
    response = client.head('/robots.txt')

    assert response.status_code == 200
    assert response['Content-Type'].startswith('text/plain')


NS = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9', 'xhtml': 'http://www.w3.org/1999/xhtml'}


@override_settings(SITE_URL=SITE)
def test_sitemap_lists_every_page_in_both_languages_with_alternates(client):
    response = client.get('/sitemap.xml')

    assert response.status_code == 200
    root = ET.fromstring(response.content)  # valid XML
    urls = root.findall('sm:url', NS)
    locs = {u.find('sm:loc', NS).text for u in urls}
    assert locs == {f'{SITE}/{lang}/{page}' for lang in ['en', 'es']
                    for page in ['', 'privacy/', 'terms/']}
    for url in urls:
        alternates = {link.get('hreflang'): link.get('href')
                      for link in url.findall('xhtml:link', NS)}
        assert set(alternates) == {'en', 'es'}
        assert all(href.startswith(f'{SITE}/') for href in alternates.values())
    assert 'testserver' not in response.content.decode()


def test_crawler_files_are_not_language_prefixed(client):
    assert client.get('/en/robots.txt').status_code == 404
    assert client.get('/en/sitemap.xml').status_code == 404


# --- Head: canonical, Open Graph, JSON-LD ---------------------------------------------

def _meta(html, prop):
    match = re.search(rf'<meta (?:property|name)="{re.escape(prop)}" content="([^"]*)">', html)
    return match.group(1) if match else None


@override_settings(SITE_URL=SITE)
@pytest.mark.parametrize('url', ['/en/', '/es/'] + LEGAL_PAGES)
def test_head_canonical_and_open_graph_use_site_url(client, url):
    html = _html(client, url + '?utm_source=x')

    assert f'<link rel="canonical" href="{SITE}{url}">' in html
    assert _meta(html, 'og:url') == f'{SITE}{url}'
    assert _meta(html, 'og:title')
    assert _meta(html, 'og:description')
    assert _meta(html, 'og:image') == f'{SITE}/static/img/og-mytreatplan.png'
    assert _meta(html, 'og:image:width') == '1200'
    assert _meta(html, 'og:image:height') == '630'
    assert _meta(html, 'twitter:card') == 'summary_large_image'
    assert 'testserver' not in html


def test_og_image_is_1200_by_630():
    from django.conf import settings
    data = (settings.BASE_DIR / 'static' / 'img' / 'og-mytreatplan.png').read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    width, height = int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
    assert (width, height) == (1200, 630)


def _json_ld(html):
    return re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)


@override_settings(SITE_URL=SITE)
def test_organization_json_ld_on_home_only(client):
    blocks = _json_ld(_html(client, '/en/'))
    assert len(blocks) == 1
    data = json.loads(blocks[0])
    assert data['@type'] == 'Organization'
    assert data['legalName'] == 'MyTPDSO Limited'
    assert data['identifier']['value'] == '815526'
    assert data['address']['streetAddress'] == '19 Baggot Street Lower'
    assert data['address']['postalCode'] == 'D02 X658'
    assert data['address']['addressCountry'] == 'IE'
    assert data['url'] == f'{SITE}/'
    assert data['logo'].startswith(f'{SITE}/static/')
    assert [p['address']['addressLocality'] for p in data['location']] == ['Dublin', 'Dubai', 'Madrid']
    assert data['areaServed']
    assert 'https://www.linkedin.com/company/mytreatplan/' in data['sameAs']

    for url in LEGAL_PAGES:
        assert _json_ld(_html(client, url)) == [], url


# --- Portal links ---------------------------------------------------------------------

@pytest.mark.parametrize('url', ['/en/', '/en/privacy/'])
def test_portal_links_absent_while_pages_do_not_exist(client, url):
    html = _html(client, url)

    assert 'data-portal-link' not in html
    assert 'Sign up' not in html
    assert 'Log in' not in html


@override_settings(ROOT_URLCONF='mytp_publicsite.tests.urls_with_portal')
@pytest.mark.parametrize('url, lang', [('/en/', 'en'), ('/es/', 'es'), ('/es/privacy/', 'es')])
def test_portal_links_render_once_the_urls_exist(client, url, lang):
    html = _html(client, url)
    nav = re.search(r'<nav[^>]*aria-label="(?:Main|Principal)".*?</nav>', html, re.S).group(0)

    assert f'href="/{lang}/signup/" class="' in nav
    assert f'href="/{lang}/login/" class="' in nav
    assert re.search(r'data-portal-link="signup">[^<]+</a>', nav)
    assert re.search(r'data-portal-link="login">[^<]+</a>', nav)


# --- Draft legal texts guard ----------------------------------------------------------

@override_settings(DEBUG=False, LEGAL_TEXTS_FINAL=False)
def test_deploy_check_fails_on_draft_legal_texts():
    errors = [e for e in run_checks(include_deployment_checks=True) if e.id == DRAFT_LEGAL_TEXTS]

    assert len(errors) == 1
    assert isinstance(errors[0], Error)
    assert 'draft legal texts' in errors[0].msg


@override_settings(DEBUG=False, LEGAL_TEXTS_FINAL=False)
def test_draft_guard_is_a_deploy_only_check():
    assert DRAFT_LEGAL_TEXTS not in [e.id for e in run_checks()]


@pytest.mark.parametrize('debug, final', [(True, False), (False, True), (True, True)])
def test_deploy_check_passes_in_debug_or_with_final_texts(debug, final):
    with override_settings(DEBUG=debug, LEGAL_TEXTS_FINAL=final):
        assert check_legal_texts_final(None) == []
