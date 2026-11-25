"""What other apps do when an order moves (epic-payments entry 1): they register a receiver
once, in their AppConfig.ready, as storage registers its purposes:

    payments.hooks.register('paid', 'subscriptions_activate', receiver)

`event` is one of EVENTS: `created` (the order exists, in `checkout`; the place to reserve
a voucher or an included case), `transfer_approved` (the doctor chose the bank transfer: the
order is `pending_payment` and waits for the money; fired each time the transfer is chosen, so
a receiver is idempotent per order; epic-ordering 9.17 empties the cart here and 9.8 hangs its
pending-payment work orders here), `paid`, `cancelled`, `expired` and `refunded` (a paid order
was fully refunded and its credit note issued; the place to give back what the order granted,
in later stories).
`receiver(order)` gets the locked `payments.models.Order`; it runs INSIDE the transition's transaction, so a
receiver that raises rolls the whole transition back (`payments.service.HookFailed`), and
it must be idempotent, keyed by `order.id` (the UUID). Receivers of one event run in the
order they were registered. Only `payments.service` calls `fire`, exactly once per
transition (`transfer_approved`: once per choice of the bank transfer).
"""
import re

EVENTS = ('created', 'transfer_approved', 'paid', 'cancelled', 'expired', 'refunded')
NAME_RE = re.compile(r'^[a-z][a-z0-9_]{0,39}$')

_registry = {event: {} for event in EVENTS}


def register(event, name, receiver):
    """Register `receiver` for `event` under `name`; a second registration of the same
    name on the same event is an error."""
    if event not in EVENTS:
        raise ValueError(f'event is one of {", ".join(EVENTS)}, not {event!r}')
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError('a receiver name is 1-40 lowercase letters, digits or "_"')
    if not callable(receiver):
        raise ValueError('a receiver is callable')
    if name in _registry[event]:
        raise ValueError(f'receiver {name!r} is already registered for {event!r}')
    _registry[event][name] = receiver


def unregister(event, name):
    """Remove a receiver (for tests); a missing one is ignored."""
    _registry[event].pop(name, None)


def receivers(event):
    """[(name, receiver)] of `event`, in registration order."""
    return list(_registry[event].items())


def fire(event, order):
    """Call each receiver of `event` with `order`. Called by `payments.service` only."""
    for _name, receiver in receivers(event):
        receiver(order)
