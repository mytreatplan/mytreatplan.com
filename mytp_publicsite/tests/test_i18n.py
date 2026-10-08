"""English and Spanish: URL prefixes, root redirect, language switcher and the Spanish catalogue."""
import re
import shutil
from collections import Counter

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

CATALOGUE = settings.BASE_DIR / 'locale' / 'es' / 'LC_MESSAGES' / 'django.po'

# Folders makemessages must not scan (vendored, generated or planning files).
# To refresh the catalogue by hand:
#   python manage.py makemessages -l es -i node_modules -i static_root -i '_bmad*'
MAKEMESSAGES_IGNORE = ['node_modules', 'static_root', '_bmad*']

# Only these folders hold translatable strings; the catalogue test copies just these.
TRANSLATABLE_FOLDERS = ['templates', 'mytp_publicsite', 'accounts', 'mytreatplan', 'locale']


# --- Pages and redirects ---------------------------------------------------------------

def test_english_page(client):
    response = client.get('/en/')

    assert response.status_code == 200
    html = response.content.decode()
    assert '<html lang="en"' in html
    assert 'What we do' in html
    assert 'font-black">Dubai</h3>' in html


def test_spanish_page(client):
    response = client.get('/es/')

    assert response.status_code == 200
    html = response.content.decode()
    assert '<html lang="es"' in html
    # One string from every section, plus header and footer.
    for text in ['Ortodoncia Digital', 'qué hacemos', 'Qué hacemos', 'Sobre nosotros',
                 'Quiénes<br>somos', 'Dónde estamos', 'Contacto', 'Contáctenos',
                 'Número de licencia', 'Todos los derechos reservados']:
        assert text in html, text
    assert 'font-black">Dubái</h3>' in html
    assert 'What we do' not in html
    # Brand and proper nouns are kept.
    for text in ['VirtuaOrtho', 'Belén Jiménez', 'sayhi@mytreatplan.com']:
        assert text in html, text


@pytest.mark.parametrize('accept_language, expected', [
    ('es', '/es/'),
    ('es-ES,es;q=0.9,en;q=0.8', '/es/'),
    ('en', '/en/'),
    ('fr-FR,fr;q=0.9', '/en/'),
    (None, '/en/'),
])
def test_root_redirects_by_browser_language(client, accept_language, expected):
    headers = {'HTTP_ACCEPT_LANGUAGE': accept_language} if accept_language else {}
    response = client.get('/', **headers)

    assert response.status_code == 302
    assert response['Location'] == expected


def test_unknown_language_is_404(client):
    assert client.get('/fr/').status_code == 404


def test_admin_stays_unprefixed():
    assert reverse('admin:index') == '/admin/'


# --- Language switcher -----------------------------------------------------------------

def _switcher_links(html):
    """{lang: <a ...> tag} for each link in the language switcher."""
    links = re.findall(r'<a [^>]*\bhreflang="[^"]+"[^>]*>', html)
    return {re.search(r'\bhreflang="([^"]+)"', a).group(1): a for a in links}


@pytest.mark.parametrize('page, other', [('/en/', '/es/'), ('/es/', '/en/')])
def test_switcher_links_to_the_same_page_in_the_other_language(client, page, other):
    html = client.get(page).content.decode()
    links = _switcher_links(html)

    assert set(links) == {'en', 'es'}
    current_lang = page.strip('/')
    other_lang = other.strip('/')
    assert f'href="{other}"' in links[other_lang]
    assert f'lang="{other_lang}"' in links[other_lang]
    assert 'aria-current' not in links[other_lang]
    assert f'href="{page}"' in links[current_lang]
    assert 'aria-current="true"' in links[current_lang]


def test_switcher_sits_inside_the_burger_panel(client):
    html = client.get('/en/').content.decode()

    nav = re.search(r'<nav[^>]*\bid="site-menu".*?</nav>', html, re.S).group(0)
    assert 'hreflang="es"' in nav
    assert 'aria-label="Language"' in nav


def test_head_lists_alternate_languages(client):
    html = client.get('/es/').content.decode()

    assert '<link rel="alternate" hreflang="en" href="http://testserver/en/">' in html
    assert '<link rel="alternate" hreflang="es" href="http://testserver/es/">' in html
    assert '<link rel="alternate" hreflang="x-default" href="http://testserver/">' in html


def test_head_alternates_omit_the_query_string(client):
    html = client.get('/es/?utm=1').content.decode()

    alternates = re.findall(r'<link rel="alternate" hreflang="[^"]+" href="([^"]+)">', html)
    assert alternates
    assert all('?' not in href for href in alternates), alternates
    assert '<link rel="alternate" hreflang="en" href="http://testserver/en/">' in html


# --- Spanish catalogue -----------------------------------------------------------------

def _parse_po(path):
    """Entries of a .po file as dicts: msgid, msgstrs (list), fuzzy. Skips header and obsolete."""
    entries = []
    for block in path.read_text(encoding='utf-8').split('\n\n'):
        lines = [line for line in block.strip().splitlines() if not line.startswith('#~')]
        flags = ' '.join(line for line in lines if line.startswith('#,'))
        fields, current = {}, None
        for line in lines:
            match = re.match(r'^(msgid|msgid_plural|msgctxt|msgstr(?:\[\d+\])?) (".*")$', line)
            if match:
                current = match.group(1)
                fields[current] = _unquote(match.group(2))
            elif line.startswith('"') and current:
                fields[current] += _unquote(line)
        if 'msgid' not in fields or fields['msgid'] == '':
            continue
        entries.append({
            'msgid': fields['msgid'],
            'msgstrs': [v for k, v in fields.items() if k.startswith('msgstr')],
            'fuzzy': 'fuzzy' in flags,
        })
    return entries


def _unquote(quoted):
    escapes = {'n': '\n', 't': '\t', '"': '"', '\\': '\\'}
    return re.sub(r'\\(.)', lambda m: escapes.get(m.group(1), m.group(0)), quoted[1:-1])


def _problems(entries):
    problems = []
    for entry in entries:
        if entry['fuzzy']:
            problems.append(f"fuzzy: {entry['msgid']!r}")
        elif not all(entry['msgstrs']):
            problems.append(f"untranslated: {entry['msgid']!r}")
    return problems


def test_committed_catalogue_is_complete():
    entries = _parse_po(CATALOGUE)

    assert entries
    assert _problems(entries) == []


def test_every_public_string_has_a_spanish_translation(tmp_path, monkeypatch):
    """Re-extract the strings from a temp copy of the code into a copy of the catalogue: nothing
    may be new, untranslated or fuzzy. Fails naming the string when a {% translate %} lacks
    Spanish. The copy matters: makemessages writes into ./locale, so it must not run in the repo."""
    committed_bytes = CATALOGUE.read_bytes()
    source = tmp_path / 'src'
    for folder in TRANSLATABLE_FOLDERS:
        shutil.copytree(settings.BASE_DIR / folder, source / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.mo'))
    shutil.copy(settings.BASE_DIR / 'manage.py', source / 'manage.py')
    locale_dir = source / 'locale'

    monkeypatch.chdir(source)
    with override_settings(LOCALE_PATHS=[locale_dir]):
        call_command('makemessages', locale=['es'], ignore_patterns=MAKEMESSAGES_IGNORE,
                     verbosity=0)

    assert CATALOGUE.read_bytes() == committed_bytes, 'makemessages touched the real catalogue'
    extracted = _parse_po(locale_dir / 'es' / 'LC_MESSAGES' / 'django.po')
    committed = {entry['msgid'] for entry in _parse_po(CATALOGUE)}
    missing = [f"missing from django.po: {e['msgid']!r}" for e in extracted
               if e['msgid'] not in committed]
    assert missing + _problems(extracted) == []


PLACEHOLDER = re.compile(r'%\(\w+\)s|\{\w+\}|<[^>]+>|&\w+;')


def test_spanish_keeps_placeholders_and_markup():
    """Same %(name)s / {name} placeholders and HTML tags as the English; entities other
    than &amp; (which Spanish writes as "y") are kept too."""
    mismatched = []
    for entry in _parse_po(CATALOGUE):
        def tokens(text):
            return Counter(t for t in PLACEHOLDER.findall(text) if t != '&amp;')
        for msgstr in entry['msgstrs']:
            if tokens(entry['msgid']) != tokens(msgstr):
                mismatched.append(entry['msgid'])
    assert mismatched == []
