"""Global ids on records that cross sites (AD-5, story 1.8; mytreatplan.uids).

Every model marked `CROSSES_SITES = True` has a UUID primary key or an immutable, unique
`uid` UUID; part-1 tables gained their `uid` through three migrations per app (add
nullable, fill, make unique), which give every existing row its own value.
"""
import uuid
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection, models
from django.db.migrations.executor import MigrationExecutor
from django.forms import modelform_factory
from django.test.utils import isolate_apps
from django.utils import timezone

from accounts.models import User
from eventlog.models import Attempt
from legal.models import LegalChoice, LegalDocument
from mytp_publicsite.models import ContactMessage
from mytreatplan.uids import crossing_site_problems, fill_uids
from signup.models import PendingRegistration

UID_MODELS = (User, ContactMessage, LegalDocument, LegalChoice)
UUID_PK_MODELS = (Attempt, PendingRegistration)

# The migration before each app's fill step, and the app's latest migration.
BEFORE_FILL = [('accounts', '0009_uid_add'), ('legal', '0003_uid_add'),
               ('mytp_publicsite', '0002_uid_add')]


# --- The convention -------------------------------------------------------------------

def test_every_model_marked_as_crossing_has_a_global_id():
    assert crossing_site_problems(apps.get_models()) == []


def test_the_part1_crossing_models_are_marked():
    marked = {model for model in apps.get_models() if getattr(model, 'CROSSES_SITES', False)}
    assert set(UID_MODELS) | set(UUID_PK_MODELS) <= marked


@pytest.mark.parametrize('model', UID_MODELS, ids=lambda m: m._meta.label)
def test_part1_tables_keep_their_integer_key(model):
    assert isinstance(model._meta.pk, (models.AutoField, models.BigAutoField))


@isolate_apps('mytp_publicsite')
def test_convention_names_a_crossing_model_without_global_id():
    class NoUid(models.Model):
        CROSSES_SITES = True

        class Meta:
            app_label = 'mytp_publicsite'

    class EditableUid(models.Model):
        CROSSES_SITES = True
        uid = models.UUIDField(default=uuid.uuid4, unique=True)

        class Meta:
            app_label = 'mytp_publicsite'

    class NotUniqueUid(models.Model):
        CROSSES_SITES = True
        uid = models.UUIDField(default=uuid.uuid4, editable=False)

        class Meta:
            app_label = 'mytp_publicsite'

    class NotCrossing(models.Model):
        class Meta:
            app_label = 'mytp_publicsite'

    class UuidKey(models.Model):
        CROSSES_SITES = True
        id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

        class Meta:
            app_label = 'mytp_publicsite'

    problems = crossing_site_problems([NoUid, EditableUid, NotUniqueUid, NotCrossing,
                                       UuidKey])

    assert len(problems) == 3
    assert 'mytp_publicsite.NoUid crosses sites' in problems[0]
    assert problems[1] == 'mytp_publicsite.EditableUid.uid must not be editable.'
    assert problems[2] == 'mytp_publicsite.NotUniqueUid.uid must be unique and not null.'


# --- New rows and edits ---------------------------------------------------------------

def _rows():
    user = User.objects.create_user(email='uid@example.com', password='x-Long-passw0rd!')
    other = User.objects.create_user(email='uid2@example.com', password='x-Long-passw0rd!')
    message = ContactMessage.objects.create(first_name='Ana', surname='Ruiz', country='Spain',
                                            phone='+34600000000', email='ana@example.com')
    document = LegalDocument.objects.create(kind=LegalDocument.Kind.TERMS, title_en='T',
                                            title_es='T', body_en='b', body_es='b')
    choice = LegalChoice.objects.create(document=document, accepted=True,
                                        decided_at=timezone.now(), user=user)
    return [user, other, message, document, choice]


@pytest.mark.django_db
def test_a_new_row_gets_its_own_uid():
    rows = _rows()

    uids = [row.uid for row in rows]
    assert all(isinstance(uid, uuid.UUID) for uid in uids)
    assert len(set(uids)) == len(uids)
    for row in rows:
        row.refresh_from_db()
    assert [row.uid for row in rows] == uids


@pytest.mark.django_db
def test_saving_again_keeps_the_uid():
    rows = _rows()
    before = {row._meta.label: type(row).objects.values_list('uid', flat=True).get(pk=row.pk)
              for row in rows[1:]}
    user = rows[0]
    uid = user.uid

    user.first_name = 'Changed'
    user.save()
    user.save(update_fields=['first_name'])
    for row in rows[1:]:
        row.save()

    user.refresh_from_db()
    assert user.uid == uid
    for row in rows[1:]:
        assert type(row).objects.values_list('uid', flat=True).get(pk=row.pk) == \
            before[row._meta.label]


@pytest.mark.parametrize('model', UID_MODELS, ids=lambda m: m._meta.label)
def test_uid_is_never_in_a_form(model):
    form_class = modelform_factory(model, fields='__all__')
    assert 'uid' not in form_class.base_fields


@pytest.mark.parametrize('model', UID_MODELS, ids=lambda m: m._meta.label)
def test_uid_is_read_only_in_the_admin(model, rf, admin_user):
    from django.contrib import admin

    model_admin = admin.site._registry[model]
    request = rf.get('/')
    request.user = admin_user

    assert 'uid' in model_admin.get_readonly_fields(request, model())
    assert 'uid' not in model_admin.get_form(request, None).base_fields


@pytest.mark.django_db
def test_an_insert_without_uid_gets_its_own_from_the_database():
    """The previous release (no `uid` in its model) still inserts while a deploy migrates
    or after a rollback: the database default gives each such row its own uid."""
    message = ContactMessage._meta.db_table
    document = LegalDocument._meta.db_table
    now = timezone.now()
    with connection.cursor() as cursor:
        for i in range(3):
            cursor.execute(
                f'INSERT INTO {message} (first_name, middle_name, surname, country, phone, '
                'email, aligners, club, decision, sent_at, mail_status) '
                "VALUES ('a', '', 'b', 'c', 'd', 'e', '', '', '', %s, 'pending')", [now])
            cursor.execute(
                f'INSERT INTO {document} (kind, version, title_en, title_es, body_en, body_es, '
                "created_at) VALUES ('terms', %s, 't', 't', 'b', 'b', %s)", [800 + i, now])

    for model in (ContactMessage, LegalDocument):
        uids = list(model.objects.values_list('uid', flat=True))
        assert len(uids) >= 3
        assert None not in uids
        assert len(set(uids)) == len(uids), model


# --- Existing rows: the migrations -----------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_migrating_existing_rows_gives_each_its_own_uid():
    """Rows that existed before the uid (as on staging) each get a distinct one."""
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE_FILL)
    old = executor.loader.project_state(BEFORE_FILL).apps
    try:
        OldUser = old.get_model('accounts', 'User')
        OldMessage = old.get_model('mytp_publicsite', 'ContactMessage')
        OldDocument = old.get_model('legal', 'LegalDocument')
        OldChoice = old.get_model('legal', 'LegalChoice')
        users = [OldUser.objects.create(email=f'old{i}@example.com', email_index=f'old{i}',
                                        password='!')
                 for i in range(3)]
        for i in range(3):
            OldMessage.objects.create(first_name='A', surname='B', country='C', phone='1',
                                      email=f'm{i}@example.com')
        for version in (900, 901):
            OldDocument.objects.create(kind='terms', version=version, title_en='T',
                                       title_es='T', body_en='b', body_es='b')
        documents = list(OldDocument.objects.all())
        for user in users:
            for document in documents:
                OldChoice.objects.create(document=document, accepted=True,
                                         decided_at=timezone.now(), user=user)
        # The add step's database default already gave these rows a uid (and legal 0002's
        # versions kept theirs): clear them all, as rows read by a release without it.
        for model in (OldUser, OldMessage, OldDocument, OldChoice):
            model.objects.update(uid=None)
            assert set(model.objects.values_list('uid', flat=True)) == {None}

        # The fill step works in batches: smaller ones than the rows still fill them all.
        fill_uids('mytp_publicsite', 'contactmessage', batch_size=2)(
            old, SimpleNamespace(connection=connection))
        filled = list(OldMessage.objects.values_list('uid', flat=True))
        assert None not in filled and len(set(filled)) == len(filled) == 3
        OldMessage.objects.update(uid=None)
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    for model in UID_MODELS:
        uids = list(model.objects.values_list('uid', flat=True))
        assert uids, model
        assert None not in uids
        assert len(set(uids)) == len(uids), model
