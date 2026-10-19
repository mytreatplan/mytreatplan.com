"""accounts.User: email as the username, its manager, admin and the welcome page."""
import pytest
from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.db import IntegrityError

User = get_user_model()
PASSWORD = 'violet-orbit-canyon-42'


def test_email_is_the_username():
    assert User.USERNAME_FIELD == 'email'
    assert User.REQUIRED_FIELDS == []
    assert 'username' not in {f.name for f in User._meta.get_fields()}
    # The email is encrypted (each ciphertext differs): its blind index is what is unique.
    assert User._meta.get_field('email_index').unique


@pytest.mark.parametrize('raw, expected', [
    ('  Ana.Garcia@EXAMPLE.Com  ', 'Ana.Garcia@example.com'),
    ('ana@example.com', 'ana@example.com'),
    (' no-at-sign ', 'no-at-sign'),
])
def test_normalize_email(raw, expected):
    assert User.objects.normalize_email(raw) == expected


@pytest.mark.django_db
def test_create_user():
    user = User.objects.create_user(' Ana@EXAMPLE.com ', PASSWORD)

    assert user.email == 'Ana@example.com'
    assert user.password != PASSWORD and user.check_password(PASSWORD)
    assert not user.is_staff and not user.is_superuser and user.is_active
    assert str(user) == 'Ana@example.com'


@pytest.mark.django_db
def test_by_phone_finds_users_through_the_blind_index():
    ana = User.objects.create_user('ana@example.com', PASSWORD, phone='+34612345678')
    User.objects.create_user('bea@example.com', PASSWORD, phone='+34612345679')
    User.objects.create_user('carla@example.com', PASSWORD)

    assert list(User.objects.by_phone('+34612345678')) == [ana]
    assert not User.objects.by_phone('+34600000000').exists()


@pytest.mark.django_db
def test_create_user_requires_an_email():
    with pytest.raises(ValueError):
        User.objects.create_user('', PASSWORD)


@pytest.mark.django_db
def test_create_superuser():
    user = User.objects.create_superuser('admin@example.com', PASSWORD)
    assert user.is_staff and user.is_superuser


@pytest.mark.django_db
@pytest.mark.parametrize('flag', ['is_staff', 'is_superuser'])
def test_create_superuser_refuses_missing_flags(flag):
    with pytest.raises(ValueError):
        User.objects.create_superuser('admin@example.com', PASSWORD, **{flag: False})


@pytest.mark.django_db
def test_email_is_unique():
    User.objects.create_user('ana@example.com', PASSWORD)
    with pytest.raises(IntegrityError):
        User.objects.create_user('ana@example.com', PASSWORD)


@pytest.mark.django_db
def test_email_case_variant_is_refused():
    """The unique blind index of the lowercased email: one address whatever its case."""
    User.objects.create_user('Ana.Garcia@example.com', PASSWORD)
    with pytest.raises(IntegrityError):
        User.objects.create_user('ana.garcia@example.com', PASSWORD)


@pytest.mark.django_db
@pytest.mark.parametrize('typed', ['Ana.Garcia@example.com', 'ana.garcia@example.com',
                                   'Ana.Garcia@EXAMPLE.com'])
def test_admin_login_ignores_email_case(client, typed):
    User.objects.create_superuser('Ana.Garcia@example.com', PASSWORD)

    response = client.post('/admin/login/?next=/admin/', {'username': typed, 'password': PASSWORD})

    assert response.status_code == 302
    assert response['Location'] == '/admin/'


@pytest.mark.django_db
def test_createsuperuser_asks_for_email(monkeypatch):
    monkeypatch.setenv('DJANGO_SUPERUSER_PASSWORD', PASSWORD)
    call_command('createsuperuser', interactive=False, email='root@example.com', verbosity=0)

    user = User.objects.get()
    assert user.email == 'root@example.com' and user.is_superuser
    assert authenticate(username='root@example.com', password=PASSWORD) == user


@pytest.mark.django_db
def test_admin_login_by_email(client):
    User.objects.create_superuser('root@example.com', PASSWORD)

    response = client.post('/admin/login/?next=/admin/',
                           {'username': 'root@example.com', 'password': PASSWORD})

    assert response.status_code == 302
    assert response['Location'] == '/admin/'
    assert client.get('/admin/').status_code == 200


@pytest.mark.django_db
def test_admin_user_pages(admin_client):
    """The admin's fieldsets and forms work without `username`."""
    user = User.objects.create_user('ana@example.com', PASSWORD)

    # Search: the exact email, whatever its case (through the blind index).
    changelist = admin_client.get('/admin/accounts/user/?q=ANA@example.com')
    assert changelist.status_code == 200
    assert 'ana@example.com' in changelist.content.decode()
    assert 'ana@example.com' not in admin_client.get('/admin/accounts/user/?q=ana').content.decode()
    assert admin_client.get(f'/admin/accounts/user/{user.pk}/change/').status_code == 200
    assert admin_client.get('/admin/accounts/user/add/').status_code == 200

    response = admin_client.post('/admin/accounts/user/add/', {
        'email': ' Bea@EXAMPLE.com ', 'usable_password': 'true',
        'password1': PASSWORD, 'password2': PASSWORD,
    })
    assert response.status_code == 302, response.context['adminform'].form.errors
    assert User.objects.get_by_natural_key('Bea@example.com').check_password(PASSWORD)

    response = admin_client.post('/admin/accounts/user/add/', {
        'email': 'ANA@example.com', 'usable_password': 'true',
        'password1': PASSWORD, 'password2': PASSWORD,
    })
    assert response.status_code == 200
    assert response.context['adminform'].form.errors['email']


# --- Welcome ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.usefixtures('portal_urls')
def test_welcome_requires_login(client):
    response = client.get('/en/welcome/')
    assert response.status_code == 302
    assert response['Location'] == '/en/login/?next=/en/welcome/'


@pytest.mark.django_db
@pytest.mark.usefixtures('portal_urls')
@pytest.mark.parametrize('lang, text', [('en', 'Welcome to MyTreatPlan'),
                                         ('es', 'Le damos la bienvenida a MyTreatPlan')])
def test_welcome_for_a_signed_in_user(client, lang, text):
    client.force_login(User.objects.create_user('ana@example.com', PASSWORD))

    response = client.get(f'/{lang}/welcome/')

    assert response.status_code == 200
    html = response.content.decode()
    assert text in html and 'ana@example.com' in html


@pytest.mark.django_db
def test_admin_shows_the_step1_details(admin_client):
    """Title, middle name, country, mobile and how-found-us appear on the change page; the
    mobile (E.164, validated by signup step 1) is read-only."""
    user = User.objects.create_user(
        'carla@example.com', PASSWORD, title='dr', first_name='Carla', middle_name='M.',
        last_name='Ruiz Soto', country='ES', phone='+34612345678', how_found='others',
        how_found_other='A colleague at the clinic')

    page = admin_client.get(f'/admin/accounts/user/{user.pk}/change/').content.decode()

    for name in ('title', 'middle_name', 'country', 'how_found', 'how_found_other'):
        assert f'name="{name}"' in page
    assert 'M.' in page and 'A colleague at the clinic' in page
    assert '+34612345678' in page
    assert 'name="phone"' not in page
    changelist = admin_client.get('/admin/accounts/user/').content.decode()
    assert 'Spain' in changelist
