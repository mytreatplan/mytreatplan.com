import logging
import time

import pytest


@pytest.fixture(autouse=True)
def capture_codes(caplog):
    """Let caplog see the fake provider's code lines (that logger does not propagate)."""
    logger = logging.getLogger('signup.verification')
    logger.addHandler(caplog.handler)
    yield caplog
    logger.removeHandler(caplog.handler)


@pytest.fixture(autouse=True)
def clear_cache(request):
    """Start every database test with an empty cache (the resend limits and the fake
    provider's codes live there)."""
    if request.node.get_closest_marker('django_db') is None:
        yield
        return
    from django.core.cache import cache

    request.getfixturevalue('db')
    cache.clear()
    yield
    cache.clear()


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
