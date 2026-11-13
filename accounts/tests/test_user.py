"""accounts.User: email as the username, its manager, admin and the welcome page."""
import importlib
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.forms import modelform_factory

from integration.tests.snapshot_rows import put_country
from regions.profiles import PROFILES, REGION_CHOICES, get_profile

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


# --- Home region (R2 / AD-14, epic-regions 2.2) -----------------------------------------

BEFORE_HOME_REGION = [('accounts', '0011_uid_unique')]
HOME_REGION_ADDED = [('accounts', '0012_home_region_add')]
fill_home_regions = importlib.import_module(
    'accounts.migrations.0013_home_region_fill').fill_home_regions


@pytest.fixture(params=sorted(PROFILES))
def region(request, settings):
    """Each instance region in turn: its code, with settings.REGION_PROFILE set to it."""
    settings.REGION = request.param
    settings.REGION_PROFILE = get_profile(request.param)
    return request.param


def test_home_region_allows_only_the_profile_codes():
    field = User._meta.get_field('home_region')
    assert [tuple(choice) for choice in field.choices] == list(REGION_CHOICES)
    assert [code for code, _label in REGION_CHOICES] == list(PROFILES)
    assert not field.editable
    # Region-neutral in the database: the migration state is the same on every instance.
    assert field.db_default == ''


@pytest.mark.django_db
def test_a_new_user_gets_the_instance_region_whatever_its_country(region):
    user = User.objects.create_user('ana@example.com', PASSWORD, country='ES')
    admin = User.objects.create_superuser('admin@example.com', PASSWORD)

    for created in (user, admin):
        assert created.home_region == region
        created.refresh_from_db()
        assert created.home_region == region


@pytest.mark.django_db
def test_createsuperuser_gets_the_instance_region(region, monkeypatch):
    monkeypatch.setenv('DJANGO_SUPERUSER_PASSWORD', PASSWORD)
    call_command('createsuperuser', '--noinput', '--email', 'root@example.com', verbosity=0)

    assert User.objects.by_email('root@example.com').get().home_region == region


@pytest.mark.django_db
def test_home_region_is_never_recomputed(settings):
    settings.REGION_PROFILE = get_profile('eu')
    user = User.objects.create_user('ana@example.com', PASSWORD, country='AE')
    # The country's state on the map changes, and the account is saved on another
    # instance region: the home region stays.
    put_country('AE', 'banned')
    settings.REGION_PROFILE = get_profile('ae')
    user.first_name = 'Ana'
    user.save()

    user.refresh_from_db()
    assert user.home_region == 'eu'


def test_home_region_is_never_in_a_form():
    assert 'home_region' not in modelform_factory(User, fields='__all__').base_fields


@pytest.mark.django_db
def test_home_region_is_read_only_in_the_admin(rf, admin_user, admin_client):
    from django.contrib import admin

    model_admin = admin.site._registry[User]
    request = rf.get('/')
    request.user = admin_user
    assert 'home_region' in model_admin.get_readonly_fields(request, admin_user)
    assert 'home_region' not in model_admin.get_form(request, admin_user).base_fields

    page = admin_client.get(f'/admin/accounts/user/{admin_user.pk}/change/').content.decode()
    assert 'Home region' in page and 'name="home_region"' not in page


def _insert_without_home_region(email):
    """Insert a user as the previous release would: every column but `home_region`."""
    user = User(email=email, password='!')
    user.set_blind_indexes()
    fields = [field for field in User._meta.concrete_fields
              if field.name not in ('id', 'home_region')]
    columns = ', '.join(connection.ops.quote_name(field.column) for field in fields)
    values = [field.get_db_prep_save(field.pre_save(user, True), connection) for field in fields]
    with connection.cursor() as cursor:
        cursor.execute(f'INSERT INTO {User._meta.db_table} ({columns}) '
                       f'VALUES ({", ".join(["%s"] * len(fields))})', values)
    return User.objects.by_email(email).get()


@pytest.mark.django_db
def test_an_insert_without_home_region_gets_blank_and_the_fill_sets_it(region):
    """The previous release inserts without the column: the database default ('') applies,
    and the fill (re-run if needed) gives such rows the instance region, and only them."""
    kept = User.objects.create_user('kept@example.com', PASSWORD)
    User.objects.filter(pk=kept.pk).update(home_region=next(
        code for code in PROFILES if code != region))

    old = _insert_without_home_region('old@example.com')
    assert old.home_region == ''

    fill_home_regions(apps, SimpleNamespace(connection=connection))

    old.refresh_from_db()
    kept.refresh_from_db()
    assert old.home_region == region
    assert kept.home_region != region


@pytest.mark.django_db(transaction=True)
def test_migrating_existing_rows_gives_them_the_instance_region(settings):
    """Rows created before the field, and rows the previous release inserts between the
    add and the fill, read the instance region once the fill has run (ae on dev-ae)."""
    settings.REGION_PROFILE = get_profile('ae')
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE_HOME_REGION)
    try:
        OldUser = executor.loader.project_state(BEFORE_HOME_REGION).apps.get_model(
            'accounts', 'User')
        for i in range(2):
            OldUser.objects.create(email=f'old{i}@example.com', email_index=f'old{i}',
                                   password='!')

        executor = MigrationExecutor(connection)
        executor.migrate(HOME_REGION_ADDED)
        # The previous release, while the deploy migrates.
        OldUser.objects.create(email='during@example.com', email_index='during', password='!')
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT home_region FROM {User._meta.db_table}')
            assert {row[0] for row in cursor.fetchall()} == {''}
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    regions = list(User.objects.filter(email_index__in=['old0', 'old1', 'during'])
                   .values_list('home_region', flat=True))
    assert regions == ['ae'] * 3
