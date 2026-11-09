"""The staging-only sample record (epic-admin-interface entry 5, decision 2026-10-30): what
the admin site reads remotely until epic-ordering's patients exist; then this module, the
model, the endpoint, the command and integration.E006 go.

Only with INTERFACE_SAMPLE_RECORDS=True (refused on a public site by integration.E006).
`GET /api/v1/remote/sample-record` reads the newest sample record (`seed_sample_record`
keeps one) under the `sample_record` rules of `mytp_core.access` (area `patients`);
`POST` to the same route changes its note (entry 6, integration.remote.remote_write)
through `update_sample_record`, the one way a remote write changes it.
"""
from django.conf import settings
from django.db import transaction

from mytp_core import access

from .models import SampleRecord
from .remote import RecordGone, StaleVersion

RESOURCE = access.SAMPLE_RECORD


def enabled():
    return bool(getattr(settings, 'INTERFACE_SAMPLE_RECORDS', False))


def load(object_id=None):
    """The newest sample record (or the one with this UUID), or None."""
    records = SampleRecord.objects.all()
    if object_id is not None:
        records = records.filter(pk=object_id)
    return records.first()


def assignee(record):
    return record.assignee_uuid


def version(record):
    return record.version


def build(record):
    """Every field of the record, JSON-ready (the contract's `SampleRecordData`)."""
    return {
        'uuid': str(record.uuid),
        'patient_name': record.patient_name,
        'amount': record.amount.to_contract(),
        'assignee_uuid': str(record.assignee_uuid) if record.assignee_uuid else None,
        'note': record.note,
    }


def update_sample_record(object_id, expected_version, changes):
    """Apply a remote write's `changes` (field names to values, already checked by the
    contract's `SampleRecordChanges` and the role's writable fields) to the sample
    record `object_id`, in a transaction (the caller's, when there is one): lock the row,
    check it is still at `expected_version` (else StaleVersion with the current version;
    nothing changed), apply, version + 1. Returns the updated record; RecordGone when it
    no longer exists."""
    with transaction.atomic():
        record = SampleRecord.objects.select_for_update().filter(pk=object_id).first()
        if record is None:
            raise RecordGone(object_id)
        if record.version != expected_version:
            raise StaleVersion(record.version)
        unknown = set(changes) - WRITABLE_FIELDS
        if unknown:
            raise ValueError(f'not a writable sample field: {sorted(unknown)}')
        for name, value in changes.items():
            setattr(record, name, value)
        record.version += 1
        record.save(update_fields=[*sorted(changes), 'version'])
        return record


# The model fields a remote write may set (the contract's SampleRecordChanges); the
# role's own allow-list (mytp_core.access) is narrower or equal.
WRITABLE_FIELDS = frozenset({'note'})
