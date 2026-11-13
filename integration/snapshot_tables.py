"""The tables the configuration snapshot feeds are read-only outside its apply
(epic-catalogue entry 11, AD-3, AD-6, AD-16, AD-19).

A snapshot-fed table (`SnapshotFedModel`: the nine catalogue tables, the country map
`regions.Country` and the admin directory `integration.AdminDirectoryEntry`) changes only
while integration.config applies a snapshot. Every ORM write path refuses with
`ReadOnlyTable` (READ_ONLY_MESSAGE) outside it: an instance's `save()` and `delete()`
(so `objects.create`, `get_or_create` and `update_or_create` too) and the queryset's
`update()`, `delete()`, `bulk_create()` and `bulk_update()`. Each model also names
`objects` as its base manager (`Meta.base_manager_name`: a Meta of an abstract base is
not inherited by a child that declares its own, so each model sets it), so
`Model._base_manager` and the reverse related managers' `add()` / `set()` (which update
through the base manager) are refused too.

The marker is a context variable, set only by `_snapshot_apply()` around the writes of
integration.config._apply and reset when it ends, also by an error: a thread (or an async
task) sees only its own apply, never another one's. Production code sets it nowhere else
(integration/tests/test_snapshot_tables.py checks that). Tests that need rows write them
through a pull, or through integration/tests/snapshot_rows.py.

Not guarded: raw SQL; the private queryset methods (`_update`, `_raw_delete`, `_insert`,
which `save()` and the deletes use once the guard has let them through); raw
deserialisation, `save_base(raw=True)` (`loaddata` fixtures, a test's
`serialized_rollback`); and the historical models of migrations (a migration seeds and
reverses these tables as before; their base manager is a plain one).
"""
import contextlib
import contextvars

from django.db import models

READ_ONLY_MESSAGE = ('This table is read-only here: it changes only when a configuration '
                     'snapshot is applied (integration.config).')


class ReadOnlyTable(TypeError):
    """A snapshot-fed table was written outside the snapshot apply."""

    def __init__(self, message=READ_ONLY_MESSAGE):
        super().__init__(message)


_applying = contextvars.ContextVar('mytp_snapshot_apply', default=False)


def snapshot_apply_running():
    """Whether this thread (or async task) is inside the snapshot apply."""
    return _applying.get()


@contextlib.contextmanager
def _snapshot_apply():
    """Mark the snapshot apply as running for the block (integration.config._apply only;
    the test helper integration/tests/snapshot_rows.py)."""
    token = _applying.set(True)
    try:
        yield
    finally:
        _applying.reset(token)


def refuse_outside_the_apply():
    if not _applying.get():
        raise ReadOnlyTable()


class SnapshotQuerySet(models.QuerySet):
    """Refuses `update`, `delete`, `bulk_create` and `bulk_update` outside the apply."""

    def update(self, **kwargs):
        refuse_outside_the_apply()
        return super().update(**kwargs)

    update.alters_data = True

    def delete(self):
        refuse_outside_the_apply()
        return super().delete()

    delete.alters_data = True
    delete.queryset_only = True

    def bulk_create(self, objs, *args, **kwargs):
        refuse_outside_the_apply()
        return super().bulk_create(objs, *args, **kwargs)

    bulk_create.alters_data = True

    def bulk_update(self, objs, fields, *args, **kwargs):
        refuse_outside_the_apply()
        return super().bulk_update(objs, fields, *args, **kwargs)

    bulk_update.alters_data = True


class SnapshotFedModel(models.Model):
    """A table filled only by the snapshot apply: see the module docstring."""

    objects = SnapshotQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        refuse_outside_the_apply()
        return super().save(*args, **kwargs)

    save.alters_data = True

    def delete(self, *args, **kwargs):
        refuse_outside_the_apply()
        return super().delete(*args, **kwargs)

    delete.alters_data = True
