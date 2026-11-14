"""What keeps the record apart from the catalogue and the service its only writer
(epic-subscriptions entry 1): no database link to the snapshot's rows (an apply never
deletes, blocks on or changes a record), no reverse relation on the catalogue, the change
rows append-only, the purge of erased accounts, and no write path outside
`subscriptions.service` and the backfill migration."""
import ast
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.erasure import erase_user, purge_erased
from catalogue import resolver
from catalogue.models import Subscription
from integration.snapshot_tables import SnapshotFedModel
from integration.tests.snapshot_rows import pull, snapshot
from subscriptions import service
from subscriptions.models import AppendOnly, DoctorSubscription, SubscriptionChange

from .helpers import ADVANCED, PASSWORD, catalogue, doctor, load

User = get_user_model()
MODELS = (DoctorSubscription, SubscriptionChange)


def test_neither_model_is_snapshot_fed_nor_in_the_django_admin():
    for model in MODELS:
        assert not issubclass(model, SnapshotFedModel)
        assert not django_admin.site.is_registered(model)


def test_the_record_has_no_database_link_to_the_catalogue():
    for model in MODELS:
        for field in model._meta.get_fields():
            if field.is_relation:
                assert field.related_model._meta.app_label != 'catalogue', field
    reverse = [field.related_model for field in Subscription._meta.get_fields()
               if field.auto_created and not field.concrete]
    assert not set(reverse) & set(MODELS)


@pytest.mark.django_db
def test_an_apply_leaves_records_holding_unknown_ids(admin):
    load(admin)
    user = doctor()
    service.change(user, expected_version=1, actor=service.SYSTEM_ACTOR, reason='test',
                   tier='advanced', subscription_id=ADVANCED)
    unknown = uuid.uuid4()
    other = doctor('other@example.com')
    service.change(other, expected_version=1, actor=service.SYSTEM_ACTOR, reason='test',
                   tier='advanced', subscription_id=ADVANCED)
    # A row written before this release's checks: an id the catalogue never had.
    DoctorSubscription.objects.filter(doctor=other).update(subscription_id=unknown)
    before = sorted(DoctorSubscription.objects.values_list(
        'doctor_id', 'tier', 'subscription_id', 'version'))
    changes = SubscriptionChange.objects.count()
    assert pull(admin, 2, catalogue(advanced=False)) == ('applied', 2)
    assert pull(admin, 3, {'country_groups': [], 'products': [], 'subscriptions': []}) == (
        'applied', 3)
    admin.snapshot = snapshot(version=4)  # no catalogue section at all: tables emptied
    assert pull(admin, 4) == ('applied', 4)
    assert not Subscription.objects.exists()
    assert sorted(DoctorSubscription.objects.values_list(
        'doctor_id', 'tier', 'subscription_id', 'version')) == before
    assert SubscriptionChange.objects.count() == changes
    assert resolver.subscription_by_id(ADVANCED) is None


@pytest.mark.django_db
def test_the_change_rows_are_append_only():
    user = doctor()
    row = SubscriptionChange.objects.get(record__doctor=user)
    row.reason = 'edited'
    with pytest.raises(AppendOnly):
        row.save()
    with pytest.raises(AppendOnly):
        row.delete()
    with pytest.raises(AppendOnly):
        SubscriptionChange.objects.filter(pk=row.pk).update(reason='edited')
    assert SubscriptionChange.objects.get(pk=row.pk).reason == 'signup'


@pytest.mark.django_db
def test_the_purge_removes_the_record_and_its_changes(settings):
    settings.ACCOUNT_ERASURE_RETENTION_DAYS = 30
    staff = User.objects.create_superuser('staff@example.net', PASSWORD)
    user = doctor()
    kept = doctor('kept@example.com')
    erase_user(user, by=staff, now=timezone.now() - timedelta(days=31))
    assert purge_erased() == 1
    assert not DoctorSubscription.objects.filter(doctor_id=user.pk).exists()
    assert list(SubscriptionChange.objects.values_list('record__doctor', flat=True)) == [
        kept.pk]


@pytest.mark.django_db
def test_both_models_cross_sites_with_a_uuid_key():
    for model in MODELS:
        assert model.CROSSES_SITES and model._meta.pk.get_internal_type() == 'UUIDField'


# --- No write path outside the service ------------------------------------------------------

ALLOWED = {Path('subscriptions/models.py'), Path('subscriptions/service.py')}
OWN_MIGRATIONS = Path('subscriptions/migrations')
MODEL_NAMES = {'DoctorSubscription', 'SubscriptionChange'}
TABLES = {'subscriptions_doctorsubscription', 'subscriptions_subscriptionchange'}


def _production_modules(root):
    """(path relative to `root`, path) of every module of the project's packages (the
    top-level folders with an `__init__.py`), without their tests: other apps'
    migrations included, this app's own left out (they create and fill the tables)."""
    for package in sorted(root.iterdir()):
        if not (package / '__init__.py').exists():
            continue
        for path in sorted(package.rglob('*.py')):
            relative = path.relative_to(root)
            if 'tests' in relative.parts or relative.is_relative_to(OWN_MIGRATIONS):
                continue
            yield relative, path


def _names(tree):
    """(line, name) of every name, attribute and imported name in a module, and of each
    model or table name found in a string constant."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.lineno, node.id
        elif isinstance(node, ast.Attribute):
            yield node.lineno, node.attr
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module:
                yield node.lineno, node.module
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            for name in MODEL_NAMES | TABLES:
                if name.lower() in lowered:
                    yield node.lineno, name


def test_no_module_but_the_service_names_the_models():
    """Only subscriptions.models and subscriptions.service (and this app's migrations)
    name the two models or their tables (as code or in a string), or import
    subscriptions.models: every other module, other apps' migrations included, reads and
    writes through the service."""
    root = Path(settings.BASE_DIR)
    modules = dict(_production_modules(root))
    offenders = [
        f'{relative}:{line}:{name}'
        for relative, path in modules.items() if relative not in ALLOWED
        for line, name in _names(ast.parse(path.read_text(), filename=str(path)))
        if name in MODEL_NAMES | TABLES | {'subscriptions.models', 'subscription_record'}]
    assert offenders == []
    assert Path('subscriptions/service.py') in modules
    assert Path('accounts/migrations/0001_initial.py') in modules


def test_the_scan_finds_a_model_or_table_name_in_a_string():
    tree = ast.parse("SQL = 'DELETE FROM subscriptions_subscriptionchange'\n"
                     "apps.get_model('subscriptions', 'DoctorSubscription')\n")
    assert {name for _line, name in _names(tree)} >= {
        'subscriptions_subscriptionchange', 'DoctorSubscription'}


def test_only_the_backfill_migration_writes_the_records():
    migrations = Path(settings.BASE_DIR) / OWN_MIGRATIONS
    writers = [path.name for path in sorted(migrations.glob('0*.py'))
               if 'RunPython' in path.read_text() or 'RunSQL' in path.read_text()]
    assert writers == ['0002_basic_for_existing_doctors.py']
