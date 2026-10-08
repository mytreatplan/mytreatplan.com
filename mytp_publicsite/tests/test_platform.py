import os
import shutil
import subprocess
import sys

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.questioner import NonInteractiveMigrationQuestioner
from django.db.migrations.state import ProjectState


def test_user_model_is_custom():
    assert settings.AUTH_USER_MODEL == 'accounts.User'
    assert get_user_model()._meta.label == 'accounts.User'


def test_no_missing_migrations():
    """Every model change has a migration (same as `makemigrations --check`, without a DB)."""
    loader = MigrationLoader(None, ignore_no_migrations=True)
    autodetector = MigrationAutodetector(
        loader.project_state(),
        ProjectState.from_apps(apps),
        NonInteractiveMigrationQuestioner(),
    )
    changes = autodetector.changes(graph=loader.graph)
    assert changes == {}


def test_accounts_migration_precedes_admin():
    """accounts.0001 must exist and admin must depend on the swappable user model."""
    loader = MigrationLoader(None, ignore_no_migrations=True)
    assert ('accounts', '0001_initial') in loader.graph.nodes
    admin_deps = loader.graph.forwards_plan(('admin', '0001_initial'))
    assert ('accounts', '0001_initial') in admin_deps


def test_missing_secret_key_refuses_to_start(tmp_path):
    """With SECRET_KEY in neither the environment nor settings.env, settings fail naming the key."""
    # Copy the settings package to a directory with no settings.env beside it.
    package = tmp_path / 'mytreatplan'
    package.mkdir()
    shutil.copy(settings.BASE_DIR / 'mytreatplan' / 'settings.py', package / 'settings.py')
    (package / '__init__.py').touch()

    env = {k: v for k, v in os.environ.items() if k not in {'SECRET_KEY', 'DJANGO_SETTINGS_MODULE'}}
    env['PYTHONPATH'] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, '-c', 'import mytreatplan.settings'],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert 'UndefinedValueError' in result.stderr
    assert 'SECRET_KEY' in result.stderr
