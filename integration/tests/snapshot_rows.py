"""Test-only: rows of a snapshot-fed table written directly (epic-catalogue entry 11).

The snapshot-fed tables (integration.snapshot_tables) refuse every ORM write outside the
snapshot apply. A test that needs rows writes them through a pull (the fake admin site of
integration/tests/test_config.py) or, when that is out of reach (a browser test's map row,
a country banned between two steps, a release before this one simulated), with the
helpers below, which mark the apply as running for their block only.

Never imported by production code: it lives in a `tests` package, and
integration/tests/test_snapshot_tables.py fails when a module outside the `tests`
packages imports it or the marker itself (only integration.config may).
"""
from integration.snapshot_tables import _snapshot_apply


def snapshot_writes():
    """A context manager: the block writes as the snapshot apply does."""
    return _snapshot_apply()


def put_country(code, state):
    """The country map's row of `code` with `state` (created or changed)."""
    from regions.models import Country
    with snapshot_writes():
        return Country.objects.update_or_create(code=code, defaults={'state': state})[0]


def ensure_country(code, state):
    """The country map's row of `code`, created with `state` when it is missing."""
    from regions.models import Country
    with snapshot_writes():
        return Country.objects.get_or_create(code=code, defaults={'state': state})[0]
