"""The catalogue tests' fixtures: every module pulls the configuration snapshot from the
fake admin site (integration/tests/snapshot_rows.py; the root conftest's `admin`)."""
import pytest

from regions.profiles import get_profile


@pytest.fixture(autouse=True)
def interface_settings(interface_settings):
    """The root conftest's interface settings, for every catalogue test."""
    return interface_settings


@pytest.fixture
def ae_portal(settings):
    """This portal is the ae region's (dev-ae): the country map's ae countries are its
    own (epic-catalogue entry 16: a portal answers only for the countries it serves)."""
    settings.REGION = 'ae'
    settings.REGION_PROFILE = get_profile('ae')
    return settings
