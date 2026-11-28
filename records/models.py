"""The scanned records file of a work order (epic-case-lifecycle 10.5)."""
from django.db import models
from django.utils import timezone

from patients.models import WorkOrder
from payments.models import ACTOR_TYPES


class CaseRecords(models.Model):
    """One scanned file linked to a work order, written only by `records.service`.

    The row goes with its work order (the erasure purge deletes the work orders first).
    `stored_object` is SET_NULL: the storage deletion task removes the object row, and a
    row without an object counts as no file. No patient data and no file name here (AD-23);
    the name is stored, encrypted, on the `StoredObject`."""

    work_order = models.OneToOneField(WorkOrder, on_delete=models.CASCADE,
                                      related_name='records')
    stored_object = models.ForeignKey('storage.StoredObject', on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name='+')
    attached_at = models.DateTimeField(default=timezone.now)
    actor_type = models.CharField(max_length=10, choices=ACTOR_TYPES)
    actor_uuid = models.UUIDField(null=True, blank=True)

    def __str__(self):
        return f'records of work order {self.work_order_id}'
