"""The payment gateway adapters (epic-payments entry 1; entries 2-4 add the real ones).

An adapter registers once, in its AppConfig.ready:

    payments.gateways.register('redsys', start, enabled=None)

`start(order, request)` returns the URL the doctor is redirected to (a hosted page) once
the order is `pending_payment` with this gateway chosen. `enabled` (optional, a callable
without arguments) says whether the adapter is available now (the fake one reads its flag
there); an adapter without it is always available. An order offers the gateways its pricing
named (`Order.gateways`, from the billing rule and the region) that have an adapter, plus
the fake one when it is enabled.
"""
import re
from dataclasses import dataclass
from typing import Callable, Optional

NAME_RE = re.compile(r'^[a-z][a-z0-9_]{0,19}$')
FAKE = 'fake'


@dataclass(frozen=True)
class Adapter:
    name: str
    start: Callable
    enabled: Optional[Callable] = None

    def available(self):
        return self.enabled is None or bool(self.enabled())


_registry = {}


def register(name, start, enabled=None):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError('a gateway name is 1-20 lowercase letters, digits or "_"')
    if name in _registry:
        raise ValueError(f'gateway {name!r} is already registered')
    _registry[name] = Adapter(name, start, enabled)


def unregister(name):
    """Remove an adapter (for tests); a missing one is ignored."""
    _registry.pop(name, None)


def adapter(name):
    """The adapter `name` when it is registered and available now, else None."""
    found = _registry.get(name)
    return found if found is not None and found.available() else None


def offered(order):
    """The gateway names the doctor may choose for `order`, in order: the order's priced
    gateways that have an available adapter, then the fake one when it is available and
    the order has something to pay."""
    if order.total <= 0:
        return []
    names = [name for name in order.gateways if name != FAKE and adapter(name) is not None]
    if adapter(FAKE) is not None:
        names.append(FAKE)
    return names
