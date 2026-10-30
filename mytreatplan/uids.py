"""Global ids for records that cross sites (AD-5).

A record that crosses sites is referenced only by a UUID; integer primary keys never leave
an instance. A model whose records cross sites says so with the class attribute
`CROSSES_SITES = True`; it must then have a UUID primary key (new tables) or an immutable,
unique `uid` UUID (part-1 tables, which keep their integer key). The convention test
(mytp_publicsite.tests.test_cross_sites) checks every installed model with
`crossing_site_problems`.

`fill_uids` is the data step of the three migrations that add a `uid` to an existing table
(add nullable -> fill -> make unique and non-null, each its own migration, so PostgreSQL
never alters a table with pending trigger events).
"""
import uuid

from django.core.exceptions import FieldDoesNotExist
from django.db import models

UID_FIELD = 'uid'
FILL_BATCH_SIZE = 500


def crosses_sites(model):
    return bool(getattr(model, 'CROSSES_SITES', False))


def crossing_site_problem(model):
    """Why `model` breaks the convention, or None. Only for a model marked as crossing."""
    if isinstance(model._meta.pk, models.UUIDField):
        return None
    name = model._meta.label
    try:
        field = model._meta.get_field(UID_FIELD)
    except FieldDoesNotExist:
        return f'{name} crosses sites but has neither a UUID primary key nor a `uid` field.'
    if not isinstance(field, models.UUIDField):
        return f'{name}.{UID_FIELD} is not a UUIDField.'
    if not field.unique or field.null:
        return f'{name}.{UID_FIELD} must be unique and not null.'
    if field.editable:
        return f'{name}.{UID_FIELD} must not be editable.'
    if field.default is not uuid.uuid4:
        return f'{name}.{UID_FIELD} must default to uuid.uuid4.'
    return None


def crossing_site_problems(model_list):
    """The convention breaches among the models in `model_list` marked as crossing."""
    return [problem for model in model_list if crosses_sites(model)
            if (problem := crossing_site_problem(model)) is not None]


def fill_uids(app_label, model_name, batch_size=FILL_BATCH_SIZE):
    """A RunPython function giving every row of the model without a uid its own uuid4,
    in batches. Only the `uid` column is written (no save(), no other field touched)."""

    def forward(apps, schema_editor):
        model = apps.get_model(app_label, model_name)
        manager = model._base_manager.using(schema_editor.connection.alias)
        while True:
            pks = list(manager.filter(**{f'{UID_FIELD}__isnull': True})
                       .order_by('pk').values_list('pk', flat=True)[:batch_size])
            if not pks:
                return
            rows = [model(pk=pk, **{UID_FIELD: uuid.uuid4()}) for pk in pks]
            manager.bulk_update(rows, [UID_FIELD])

    return forward
