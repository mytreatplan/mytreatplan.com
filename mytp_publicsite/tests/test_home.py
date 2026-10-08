from django.urls import reverse


def test_home_renders_with_base_template(client):
    response = client.get('/')

    assert response.status_code == 200
    templates = [t.name for t in response.templates]
    assert 'publicsite/home.html' in templates
    assert 'base.html' in templates


def test_home_shows_hero_header_and_footer(client):
    html = client.get(reverse('publicsite:home')).content.decode()

    assert 'Digital Orthodontics' in html
    assert 'Made Simple' in html
    assert 'alt="MyTreatPlan"' in html  # header logo
    assert 'what we do' in html  # header nav
    assert 'License number' in html  # footer company line
    assert 'linkedin.com/company/mytreatplan' in html  # footer socials


def test_home_loads_only_local_assets(client):
    html = client.get('/').content.decode()

    assert '/static/css/site.css' in html
    assert '/static/js/htmx.min.js' in html
    assert 'fonts.googleapis.com' not in html
    assert 'cdn' not in html.lower()
