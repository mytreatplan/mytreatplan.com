"""The accounts tests' shared fixtures (their flow helpers are in helpers.py).

Signup's `capture_codes` and `clear_cache` (signup/tests/conftest.py) are autouse there;
here they are opt-in, for the modules that name them in `pytestmark`
(`pytest.mark.usefixtures`): an empty cache around every accounts test would break the
tests that end in an IntegrityError (test_crypto.py)."""
import pytest
from django.contrib.auth import get_user_model

from eventlog.models import Attempt

from signup.tests.conftest import cache_cleared, clock  # noqa: F401 - clock: fixture
from signup.tests.conftest import codes_captured
from signup.tests.helpers import NORMALIZED, PASSWORD, _complete

User = get_user_model()


@pytest.fixture
def capture_codes(caplog):
    """signup's `capture_codes`, opt-in: caplog sees the fake provider's code lines."""
    yield from codes_captured(caplog)


@pytest.fixture
def clear_cache(request):
    """signup's `clear_cache`, opt-in: a database test starts with an empty cache (the
    limits, the login counters and the fake provider's codes live there)."""
    yield from cache_cleared(request)


@pytest.fixture
def doctor(client):
    """A Doctor created by signup (the whole wizard), then logged out. The login record of
    signup's auto-login (QA A2, tested in test_login_records.py) is removed, so a test
    sees only the records of what it does."""
    response = _complete(client)
    assert response['Location'] == '/en/welcome/'
    client.logout()
    user = User.objects.by_email(NORMALIZED).get()
    Attempt.objects.filter(kind=Attempt.LOGIN, account_id=user.pk).delete()
    return user


@pytest.fixture
def staff():
    return User.objects.create_superuser('staff@example.net', PASSWORD)


@pytest.fixture
def login_clock(clock, monkeypatch):  # noqa: F811
    """The signup clock (limits, fake provider), also seen by the pending login."""
    monkeypatch.setattr('accounts.views._now', clock)
    return clock


@pytest.fixture
def lock_clock(login_clock, monkeypatch):
    """The login clock, also seen by the failure counters and the locks."""
    monkeypatch.setattr('accounts.login_failures._now', login_clock)
    return login_clock


@pytest.fixture
def idle_clock(clock, monkeypatch):  # noqa: F811
    """The signup clock (limits, fake provider), also the sessions' idle clock."""
    monkeypatch.setattr('accounts.sessions._now', clock)
    return clock
