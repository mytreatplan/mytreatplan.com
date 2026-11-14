"""The catalogue tests' fixtures: every module pulls the configuration snapshot from the
fake admin site (integration/tests/snapshot_rows.py; the root conftest's `admin`). `ae_portal`
is the root conftest's."""
import pytest


@pytest.fixture(autouse=True)
def interface_settings(interface_settings):
    """The root conftest's interface settings, for every catalogue test."""
    return interface_settings
