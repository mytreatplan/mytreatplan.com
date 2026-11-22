"""The doctor area's layout and home (epic-doctor-profile entry 1): the rows of the plan's
I/O matrix that a request shows (the login landing on the home is in test_login.py, the
page records in test_page_records.py, the browser journey in browser/test_doctor_area.py)."""
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from accounts import doctor_area
from accounts.views import COMING_SOON_AREAS
from signup.tests.helpers import PASSWORD

from .helpers import _html

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

User = get_user_model()

# Each "coming soon" area: its URL name, its address, and its name in EN and ES.
AREAS = [
    ('support', 'support/', 'Support', 'Soporte'),
    ('messages', 'messages/', 'Messages', 'Mensajes'),
    ('cart', 'cart/', 'Cart', 'Carrito'),
    ('tutorials', 'tutorials/', 'Tutorials', 'Tutoriales'),
    ('virtuaortho', 'virtuaortho/', 'Virtua ORTHO', 'Virtua ORTHO'),
    ('planning_services', 'planning-services/', 'Planning Services',
     'Servicios de Planificación'),
    ('start_a_case', 'start-a-case/', 'Request New Planning', 'Solicita Nueva Planificación'),
]


@pytest.fixture
def doctor():
    return User.objects.create_user('maria.espuny@example.net', PASSWORD, title='mrs',
                                    first_name='María José', last_name='Espuny Marín',
                                    country='ES')


def _browser(user):
    browser = Client()
    browser.force_login(user)
    return browser


def _h1(html):
    return re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S).group(1).strip()


# --- The home ---------------------------------------------------------------------------

@pytest.mark.parametrize('lang, hello, intro, tiles', [
    ('en', 'Hello, María José', 'Here are some quick links that may help you.',
     ['Virtua ORTHO', 'Aligners, planning and clinical support, all in one',
      'Planning Services', 'Expert planning and support at every step',
      'Request New Planning', 'Personal data', 'Settings', 'Messages', 'Alerts', 'Welcome',
      'Tutorials']),
    ('es', 'Hola, María José', 'Aquí tienes algunos accesos rápidos que te pueden ayudar.',
     ['Virtua ORTHO', 'Alineadores, planificación y soporte clínico todo en uno',
      'Servicios de Planificación', 'Planificación y soporte experto en cada paso',
      'Solicita Nueva Planificación', 'Datos personales', 'Configuración', 'Mensajes', 'Alertas',
      'Bienvenida', 'Tutoriales']),
])
def test_home_shows_the_greeting_tiles_bar_and_profile_card(doctor, lang, hello, intro,
                                                             tiles):
    response = _browser(doctor).get(f'/{lang}/home/')

    assert response.status_code == 200
    html = _html(response)
    assert _h1(html) == hello
    assert intro in html
    for text in tiles:
        assert text in html, text
    for name in ['virtuaortho', 'planning_services', 'start_a_case', 'settings', 'messages',
                 'tutorials']:
        assert f'data-tile="{name}"' in html, name
    assert 'data-profile-card' in html
    assert 'noindex' in html


@pytest.mark.parametrize('lang, name, line', [
    ('en', 'Mrs María José Espuny Marín', 'Spain'),
    ('es', 'Sra. María José Espuny Marín', 'España'),
])
def test_profile_card_shows_the_titled_name_and_the_country(doctor, lang, name, line):
    html = _html(_browser(doctor).get(f'/{lang}/home/'))

    assert re.search(rf'<strong[^>]*data-profile-name>{re.escape(name)}</strong>', html)
    assert re.search(rf'data-profile-line>{re.escape(line)}<', html)


@pytest.mark.parametrize('names, expected', [
    ({'first_name': 'maría', 'last_name': 'espuny'}, 'ME'),
    ({'first_name': 'maría', 'last_name': ''}, 'M'),
    ({'first_name': '', 'last_name': 'espuny'}, 'E'),
    ({'first_name': '', 'last_name': ''}, 'Z'),
])
def test_initials_from_the_names_else_the_email(names, expected):
    user = User(email='zoe@example.net', **names)

    assert doctor_area.initials(user) == expected


@pytest.mark.parametrize('first, last, expected', [
    ('E\u0301mile', '\u00d6zil', '\u00c9\u00d6'),  # decomposed É: NFC first
    ('Ζωή', 'Παπαδοπούλου', 'ΖΠ'),
    ('Юлия', 'Иванова', 'ЮИ'),
    ('明', '王', '明王'),
])
def test_initials_of_decomposed_and_non_latin_names(first, last, expected):
    user = User(email='x@example.net', first_name=first, last_name=last)

    assert doctor_area.initials(user) == expected


def test_names_with_html_characters_are_escaped_once():
    user = User.objects.create_user('html@example.net', PASSWORD, first_name='<b>Ana',
                                    last_name='&Co "x"')
    html = _html(_browser(user).get('/en/home/'))

    assert re.search(r'data-avatar><span aria-hidden="true">&lt;&amp;</span>', html)
    assert 'data-profile-initials>&lt;&amp;<' in html
    assert 'data-profile-name>&lt;b&gt;Ana &amp;Co &quot;x&quot;<' in html
    assert _h1(html) == 'Hello, &lt;b&gt;Ana'
    assert 'aria-label="&lt;b&gt;Ana &amp;Co &quot;x&quot;"' in html
    assert '&amp;lt;' not in html and '<b>Ana' not in html


def test_home_without_names_greets_plainly_and_shows_the_email_initial():
    user = User.objects.create_user('zoe@example.net', PASSWORD)
    html = _html(_browser(user).get('/en/home/'))

    assert _h1(html) == 'Hello'
    assert re.search(r'data-avatar><span aria-hidden="true">Z</span>', html)
    assert re.search(r'data-profile-name>zoe@example.net<', html)
    assert 'data-profile-line' not in html


def test_avatar_shows_the_initials(doctor):
    """Without a profile photo (entry 4: test_profile_photo.py) the header's avatar and the
    profile card show the initials, and no photo link is made."""
    html = _html(_browser(doctor).get('/en/home/'))

    assert re.search(r'data-avatar><span aria-hidden="true">ME</span>', html)
    assert 'data-profile-initials>ME<' in html
    assert 'data-avatar-photo' not in html and 'data-profile-photo' not in html


def test_avatar_shows_the_clean_photo(doctor, settings, monkeypatch):
    """With a clean profile photo (entry 4) both show it, through one inline link."""
    from accounts.models import Profile
    from conftest import use_fake_bucket
    from storage import s3
    from storage.models import StoredObject

    use_fake_bucket(settings)
    links = []
    monkeypatch.setattr(s3, 'presign_download',
                        lambda key, **kwargs: links.append(key) or f'https://bucket.test/{key}')
    obj = StoredObject.objects.create(purpose='profile_photo', owner=doctor, uploaded_by=doctor,
                                      size=10, content_type='image/png',
                                      state=StoredObject.CLEAN)
    Profile.objects.create(user=doctor, photo=obj)

    html = _html(_browser(doctor).get('/en/home/'))

    assert links == [f'clean/{obj.pk}']
    assert re.search(r'data-avatar><img src="https://bucket.test/clean/[^"]+" alt="" '
                     r'class="size-full object-cover" data-avatar-photo>', html)
    assert 'data-profile-photo><img src="https://bucket.test/clean/' in html
    assert 'data-profile-initials' not in html


def test_staff_see_the_same_home():
    staff = User.objects.create_superuser('staff.home@example.net', PASSWORD)
    response = _browser(staff).get('/en/home/')

    assert response.status_code == 200
    assert 'data-profile-card' in _html(response)


# --- Anonymous visitors -----------------------------------------------------------------

@pytest.mark.parametrize('path', ['home/'] + [area[1] for area in AREAS])
def test_anonymous_visitor_is_sent_to_login(client, path):
    response = client.get(f'/en/{path}')

    assert response.status_code == 302
    assert response['Location'] == f'/en/login/?next=/en/{path}'


# --- The "coming soon" areas ------------------------------------------------------------

@pytest.mark.parametrize('name, path, en, es', AREAS)
def test_area_not_built_is_coming_soon_in_both_languages(doctor, name, path, en, es):
    browser = _browser(doctor)
    for lang, area, soon in [('en', en, 'This section is coming soon.'),
                             ('es', es, 'Esta sección estará disponible próximamente.')]:
        response = browser.get(f'/{lang}/{path}')
        assert response.status_code == 200
        html = _html(response)
        assert _h1(html) == area
        assert soon in html
        assert re.search(rf'href="/{lang}/home/"[^>]*data-back-home', html)
        assert 'noindex' in html


def test_every_area_has_a_route():
    assert sorted(COMING_SOON_AREAS) == sorted(area[0] for area in AREAS)


def test_unknown_area_is_404(doctor, rf):
    from django.http import Http404

    from accounts.views import coming_soon

    request = rf.get('/en/nowhere/')
    request.user = doctor
    with pytest.raises(Http404):
        coming_soon(request, area='nowhere')
    assert _browser(doctor).get('/en/no-such-area/').status_code == 404


def test_language_switcher_keeps_the_area(doctor):
    html = _html(_browser(doctor).get('/en/planning-services/'))

    assert 'href="/es/planning-services/"' in html


# --- The header ---------------------------------------------------------------------------

def _nav(html):
    return re.search(r'<nav id="site-menu".*?</nav>', html, re.S).group(0)


@pytest.mark.parametrize('lang, labels', [
    ('en', {'home': 'home', 'patients': 'patients', 'support': 'support',
            'messages': 'Messages', 'settings': 'Settings', 'cart': 'Cart',
            'logout': 'Log out'}),
    ('es', {'home': 'home', 'patients': 'pacientes', 'support': 'soporte',
            'messages': 'Mensajes', 'settings': 'Configuración', 'cart': 'Carrito',
            'logout': 'Cerrar sesión'}),
])
def test_header_holds_the_links_icons_avatar_switcher_and_logout(doctor, lang, labels):
    html = _html(_browser(doctor).get(f'/{lang}/home/'))
    nav = _nav(html)

    assert re.search(rf'<a href="/{lang}/home/" class="block[^"]*" aria-label="[^"]+" '
                     r'data-isotype>', html)
    assert re.search(rf'href="/{lang}/home/" class="[^"]*" aria-current="page">'
                     rf'{labels["home"]}</a>', nav)
    for name, path in [('patients', 'patients'), ('support', 'support')]:
        assert re.search(rf'href="/{lang}/{path}/" class="[^"]*">{labels[name]}</a>', nav)
    for name in ['messages', 'settings', 'cart']:
        assert re.search(rf'href="/{lang}/{name}/" class="[^"]*" aria-label="{labels[name]}"',
                         nav), name
    assert 'data-avatar' in nav
    assert re.search(rf'<form method="post" action="/{lang}/logout/">', nav)
    assert re.search(rf'data-portal-link="logout">.*?{labels["logout"]}</button>', nav, re.S)
    assert 'hreflang="en"' in nav and 'hreflang="es"' in nav
    assert 'id="site-menu-toggle"' in html


def test_header_marks_the_current_area(doctor):
    nav = _nav(_html(_browser(doctor).get('/en/patients/')))

    assert re.search(r'href="/en/patients/" class="[^"]*" aria-current="page">patients</a>',
                     nav)
    assert not re.search(r'href="/en/home/" class="[^"]*" aria-current="page"', nav)


@pytest.mark.parametrize('path', ['welcome/', 'home/', 'patients/'])
def test_doctor_pages_use_the_doctor_header(doctor, path):
    html = _html(_browser(doctor).get(f'/en/{path}'))

    assert 'data-doctor-header' in html and 'data-doctor-panel' in html
    assert 'Logo_MyTreatPlan.webp' not in html


@pytest.mark.parametrize('path', ['login/', 'signup/'])
def test_login_and_signup_keep_the_light_header(client, path):
    html = _html(client.get(f'/en/{path}'))

    assert 'data-doctor-header' not in html
    assert 'Logo_MyTreatPlan.webp' in html


def test_welcome_keeps_its_heading_and_gains_a_button_to_home(doctor):
    html = _html(_browser(doctor).get('/es/welcome/'))

    assert _h1(html) == 'Le damos la bienvenida a MyTreatPlan'
    assert re.search(r'href="/es/home/"[^>]*data-go-home>Ir a su página de inicio</a>', html)


# --- Assets -------------------------------------------------------------------------------

@pytest.mark.parametrize('lang', ['en', 'es'])
def test_home_loads_only_local_assets(doctor, lang):
    html = _html(_browser(doctor).get(f'/{lang}/home/'))

    # The assets: images, scripts, stylesheets and preloads, the isotype's mask, the fonts.
    sources = re.findall(r'\ssrc="([^"]+)"', html)
    sources += [url.strip().split(' ')[0]
                for srcset in re.findall(r'\ssrcset="([^"]+)"', html)
                for url in srcset.split(',')]
    sources += re.findall(r'<link rel="(?:stylesheet|preload)" href="([^"]+)"', html)
    sources += re.findall(r"url\([\"']?([^\"')]+)", html)
    assert len(sources) > 5
    for url in sources:
        assert url.startswith('/static/'), url
    assert '/static/img/isotype.svg' in html
    assert 'fonts.googleapis.com' not in html
