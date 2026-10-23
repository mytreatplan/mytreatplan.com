import logging
import time

import pytest


def codes_captured(caplog):
    """Let caplog see the fake provider's code lines (that logger does not propagate).
    The body of `capture_codes` (also accounts/tests/conftest.py's, opt-in there)."""
    logger = logging.getLogger('signup.verification')
    logger.addHandler(caplog.handler)
    yield caplog
    logger.removeHandler(caplog.handler)


def cache_cleared(request):
    """Start a database test with an empty cache (the resend limits and the fake
    provider's codes live there). The body of `clear_cache` (also accounts/tests/
    conftest.py's, opt-in there)."""
    if request.node.get_closest_marker('django_db') is None:
        yield
        return
    from django.core.cache import cache

    request.getfixturevalue('db')
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def capture_codes(caplog):
    yield from codes_captured(caplog)


@pytest.fixture(autouse=True)
def clear_cache(request):
    """Every database test starts with an empty cache (`cache_cleared`)."""
    yield from cache_cleared(request)


class Clock:
    """The time seen by the resend limits and the fake provider (signup.limits,
    signup.verification); starts at the real time, moves only with `advance`."""

    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr('signup.limits._now', clock)
    monkeypatch.setattr('signup.verification._now', clock)
    return clock


@pytest.fixture
def at_legal(client):
    """Step 1 saved: the legal step is next."""
    from .helpers import _step1

    assert _step1(client)['Location'] == '/en/signup/legal/'
    return client
