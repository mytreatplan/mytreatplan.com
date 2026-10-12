import logging

import pytest


@pytest.fixture(autouse=True)
def capture_codes(caplog):
    """Let caplog see the fake provider's code lines (that logger does not propagate)."""
    logger = logging.getLogger('signup.verification')
    logger.addHandler(caplog.handler)
    yield caplog
    logger.removeHandler(caplog.handler)
