"""Epic-regions 2.6 (R5): each legal document belongs to a region and every instance reads,
numbers and offers only its own region's; documents carry a `provisional` flag. One test
per row of the plan's matrix that concerns the documents (the signup and public pages
have theirs in signup/tests and mytp_publicsite/tests)."""
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from legal.models import LegalChoice, LegalDocument

User = get_user_model()
KINDS = ['terms', 'conditions', 'ormco_tps', 'commercial_consent']
ADMIN = '/admin/legal/legaldocument/'

BEFORE = [('legal', '0005_uid_unique')]


def _document(kind='terms', region=None, published_at=None, **fields):
    """A published document of `kind`, in `region` (the site's when None)."""
    document = LegalDocument(
        kind=kind, title_en=fields.pop('title_en', f'{kind} {region or "site"}'),
        title_es='t', body_en='b', body_es='b',
        published_at=published_at or timezone.now(), **fields)
    if region:
        document.region = region
    document.save()
    return document


# --- New rows ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_a_new_document_is_the_sites_and_provisional():
    document = _document()
    assert document.region == 'eu' and document.provisional is True


@pytest.mark.django_db
def test_a_new_document_on_the_ae_site_is_ae(ae_portal):
    assert _document().region == 'ae'


@pytest.mark.django_db
def test_the_version_follows_the_region_and_kind():
    """The highest of this region and kind + 1; another region numbers its own."""
    assert _document('terms').version == 2  # after the migration's v1 (eu)
    assert _document('terms', region='ae').version == 1
    assert _document('terms', region='ae').version == 2
    assert _document('terms').version == 3


@pytest.mark.django_db
def test_the_same_kind_and_version_may_exist_in_another_region():
    _document('terms', region='ae', version=1)
    assert LegalDocument.objects.filter(kind='terms', version=1).count() == 2


@pytest.mark.django_db
def test_a_duplicate_in_the_same_region_is_refused():
    with pytest.raises(IntegrityError), transaction.atomic():
        _document('terms', version=1)


def _insert_without_region(version, published_at=None):
    """A terms row as the previous release (no `region`, no `provisional`) inserts it."""
    table = LegalDocument._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            f'INSERT INTO {table} (kind, version, title_en, title_es, body_en, body_es, '
            "published_at, created_at) VALUES ('terms', %s, 'Raw', 't', 'b', 'b', %s, %s)",
            [version, published_at, timezone.now()])
    return LegalDocument.objects.get(kind='terms', version=version, region='')


@pytest.mark.django_db
def test_a_row_inserted_without_a_region_is_the_sites_and_offered():
    """The previous release inserts while a deploy migrates, or after the fill: the row
    takes the column defaults ('' and provisional) and is read as the site's own."""
    row = _insert_without_region(2, published_at=timezone.now())
    assert row.provisional is True
    assert LegalDocument.objects.current('terms') == row
    assert LegalDocument.objects.current_by_kind()['terms'] == row
    assert row in LegalDocument.objects.of_site()


@pytest.mark.django_db
def test_the_next_version_counts_a_row_without_a_region():
    _insert_without_region(2)
    assert _document('terms').version == 3
    assert _document('terms', region='ae').version == 1  # not another region's


@pytest.mark.django_db
def test_saving_a_row_without_a_region_gives_it_the_sites():
    row = _insert_without_region(2)
    row.save()
    row.refresh_from_db()
    assert row.region == 'eu'


# --- Reading: only the site's region ----------------------------------------------------

@pytest.mark.django_db
def test_only_the_sites_region_is_current():
    ae = {kind: _document(kind, region='ae') for kind in KINDS}
    _document('terms', region='ae')  # ae v2: a later version elsewhere changes nothing

    current = LegalDocument.objects.current_by_kind()
    assert sorted(current) == sorted(KINDS)
    assert {document.region for document in current.values()} == {'eu'}
    assert LegalDocument.objects.current('terms').version == 1
    assert not set(current.values()) & set(ae.values())


@pytest.mark.django_db
def test_the_ae_site_reads_only_its_own(ae_portal):
    assert LegalDocument.objects.current_by_kind() == {}  # the drafts are eu's
    terms = _document('terms')
    assert LegalDocument.objects.current('terms') == terms
    assert LegalDocument.objects.current_by_kind() == {'terms': terms}


# --- Admin ------------------------------------------------------------------------------

@pytest.fixture
def admin_client(client, db):
    admin = User.objects.create_superuser('admin@example.com', 'violet-orbit-canyon-42')
    client.force_login(admin)
    return client


def test_admin_lists_and_opens_only_the_sites_documents(admin_client):
    ae = _document('terms', region='ae', title_en='Other region terms',
                   published_at=timezone.now() + timedelta(days=1))
    html = admin_client.get(ADMIN).content.decode()
    assert 'Other region terms' not in html
    assert admin_client.get(f'{ADMIN}{ae.pk}/change/').status_code == 302  # "not found"
    assert admin_client.post(f'{ADMIN}{ae.pk}/delete/', {'post': 'yes'}).status_code == 302
    assert LegalDocument.objects.filter(pk=ae.pk).exists()


def test_admin_lists_a_row_without_a_region(admin_client):
    _insert_without_region(2)
    assert 'Raw' in admin_client.get(ADMIN).content.decode()


def test_admin_status_reads_the_sites_current(admin_client):
    _document('terms', region='ae')  # a later version elsewhere does not supersede v1
    html = admin_client.get(f'{ADMIN}?kind__exact=terms').content.decode()
    assert 'superseded' not in html and 'current' in html


def test_admin_adds_a_final_version_in_the_sites_region(admin_client):
    response = admin_client.post(f'{ADMIN}add/', {
        'kind': 'terms', 'title_en': 'Terms of Use', 'title_es': 'Términos de uso',
        'body_en': 'Final', 'body_es': 'Final', 'published_at_0': '2000-01-01',
        'published_at_1': '00:00:00'})  # `provisional` unticked
    assert response.status_code == 302, response.context['adminform'].form.errors
    document = LegalDocument.objects.get(kind='terms', version=2)
    assert document.region == 'eu' and document.provisional is False
    assert LegalDocument.objects.current('terms') == document


def test_admin_shows_provisional_as_editable_on_a_draft(admin_client):
    draft = _document('terms', published_at=timezone.now() + timedelta(days=1))
    html = admin_client.get(f'{ADMIN}{draft.pk}/change/').content.decode()
    assert 'name="provisional"' in html and 'name="region"' not in html


# --- The migrations ---------------------------------------------------------------------

def _migrate_with_acceptances(region):
    """Migrate legal back to 0005, add documents and acceptances as rows from before,
    migrate forward (every document gets `region`), back and forward again: every
    acceptance points at the same document throughout."""
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    old = executor.loader.project_state(BEFORE).apps
    try:
        OldDocument = old.get_model('legal', 'LegalDocument')
        OldChoice = old.get_model('legal', 'LegalChoice')
        bodies = {d.pk: (d.title_en, d.body_en, d.body_es)
                  for d in OldDocument.objects.all()}
        # legal 0002's drafts when present (a transactional test before flushes them),
        # and two more versions.
        for version in (900, 901):
            extra = OldDocument.objects.create(kind='terms', version=version,
                                               title_en=f'T{version}', title_es='T',
                                               body_en='b', body_es='b',
                                               published_at=timezone.now())
            bodies[extra.pk] = (f'T{version}', 'b', 'b')
        users = [User.objects.create_user(f'old{i}@example.com', 'violet-orbit-canyon-42')
                 for i in range(2)]
        for user in users:
            for document in OldDocument.objects.all():
                OldChoice.objects.create(document_id=document.pk, accepted=True,
                                         decided_at=timezone.now(), user_id=user.pk)
        accepted = sorted(OldChoice.objects.values_list('uid', 'document_id'))

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        documents = LegalDocument.objects.all()
        assert {d.region for d in documents} == {region}
        assert all(d.provisional for d in documents)
        # Nothing rewritten: the seeded drafts keep their text, whatever the company.
        assert {d.pk: (d.title_en, d.body_en, d.body_es) for d in documents} == bodies
        assert sorted(LegalChoice.objects.values_list('uid', 'document_id')) == accepted

        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        old = executor.loader.project_state(BEFORE).apps
        assert sorted(old.get_model('legal', 'LegalChoice').objects
                      .values_list('uid', 'document_id')) == accepted
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
    assert sorted(LegalChoice.objects.values_list('uid', 'document_id')) == accepted


@pytest.mark.django_db(transaction=True)
def test_migrations_give_existing_documents_the_sites_region_and_keep_acceptances():
    _migrate_with_acceptances('eu')


@pytest.mark.django_db(transaction=True)
def test_on_the_ae_site_existing_documents_become_its_placeholders(ae_portal):
    _migrate_with_acceptances('ae')


@pytest.mark.django_db(transaction=True)
def test_a_row_inserted_between_0006_and_0007_is_filled():
    executor = MigrationExecutor(connection)
    executor.migrate([('legal', '0006_region_provisional_add')])
    try:
        _insert_without_region(950)
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
    assert LegalDocument.objects.get(kind='terms', version=950).region == 'eu'
