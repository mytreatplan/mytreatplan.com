"""The subscriptions tests' fixtures: the configuration snapshot from the fake admin site
(integration/tests/snapshot_rows.py; the root conftest's `admin` and
`interface_settings`), and signup's code capture and empty cache for the tests that sign
a doctor up."""
import pytest

from signup.tests.conftest import cache_cleared, codes_captured


@pytest.fixture(autouse=True)
def interface_settings_for_db_tests(request):
    """The root conftest's interface settings, for every subscriptions database test."""
    if request.node.get_closest_marker('django_db') is not None:
        request.getfixturevalue('interface_settings')


@pytest.fixture
def capture_codes(caplog):
    yield from codes_captured(caplog)


@pytest.fixture
def clear_cache(request):
    yield from cache_cleared(request)
