"""Epic-regions 2.6 (R5): the footer, the home page's structured data and the public
Privacy and Terms pages name the company of the instance's region profile, and the
public legal pages say their text is provisional while LEGAL_TEXTS_FINAL is off."""
import json
import re

import pytest

EU_COMPANY = ['MyTPDSO Limited', 'Company number 815526 (Ireland)',
              '19 Baggot Street Lower, Dublin 2, D02 X658']
AE_COMPANY = ['MyTPDSO L.L.C-FZ', 'License 2542361.01 (Dubai)',
              'Meydan Grandstand, 6th Floor, Meydan Road, Nad Al Sheba, Dubai']
# The registration line on a Spanish page (the English bodies keep the English one).
REGISTRATION_ES = {
    'Company number 815526 (Ireland)': 'Número de registro mercantil 815526 (Irlanda)',
    'License 2542361.01 (Dubai)': 'Licencia 2542361.01 (Dubái)',
}
# The bare number in the structured data.
NUMBER = {'MyTPDSO Limited': '815526', 'MyTPDSO L.L.C-FZ': '2542361.01'}
# Bits of each company that must never show on the other region's site.
EU_MARKS = ['MyTPDSO Limited', '815526', 'Baggot']
AE_MARKS = ['L.L.C-FZ', '2542361', 'Meydan']
PROVISIONAL_EN = 'Provisional text: this document is pending legal review'
PROVISIONAL_ES = 'Texto provisional: este documento está pendiente de revisión legal'


@pytest.fixture(params=['eu', 'ae'])
def region(request):
    """Each test runs on the default (eu) portal and on the ae one (`ae_portal`)."""
    if request.param == 'ae':
        request.getfixturevalue('ae_portal')
        return AE_COMPANY, EU_MARKS
    return EU_COMPANY, AE_MARKS


def _html(client, url):
    response = client.get(url)
    assert response.status_code == 200, url
    return response.content.decode()


def _footer(html):
    return re.search(r'<footer.*?</footer>', html, re.S).group(0)


def _body(html):
    return re.search(r'<div class="legal-prose".*?</article>', html, re.S).group(0)


@pytest.mark.parametrize('url', ['/en/', '/es/', '/en/privacy/', '/es/terms/'])
def test_footer_names_the_region_company_only(client, region, url):
    company, other = region
    footer = _footer(_html(client, url))
    if url.startswith('/es/'):
        company = [REGISTRATION_ES.get(text, text) for text in company]
        assert 'Company number' not in footer and 'License ' not in footer

    for text in company:
        assert text in footer, text
    for text in other:
        assert text not in footer, text


@pytest.mark.parametrize('url, country', [('/en/', 'Ireland'), ('/es/', 'Irlanda')])
def test_footer_names_the_office_country_in_the_page_language(client, url, country):
    assert f'D02 X658, {country}' in _footer(_html(client, url))


def test_ae_footer_office(client, ae_portal):
    assert ('Registered office: Meydan Grandstand, 6th Floor, Meydan Road, Nad Al Sheba, '
            'Dubai, United Arab Emirates') in _footer(_html(client, '/en/'))


def test_structured_data_is_the_region_company(client, region):
    company, other = region
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>',
                      _html(client, '/en/'), re.S).group(1)
    data = json.loads(block)

    assert data['legalName'] == company[0]
    assert data['identifier'] == {'@type': 'PropertyValue',
                                  'name': 'Company registration number',
                                  'value': NUMBER[company[0]]}
    address = data['address']
    assert ', '.join(filter(None, [address['streetAddress'], address['addressLocality'],
                                   address.get('postalCode')])) == company[2]
    assert address['addressCountry'] == ('IE' if company is EU_COMPANY else 'AE')
    for text in other:
        assert text not in json.dumps(data, ensure_ascii=False), text


@pytest.mark.parametrize('url', ['/en/privacy/', '/en/terms/', '/es/privacy/', '/es/terms/'])
def test_legal_page_names_the_region_company_only(client, region, url):
    company, other = region
    body = _body(_html(client, url))

    for text in company:
        assert text in body, text
    for text in other:
        assert text not in body, text


def test_english_body_names_the_country_in_english_on_a_spanish_page(client):
    """The /es/ page shows the English body: its office line stays English."""
    body = _body(_html(client, '/es/privacy/'))
    assert 'D02 X658, Ireland' in body and 'Irlanda' not in body


@pytest.mark.parametrize('url, notice', [
    ('/en/privacy/', PROVISIONAL_EN), ('/en/terms/', PROVISIONAL_EN),
    ('/es/privacy/', PROVISIONAL_ES), ('/es/terms/', PROVISIONAL_ES),
])
def test_provisional_notice_while_the_texts_are_not_final(client, settings, url, notice):
    settings.LEGAL_TEXTS_FINAL = False
    html = _html(client, url)
    assert notice in html and 'data-legal-provisional' in html

    settings.LEGAL_TEXTS_FINAL = True
    html = _html(client, url)
    assert notice not in html and 'data-legal-provisional' not in html


def test_no_provisional_notice_outside_the_legal_pages(client, settings):
    settings.LEGAL_TEXTS_FINAL = False
    assert 'data-legal-provisional' not in _html(client, '/en/')


def test_english_body_keeps_the_english_registration_on_a_spanish_page(client):
    body = _body(_html(client, '/es/terms/'))
    assert 'Company number 815526 (Ireland)' in body
    assert 'Número de registro' not in body
