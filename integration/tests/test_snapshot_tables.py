"""Epic-catalogue entry 11: the snapshot-fed tables are written only by the snapshot apply
(integration.snapshot_tables, integration.config._apply). Every model and every ORM write
path is refused outside the apply and allowed inside; a model an applier writes without
the guard fails a test; the marker is per thread and reset by a failing apply; the test
helper (integration/tests/snapshot_rows.py) and the marker are imported by no production
module. The admin site is the fake of integration/tests/test_config.py (no network)."""
import ast
import re
import threading
import uuid
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings as django_settings
from django.contrib import admin as django_admin
from django.db import connection, models, transaction
from django.db.migrations.executor import MigrationExecutor

from catalogue.models import (AddOn, BillingCompany, BillingRule, CountryGroup, Product,
                              ProviderCost, ProviderProduct, Subscription, Voucher)
from catalogue.tests.test_add_ons_costs import ADD_ONS, COSTS
from catalogue.tests.test_billing import billed
from catalogue.tests.test_vouchers import GENERAL
from integration import config
from integration.models import AdminDirectoryEntry, ConfigState
from integration.snapshot_tables import (READ_ONLY_MESSAGE, ReadOnlyTable,
                                         SnapshotFedModel, SnapshotQuerySet,
                                         snapshot_apply_running)
from integration.tests.snapshot_rows import snapshot_writes
from integration.tests.test_config import (admin, interface_settings,  # noqa: F401
                                           snapshot)
from regions.models import Country

# test_config's interface settings (autouse here, as imported) clear the database cache.
pytestmark = pytest.mark.django_db
# Decision (user, 2026-11-12): the nine catalogue tables, the country map and the admin
# directory.
SNAPSHOT_FED = [CountryGroup, Product, Subscription, AddOn, ProviderProduct, ProviderCost,
                BillingCompany, BillingRule, Voucher, Country, AdminDirectoryEntry]
PATHS = ['save', 'delete', 'create', 'get_or_create', 'update_or_create',
         'queryset_update', 'queryset_delete', 'bulk_create', 'bulk_update']


def full_snapshot(version=1):
    """A snapshot with a row for every snapshot-fed table, and a country map that changes
    one country and adds one."""
    section = billed()
    section['add_ons'] = ADD_ONS
    return snapshot(version=version, catalogue=section, provider_costs=COSTS,
                    vouchers=[GENERAL],
                    country_map={'countries': [{'code': 'ES', 'state': 'banned'},
                                               {'code': 'XK', 'state': 'eu'}]})


@pytest.fixture
def applied(admin):  # noqa: F811
    admin.snapshot = full_snapshot()
    assert config.pull() == ('applied', 1)
    for model in SNAPSHOT_FED:
        assert model.objects.exists(), model
    return admin


def _field(model):
    """A concrete, editable field other than the primary key and the foreign keys."""
    return next(field for field in model._meta.concrete_fields
                if not field.primary_key and not field.is_relation)


def _fresh(model):
    """A new row of `model`, a copy of an existing one under a new key (and a new code for
    the voucher, whose code is unique)."""
    row = model.objects.order_by('pk').first()
    if model is Country:
        row.code = 'ZZ'
    elif isinstance(model._meta.pk, models.AutoField):
        row.pk = None
    else:
        row.pk = uuid.uuid4()
    if model is Voucher:
        row.code = 'NEWCODE234'
    row._state.adding = True
    return row


def _values(model):
    return list(model.objects.order_by('pk').values())


def _kwargs(row):
    return {field.attname: getattr(row, field.attname)
            for field in row._meta.concrete_fields}


def _write(model, path):
    """Write `model` through `path`."""
    existing = model.objects.order_by('pk').first()
    field = _field(model)
    if path == 'save':
        existing.save()
    elif path == 'delete':
        existing.delete()
    elif path == 'create':
        model.objects.create(**_kwargs(_fresh(model)))
    elif path == 'get_or_create':
        fresh = _kwargs(_fresh(model))
        model.objects.get_or_create(pk=fresh.pop(model._meta.pk.attname) or None,
                                    defaults=fresh)
    elif path == 'update_or_create':
        model.objects.update_or_create(
            pk=existing.pk, defaults={field.attname: getattr(existing, field.attname)})
    elif path == 'queryset_update':
        model.objects.filter(pk=existing.pk).update(
            **{field.name: getattr(existing, field.attname)})
    elif path == 'queryset_delete':
        model.objects.filter(pk=existing.pk).delete()
    elif path == 'bulk_create':
        model.objects.bulk_create([_fresh(model)])
    elif path == 'bulk_update':
        model.objects.bulk_update([existing], [field.name])


# --- Write outside the apply / inside the apply (the I/O matrix) -------------------------

@pytest.mark.parametrize('path', PATHS)
@pytest.mark.parametrize('model', SNAPSHOT_FED, ids=lambda model: model.__name__)
def test_every_write_path_is_refused_outside_the_apply(applied, model, path):
    before = _values(model)
    with pytest.raises(ReadOnlyTable, match='read-only') as error:
        _write(model, path)
    assert str(error.value) == READ_ONLY_MESSAGE
    assert isinstance(error.value, TypeError)       # what the catalogue raised before
    assert _values(model) == before


@pytest.mark.parametrize('path', PATHS)
@pytest.mark.parametrize('model', SNAPSHOT_FED, ids=lambda model: model.__name__)
def test_every_write_path_works_inside_the_apply(applied, model, path):
    count = model.objects.count()
    with snapshot_writes():
        _write(model, path)
    if path in ('delete', 'queryset_delete'):
        assert model.objects.count() == count - 1
    elif path in ('create', 'get_or_create', 'bulk_create'):
        assert model.objects.count() == count + 1
    else:
        assert model.objects.count() == count
    assert not snapshot_apply_running()


def test_the_apply_replaces_the_rows_as_before(applied):
    applied.snapshot = full_snapshot(version=2)
    applied.snapshot['vouchers'] = []
    applied.snapshot['catalogue']['billing_rules'] = []
    assert config.pull() == ('applied', 2)
    assert not Voucher.objects.exists() and not BillingRule.objects.exists()
    assert Product.objects.exists() and AdminDirectoryEntry.objects.count() == 2


# --- Every table an applier writes is guarded ----------------------------------------------

_WRITE = re.compile(r'^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"?(\w+)"?', re.I)


def test_every_model_an_applier_writes_has_the_guard(admin):  # noqa: F811
    """Fails when an applier (registered or the admin directory) writes a table whose
    model lacks the guard: the tables are those the SQL of a full apply writes while the
    marker is set (the state row is written after it)."""
    tables = set()

    def record(execute, sql, params, many, context):
        match = _WRITE.match(sql)
        if match and snapshot_apply_running():
            tables.add(match.group(1))
        return execute(sql, params, many, context)

    admin.snapshot = full_snapshot()
    with connection.execute_wrapper(record):
        assert config.pull() == ('applied', 1)
    by_table = {model._meta.db_table: model for model in apps.get_models()}
    written = {by_table[table] for table in tables}
    assert ConfigState not in written
    assert written == set(SNAPSHOT_FED)
    for model in written:
        assert issubclass(model, SnapshotFedModel), model
        assert isinstance(model.objects.all(), SnapshotQuerySet), model
        assert isinstance(model._default_manager.all(), SnapshotQuerySet), model


def test_no_snapshot_fed_model_is_in_the_django_admin():
    for model in SNAPSHOT_FED:
        assert not django_admin.site.is_registered(model), model


def test_every_snapshot_fed_model_is_listed():
    """A new subclass of the guard is added to SNAPSHOT_FED (and so to the tests above)."""
    guarded = {model for model in apps.get_models() if issubclass(model, SnapshotFedModel)}
    assert guarded == set(SNAPSHOT_FED)


# --- The marker ---------------------------------------------------------------------------

def test_a_failing_apply_resets_the_marker(applied, monkeypatch):
    def failing(section):
        assert snapshot_apply_running()
        raise RuntimeError('disk full')
    monkeypatch.setitem(config._SECTION_APPLIERS, 'vouchers', failing)
    applied.snapshot = full_snapshot(version=2)
    with pytest.raises(config.PullFailed) as error:
        config.pull()
    assert error.value.code == 'apply_failed'
    assert not snapshot_apply_running()
    with pytest.raises(ReadOnlyTable):
        Voucher.objects.all().delete()
    assert Voucher.objects.count() == 1


def test_the_marker_is_per_thread():
    seen = {}

    def other():
        seen['other'] = snapshot_apply_running()
    with snapshot_writes():
        assert snapshot_apply_running()
        thread = threading.Thread(target=other)
        thread.start()
        thread.join()
    assert seen == {'other': False}
    assert not snapshot_apply_running()


def test_an_error_inside_the_block_resets_the_marker():
    with pytest.raises(ValueError):
        with snapshot_writes():
            raise ValueError
    assert not snapshot_apply_running()


def test_the_marker_is_set_only_while_apply_runs(admin, monkeypatch):  # noqa: F811
    """Not before the version check (a stale or unchanged pull writes nothing), and not
    after the apply."""
    seen = []
    monkeypatch.setitem(config._SECTION_APPLIERS, 'vouchers',
                        lambda section: seen.append(snapshot_apply_running()))
    admin.snapshot = full_snapshot()
    config.pull()
    assert seen == [True] and not snapshot_apply_running()


# --- Migrations: the historical models are not guarded ---------------------------------------

def test_the_historical_models_of_migrations_are_not_guarded():
    executor = MigrationExecutor(connection)
    state = executor.loader.project_state()
    for model in SNAPSHOT_FED:
        historical = state.apps.get_model(model._meta.app_label, model._meta.model_name)
        assert not issubclass(historical, SnapshotFedModel)
        assert type(historical._default_manager.all()) is models.QuerySet
        assert type(historical._base_manager.all()) is models.QuerySet


# --- The base manager and the related managers (review P1) ---------------------------------

# Every reverse foreign-key relation among the guarded models: (parent model, accessor).
REVERSE_RELATIONS = [(CountryGroup, 'subscriptions'), (CountryGroup, 'billing_rules'),
                     (BillingCompany, 'rules'), (ProviderProduct, 'costs')]


def test_every_reverse_relation_is_listed():
    """A new foreign key between guarded models is added to REVERSE_RELATIONS."""
    found = {(model, field.get_accessor_name())
             for model in SNAPSHOT_FED for field in model._meta.get_fields()
             if field.one_to_many and field.auto_created}
    assert found == set(REVERSE_RELATIONS)


def _reparent(parent_model, accessor):
    """A child of another parent, and the parent it is moved to."""
    manager_field = parent_model._meta.get_field(accessor)
    child_model, fk = manager_field.related_model, manager_field.field
    child = child_model.objects.order_by('pk').first()
    other = parent_model.objects.exclude(pk=getattr(child, fk.attname)).order_by('pk').first()
    assert other is not None, (parent_model, accessor)
    return child, other, fk


@pytest.mark.parametrize('method', ['add', 'add_unbulked', 'set'])
@pytest.mark.parametrize('parent_model, accessor', REVERSE_RELATIONS,
                         ids=lambda value: getattr(value, '__name__', value))
def test_a_related_manager_is_refused_outside_the_apply(applied, parent_model, accessor,
                                                        method):
    child, other, fk = _reparent(parent_model, accessor)
    before = getattr(child, fk.attname)
    related = getattr(other, accessor)

    def write():
        if method == 'add':
            related.add(child)
        elif method == 'add_unbulked':
            related.add(child, bulk=False)
        else:
            related.set([*related.all(), child])
    # A savepoint: `add(bulk=False)` writes in an atomic block without one, so its
    # refusal would otherwise leave the test's transaction unusable.
    with pytest.raises(ReadOnlyTable), transaction.atomic():
        write()
    child.refresh_from_db()
    assert getattr(child, fk.attname) == before
    with snapshot_writes():
        write()
    child.refresh_from_db()
    assert getattr(child, fk.attname) == other.pk


@pytest.mark.parametrize('model', SNAPSHOT_FED, ids=lambda model: model.__name__)
def test_the_base_manager_is_guarded(applied, model):
    assert isinstance(model._base_manager.all(), SnapshotQuerySet)
    row = model.objects.order_by('pk').first()
    field = _field(model)
    before = _values(model)
    with pytest.raises(ReadOnlyTable):
        model._base_manager.filter(pk=row.pk).update(
            **{field.name: getattr(row, field.attname)})
    with pytest.raises(ReadOnlyTable):
        model._base_manager.all().delete()
    with pytest.raises(ReadOnlyTable):
        model._base_manager.bulk_create([_fresh(model)])
    assert _values(model) == before


def test_reads_through_the_base_manager_still_work(applied):
    """refresh_from_db and related-object access read through the base manager."""
    rule = BillingRule.objects.select_related(None).order_by('pk').first()
    rule.refresh_from_db()
    assert rule.company.rules.filter(pk=rule.pk).exists()
    assert rule.country_group.billing_rules.count() >= 1
    cost = ProviderCost.objects.order_by('pk').first()
    assert cost.product.costs.filter(pk=cost.pk).exists()
    subscription = Subscription.objects.order_by('pk').first()
    assert subscription.country_group.subscriptions.filter(pk=subscription.pk).exists()


def test_saves_and_cascading_deletes_inside_the_apply_still_work(applied):
    group = CountryGroup.objects.get(pk=Subscription.objects.first().country_group_id)
    with snapshot_writes():
        group.name = 'Renamed'
        group.save()
        group.refresh_from_db()
        assert group.name == 'Renamed'
        group.delete()           # cascades to its subscriptions and rules
    assert not Subscription.objects.filter(country_group_id=group.pk).exists()
    assert not BillingRule.objects.filter(country_group_id=group.pk).exists()


# --- The test helper and the marker stay out of production code ---------------------------

PRODUCTION_ROOTS = ('mytreatplan', 'mytp_publicsite', 'accounts', 'eventlog', 'signup',
                    'legal', 'storage', 'regions', 'integration', 'catalogue')
# The only production modules that may name the marker.
MARKER_USERS = {'integration/config.py', 'integration/snapshot_tables.py'}


def _production_modules():
    base = Path(django_settings.BASE_DIR)
    for root in PRODUCTION_ROOTS:
        for path in sorted((base / root).rglob('*.py')):
            parts = path.relative_to(base).parts
            if 'tests' in parts or 'migrations' in parts:
                continue
            yield path.relative_to(base).as_posix(), ast.parse(path.read_text())


def test_production_code_never_imports_a_tests_package_or_sets_the_marker():
    """How the helper stays test-only: it lives in a `tests` package, and no module outside
    the `tests` packages imports one (nor sets the marker, but integration.config)."""
    modules = list(_production_modules())
    assert any(name == 'integration/config.py' for name, _tree in modules)
    for name, tree in modules:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ''] + [alias.name for alias in node.names]
            else:
                imported = []
            for target in imported:
                assert 'tests' not in target.split('.'), (name, target)
                assert 'snapshot_rows' not in target, (name, target)
            if name not in MARKER_USERS:
                for target in imported:
                    assert target != '_snapshot_apply', name
                if isinstance(node, (ast.Name, ast.Attribute)):
                    assert getattr(node, 'id', getattr(node, 'attr', None)) not in (
                        '_snapshot_apply', '_applying'), name


def test_the_helpers_write_the_country_map_as_the_apply_does():
    from integration.tests.snapshot_rows import ensure_country, put_country
    assert put_country('XK', 'banned').state == 'banned'
    assert ensure_country('XK', 'eu').state == 'banned'      # kept: it exists
    assert ensure_country('ZZ', 'eu').state == 'eu'          # created
    assert not snapshot_apply_running()
    with pytest.raises(ReadOnlyTable):
        Country.objects.filter(code='ZZ').delete()


def test_raw_deserialisation_is_not_guarded(applied):
    """loaddata and serialized_rollback save with `save_base(raw=True)`: not guarded, on
    purpose (review P2; the module docstring and the README say so)."""
    from django.core import serializers
    rows = [Country.objects.get(code='XK'), Product.objects.order_by('pk').first()]
    text = serializers.serialize('json', rows)
    with snapshot_writes():
        Country.objects.filter(code='XK').delete()
    for item in serializers.deserialize('json', text):
        item.save()
    assert Country.objects.get(code='XK').state == 'eu'
