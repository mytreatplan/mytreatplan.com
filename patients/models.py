"""Patients and work orders (epic-ordering 9.1, AD-5, AD-15, AD-21).

`Patient`: one per patient of a doctor, regional (AD-2: the names, reference and comments
are field-encrypted and stay on this site; only the code travels centrally). The code
(`EU-7K3QX9MD`) is generated at random, never derived from the patient's data. It could in principle be
drawn again after its patient is deleted, which is negligible (and invoices keep only the
code). A patient is never deleted on its own here: erasing the doctor hides the patients
(`erased`), and the purge archives them (the archive app) and then deletes them with the
account. `WorkOrder`: one case of a patient; `order_line_id` is the
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

    ACTIVE, INACTIVE, ERASED = 'active', 'inactive', 'erased'
    STATUSES = [(ACTIVE, 'Active'), (INACTIVE, 'Inactive'), (ERASED, 'Erased')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # PROTECT: nothing deletes a doctor under its patients by accident. The purge
    # (`accounts.erasure.purge_erased`) deletes the work orders and patients explicitly,
    # only after the account's archive object is written and verified (9.12).
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                               related_name='patients')
    code = models.CharField(max_length=11, unique=True)
    first_name = EncryptedCharField(max_length=150, blank=True)
    surname = EncryptedCharField(max_length=150, blank=True)
    # The doctor's own reference for the patient.
    reference = EncryptedCharField(max_length=100, blank=True)
    comments = EncryptedCharField(max_length=2000, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=ACTIVE)
    # The portrait (epic-ordering 9.2, patients.portrait): `portrait` is shown, `portrait_pending`
    # is the newest upload waiting for its scan. SET_NULL: the object row is removed by the
    # deletion task; the patient stays.
    portrait = models.ForeignKey('storage.StoredObject', on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name='+')
    portrait_pending = models.ForeignKey('storage.StoredObject', on_delete=models.SET_NULL,
                                         null=True, blank=True, related_name='+')
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


class IceDeduction(models.Model):
    """One ICE credit taken by a planning line (epic-ordering 9.7; user decisions
    2026-11-25). It follows the planning order through the `payments.hooks` receivers of
    `patients.ice`, as a voucher redemption does: `reserved` when the order is created,
    `consumed` when it is paid, `released` when it is cancelled or expires before payment.
    A released row stays as history and never counts as a use; a consumed one is never
    released (a refunded planning order keeps it: the ICE is not offered again).

    `ice_line_id` is the paid ICE order line whose `net` is the credit; `order_id` and
    `order_line_id` the planning order and line that take it (`order_line_id` is the line's
    id when the deduction was reserved or consumed: re-choosing the fiscal entity re-creates
    the lines). All plain UUIDs, with no database link (the doctor's account may go). The
    database allows one live (reserved or consumed) deduction per ICE line, so two orders
    racing for one ICE fail as `payments.service.HookFailed`. `amount` is the credit the
    planning line took (its `ice`, at most the ICE line's net). No patient name is kept."""

    CROSSES_SITES = True

    RESERVED, CONSUMED, RELEASED = 'reserved', 'consumed', 'released'
    STATES = [(RESERVED, 'Reserved'), (CONSUMED, 'Consumed'), (RELEASED, 'Released')]
    LIVE_STATES = (RESERVED, CONSUMED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ice_line_id = models.UUIDField()
    order_id = models.UUIDField()
    order_line_id = models.UUIDField()
    doctor_uid = models.UUIDField()
    patient_code = models.CharField(max_length=32)
    state = models.CharField(max_length=10, choices=STATES)
    amount = models.DecimalField(max_digits=17, decimal_places=2)
    currency = models.CharField(max_length=3)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [models.Index(fields=['order_id'], name='ice_deduction_order')]
        constraints = [
            models.UniqueConstraint(fields=['order_line_id'], name='ice_deduction_one_per_line'),
            models.UniqueConstraint(
                fields=['ice_line_id'], condition=Q(state__in=['reserved', 'consumed']),
                name='ice_deduction_live_once'),
            models.CheckConstraint(condition=Q(state__in=['reserved', 'consumed', 'released']),
                                   name='ice_deduction_state'),
        ]

    def __str__(self):
        return f'ICE deduction {self.pk} ({self.state})'
