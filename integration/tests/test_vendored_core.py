"""The vendored mytp-core wheel is the release this site expects (epic-catalogue entry 1)."""

from decimal import Decimal

import mytp_core
from mytp_core.money import Money


def test_vendored_mytp_core_has_money():
    assert mytp_core.__version__ == '0.9.0'
    assert Money('10.005', 'EUR') == Money(Decimal('10.01'), 'EUR')
    assert Money('10', 'EUR') != Money('10', 'AED')
