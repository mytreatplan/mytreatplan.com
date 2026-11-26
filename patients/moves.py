"""What other apps do when a case moves (epic-case-lifecycle 10.1): they register a receiver
once, in their AppConfig.ready, per named move (`mytp_core.moves.MOVES`), as
`payments.hooks` does for orders:

    patients.moves.register('send_for_review', 'messages_notify', receiver)

`receiver(work_order)` gets the locked, updated `patients.models.WorkOrder`. It runs INSIDE
the move's transaction (`patients.service.apply_move`), so one that raises rolls the whole
move back; side effects (a message, an email) are deferred with `transaction.on_commit`.
It must be idempotent, keyed by the work order's id and version. Receivers of one move run
in the order they were registered. Only `patients.service` calls `fire`, once per applied
move; a stage change that is not a named move (payment, cancel, ICE close) fires nothing.
"""
import re

from mytp_core.moves import MOVES

NAME_RE = re.compile(r'^[a-z][a-z0-9_]{0,39}$')

_registry = {move: {} for move in MOVES}


def register(move, name, receiver):
    """Register `receiver` for `move` under `name`; a second registration of the same name
    on the same move is an error."""
    if move not in _registry:
        raise ValueError(f'move is one of {", ".join(_registry)}, not {move!r}')
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError('a receiver name is 1-40 lowercase letters, digits or "_"')
    if not callable(receiver):
        raise ValueError('a receiver is callable')
    if name in _registry[move]:
        raise ValueError(f'receiver {name!r} is already registered for {move!r}')
    _registry[move][name] = receiver


def unregister(move, name):
    """Remove a receiver (for tests); a missing one is ignored."""
    _registry.get(move, {}).pop(name, None)


def receivers(move):
    """[(name, receiver)] of `move`, in registration order."""
    return list(_registry[move].items())


def fire(move, work_order):
    """Call each receiver of `move` with `work_order`. Called by `patients.service` only."""
    for _name, receiver in receivers(move):
        receiver(work_order)
