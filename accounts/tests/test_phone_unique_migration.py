"""Migration 0017 (epic-doctor-profile entry 3): the unique mobile. It stops, naming the
accounts' ids (never the number), when accounts already share a mobile, and changes
nothing; without duplicates the rule is added (erased accounts and accounts without a
mobile, with a blank index, are left out). Runs on SQLite locally and PostgreSQL in CI."""
import uuid

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from accounts.crypto import email_index, phone_index

BEFORE = [('accounts', '0016_pending_contact_change')]
AFTER = [('accounts', '0017_user_phone_index_unique')]
SHARED = '+34622918374'


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def _latest():
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


def _applied():
    executor = MigrationExecutor(connection)
    return set(executor.loader.applied_migrations)


@pytest.fixture
def at_0016(transactional_db):
    """The schema at 0016 (no rule) for the test, the latest again afterwards (the rows
    the test made are deleted first, so the rule can come back)."""
    apps = _migrate(BEFORE)
    try:
        yield apps
    finally:
        apps.get_model('accounts', 'User').objects.all().delete()
        _migrate(_latest())


def _old_user(apps, email, phone):
    User = apps.get_model('accounts', 'User')
    return User.objects.create(uid=uuid.uuid4(), email=email, email_index=email_index(email),
                               password='!', phone=phone,
                               phone_index=phone_index(phone) if phone else '')


def test_shared_mobiles_stop_the_migration_naming_the_accounts(at_0016):
    first = _old_user(at_0016, 'a@example.com', SHARED)
    second = _old_user(at_0016, 'b@example.com', SHARED)
    _old_user(at_0016, 'c@example.com', '+34622918375')

    with pytest.raises(RuntimeError) as error:
        _migrate(AFTER)

    message = str(error.value)
    assert f'{first.pk}, {second.pk}' in message
    assert SHARED not in message and '622918374' not in message
    assert phone_index(SHARED) not in message
    # Nothing changed: 0017 is not applied, and the rows are as they were.
    assert AFTER[0] not in _applied()
    User = at_0016.get_model('accounts', 'User')
    assert User.objects.filter(phone_index=phone_index(SHARED)).count() == 2


def test_without_duplicates_the_rule_is_added(at_0016):
    _old_user(at_0016, 'a@example.com', SHARED)
    _old_user(at_0016, 'b@example.com', '')
    _old_user(at_0016, 'c@example.com', '')  # several accounts without a mobile

    apps = _migrate(AFTER)

    assert AFTER[0] in _applied()
    with pytest.raises(IntegrityError), transaction.atomic():
        _old_user(apps, 'd@example.com', SHARED)
    _old_user(apps, 'e@example.com', '')
