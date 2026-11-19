"""The order reference a doctor types for a bank transfer (user decision 2026-11-18): two
letters and six digits, shown `KM-482915`. The letters are A-Z without I and O (easy to
mistake for 1 and 0), drawn at random, as the digits. Unique per instance: `unused` redraws
on a collision. The internal key stays the order's UUID. Input is accepted with or without
the dash and in any case (`normalise`)."""
import re
import secrets

LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
DIGITS = '0123456789'
_TYPED = re.compile(r'^([A-Z]{2})[\s-]*([0-9]{6})$')
_TRIES = 20


def draw():
    """A random reference: `KM-482915`."""
    letters = ''.join(secrets.choice(LETTERS) for _ in range(2))
    digits = ''.join(secrets.choice(DIGITS) for _ in range(6))
    return f'{letters}-{digits}'


def normalise(value):
    """The reference as stored (`KM-482915`) for what a person typed (`km482915`,
    ` KM-482915 `), or None when it is not a reference (the letters I and O are never
    drawn, so they are not accepted either)."""
    found = _TYPED.match((value or '').strip().upper())
    if not found or set(found.group(1)) - set(LETTERS):
        return None
    return f'{found.group(1)}-{found.group(2)}'


def unused(taken):
    """A reference for which `taken(reference)` is False, redrawn on a collision."""
    for _ in range(_TRIES):
        reference = draw()
        if not taken(reference):
            return reference
    raise RuntimeError('no free order reference after several draws')
