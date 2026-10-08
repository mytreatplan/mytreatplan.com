import re

from django.urls import reverse
from django.utils import translation

# English is the default language; every public page lives under its language prefix.
HOME = '/en/'


def test_home_renders_with_base_template(client):
    response = client.get(HOME)

    assert response.status_code == 200
    templates = [t.name for t in response.templates]
    assert 'publicsite/home.html' in templates
    assert 'base.html' in templates


def test_home_shows_hero_header_and_footer(client):
    with translation.override('en'):
        url = reverse('publicsite:home')
    assert url == HOME
    html = client.get(url).content.decode()

    assert 'Digital Orthodontics' in html
    assert 'Made Simple' in html
    assert 'alt="MyTreatPlan"' in html  # header logo
    assert 'what we do' in html  # header nav
    assert 'License number' in html  # footer company line
    assert 'linkedin.com/company/mytreatplan' in html  # footer socials


def test_home_loads_only_local_assets(client):
    html = client.get(HOME).content.decode()

    assert '/static/css/site.css' in html
    assert '/static/js/htmx.min.js' in html
    assert 'fonts.googleapis.com' not in html
    assert 'cdn' not in html.lower()


NAV_ANCHORS = ['home', 'what-we-do', 'about-us', 'who-we-are', 'where-we-are', 'contact']


def test_every_nav_anchor_has_a_matching_section(client):
    html = client.get(HOME).content.decode()

    for anchor in NAV_ANCHORS:
        assert f'href="#{anchor}"' in html
        assert html.count(f'id="{anchor}"') == 1, anchor


def test_sections_render_in_nav_order(client):
    html = client.get(HOME).content.decode()

    positions = [html.index(f'id="{anchor}"') for anchor in NAV_ANCHORS]
    assert positions == sorted(positions)


def test_sections_show_their_key_content(client):
    html = client.get(HOME).content.decode()

    # What we do
    for text in ['What we do', 'High Quality', 'Tailored TPS', 'VirtuaOrtho', 'Education']:
        assert text in html
    # About us
    for text in ['About us', '+50.000', '+700', 'Satisfied Customers']:
        assert text in html
    # Who we are: the six team members
    for name in ['Belén Jiménez', 'J. Antonio de Andrés', 'Adina Marin', 'Albert Isern',
                 'Iliyana Petrova', 'Marta Estebaranz']:
        assert f'<h3 class="mt-4 text-h3 font-black">{name}</h3>' in html
    assert html.count('data-carousel-card') == 6
    # Where we are: the three offices
    for office in ['Dublin', 'Dubai', 'Madrid']:
        assert f'font-black">{office}</h3>' in html
    # Contact
    assert 'href="mailto:sayhi@mytreatplan.com"' in html
    assert 'contact_uae@' not in html


def test_burger_markup_is_accessible(client):
    html = client.get(HOME).content.decode()

    match = re.search(r'<button[^>]*\bid="site-menu-toggle"[^>]*>', html)
    assert match, 'burger button missing'
    burger = match.group(0)
    assert 'aria-controls="site-menu"' in burger
    assert 'aria-expanded="false"' in burger
    assert 'aria-label="Menu"' in burger
    assert re.search(r'<nav[^>]*\bid="site-menu"', html)


def test_menu_and_carousel_scripts_load_deferred(client):
    html = client.get(HOME).content.decode()

    assert '<script src="/static/js/menu.js" defer></script>' in html
    assert '<script src="/static/js/carousel.js" defer></script>' in html


def test_section_images_are_local_with_responsive_sizes(client):
    html = client.get(HOME).content.decode()

    for name in ['Cabecera_What', 'Cabecera_About', 'Cabecera_Where', 'Img_What', 'Img_About',
                 'Img_Who', 'Img_Contact', 'Mapa_mundo']:
        for size in ['480', '768', '1200']:
            assert f'/static/img/{name}-{size}.webp {size}w' in html
