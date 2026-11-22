"""Patients and work orders (epic-ordering 9.1, AD-5, AD-15, AD-21).

`Patient`: one per patient of a doctor, regional (AD-2: the names, reference and comments
are field-encrypted and stay on this site; only the code travels centrally). The code
(`EU-7K3QX9MD`) is generated, never derived from the patient's data, and never reused:
patients are never hard-deleted. `WorkOrder`: one case of a patient; `order_line_id` is the
paid order line that started it (unique: the paid hook is idempotent). A patient has at most
one work order in a live stage (`mytp_core.stages.LIVE_STAGES`), also a partial unique
constraint.

Written only by `patients.service`: a change locks the row, checks the version, raises it
by one and stores who made it.
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from accounts.crypto import EncryptedCharField
from mytp_core import stages
from payments.models import ACTOR_TYPES


class Patient(models.Model):
    # AD-5: crosses sites; the UUID is its global id.
    CROSSES_SITES = True

    ACTIVE, ERASED = 'active', 'erased'
    STATUSES = [(ACTIVE, 'Active'), (ERASED, 'Erased')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # SET_NULL: a doctor's account going does not delete the patient row (patients are
    # never hard-deleted).
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                               related_name='patients')
    code = models.CharField(max_length=11, unique=True)
    first_name = EncryptedCharField(max_length=150, blank=True)
    surname = EncryptedCharField(max_length=150, blank=True)
    # The doctor's own reference for the patient.
    reference = EncryptedCharField(max_length=100, blank=True)
    comments = EncryptedCharField(max_length=2000, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=ACTIVE)
    version = models.PositiveIntegerField(default=1)
    actor_type = models.CharField(max_length=10, choices=ACTOR_TYPES)
    actor_uuid = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', 'code']

    def __str__(self):
        return self.code

    @property
    def display_name(self):
        """The name, else the doctor's reference, else '' (never shown for an erased
        patient)."""
        if self.status == self.ERASED:
            return ''
        name = f'{self.first_name} {self.surname}'.strip()
        return name or self.reference


class WorkOrder(models.Model):
    CROSSES_SITES = True

    STAGE_CHOICES = [(stage, stage.replace('_', ' ').capitalize()) for stage in stages.STAGES]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='work_orders')
    # The paid order line that started the case; unique, so a replayed hook adds nothing.
    order_line_id = models.UUIDField(unique=True)
    product_id = models.UUIDField(null=True, blank=True)
    code = models.CharField(max_length=64, blank=True, default='')
    stage = models.CharField(max_length=32, choices=STAGE_CHOICES)
    version = models.PositiveIntegerField(default=1)
    actor_type = models.CharField(max_length=10, choices=ACTOR_TYPES)
    actor_uuid = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [models.UniqueConstraint(
            fields=('patient',), condition=Q(stage__in=sorted(stages.LIVE_STAGES)),
            name='patients_one_live_work_order')]
