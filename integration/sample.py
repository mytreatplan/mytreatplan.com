"""The staging-only sample record (epic-admin-interface entry 5, decision 2026-10-30): what
the admin site reads remotely until epic-ordering's patients exist; then this module, the
model, the endpoint, the command and integration.E006 go.

Only with INTERFACE_SAMPLE_RECORDS=True (refused on a public site by integration.E006).
`GET /api/v1/remote/sample-record` reads the newest sample record (`seed_sample_record`
keeps one) under the `sample_record` rules of `mytp_core.access` (area `patients`).
"""
from django.conf import settings

from mytp_core import access

from .models import SampleRecord

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


def build(record):
    """Every field of the record, JSON-ready (the contract's `SampleRecordData`)."""
    return {
        'uuid': str(record.uuid),
        'patient_name': record.patient_name,
        'amount': record.amount.to_contract(),
        'assignee_uuid': str(record.assignee_uuid) if record.assignee_uuid else None,
        'note': record.note,
    }
