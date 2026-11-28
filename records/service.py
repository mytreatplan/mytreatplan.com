"""The records a doctor uploads for a case (epic-case-lifecycle 10.5, L4 and L3), modelled
on `patients.portrait`.

The storage purpose `case_records` (registered in RecordsConfig.ready): uploads by a
signed-in doctor (a client account, not erased), any file type, up to 1 GiB; no download
at all (the staff download is 10.6). One file per work order (`CaseRecords`).

- the widget uploads through /storage/sign/ and /storage/complete/, then posts the
  object's id (`attach`): only an object of this purpose uploaded by the doctor, without an
  owner and not being deleted, and only for the live work order of the doctor's own
  patient while it is in `pending_registers_upload` (`NotAttachable` otherwise: the view
  answers 404 or a message). The object becomes the doctor's (`owner`) and the work order's
  file; an earlier file is deleted (a second upload replaces the first). An object the scan
  already rejected is linked all the same, so the page can say why it was refused;
- an upload never attached is deleted by the hourly sweep (`unclaimed_after`); an attached
  one stays (`in_use`);
- `complete` (Records complete) needs a clean attached file and then applies the doctor's
  move `records_complete` (`patients.service.apply_move`);
- `statuses` is what the page shows for every work order of the patient that has a file.

Every deletion goes through storage.service.delete with the patient's code in the
DeletionTrace. Nothing here logs a file name, a key, a URL or a patient's name (AD-23).
"""
import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from accounts import photo as profile_photo
from catalogue import resolver
from mytp_core import stages
from patients import service as patients_service
from patients.models import Patient, StageChange, WorkOrder
from payments.service import doctor_actor
from storage import purposes, service
from storage.models import DeletionTrace, StoredObject

from .models import CaseRecords

logger = logging.getLogger(__name__)

PURPOSE = 'case_records'
MAX_SIZE = purposes.MAX_SCANNED_SIZE
# A 1 GiB upload takes a while, but the clock starts when it is recorded, and the page
# attaches it at once.
UNCLAIMED_AFTER = timedelta(hours=6)
MOVE = 'records_complete'

# What the page shows for a file.
CHECKING = 'checking'
WAITING = 'waiting'
PROCESSED = 'processed'
REFUSED = 'refused'

storage_available = profile_photo.storage_available


class NotAttachable(Exception):
    """The object may not become this case's records (the view answers 404)."""


class NotUploadable(Exception):
    """The case is not waiting for records (the doctor's page changed meanwhile)."""


class NotReady(Exception):
    """Records complete without a clean attached file."""


# --- The purpose ----------------------------------------------------------------------

def can_upload(user):
    return profile_photo.can_upload(user)


def can_download(user, obj):
    """Nobody: the doctor has no download of the records, and the staff download is 10.6."""
    return False


def in_use(obj):
    return CaseRecords.objects.filter(stored_object=obj).exists()


def register():
    purposes.register(PURPOSE, can_upload, can_download, max_size=MAX_SIZE,
                      unclaimed_after=UNCLAIMED_AFTER, in_use=in_use)


# --- Changes --------------------------------------------------------------------------

def _delete(obj, actor, patient):
    service.delete(obj, actor, patient.code)
    # A rejected object already has the scan's system trace (no patient code): add it.
    DeletionTrace.objects.filter(object_id=obj.pk, patient_code='').update(
        patient_code=patient.code)


def _locked_waiting(doctor, code):
    """Under locks: the doctor's own patient `code` and the patient's live work order in
    `pending_registers_upload`. Raises NotAttachable (no such patient) or NotUploadable."""
    get_user_model().objects.select_for_update().filter(pk=doctor.pk).first()
    patient = (Patient.objects.select_for_update()
               .filter(doctor_id=doctor.pk, code=code)
               .exclude(status=Patient.ERASED).first())
    if patient is None:
        raise NotAttachable
    work_order = (WorkOrder.objects.select_for_update()
                  .filter(patient_id=patient.pk,
                          stage__in=sorted(stages.LIVE_STAGES)).first())
    if work_order is None or work_order.stage != stages.PENDING_REGISTERS_UPLOAD:
        raise NotUploadable
    return patient, work_order


def attach(doctor, code, object_id):
    """Link the uploaded object `object_id` as the records of the doctor's patient `code`
    (their live work order waiting for records). Returns the work order. Raises
    NotAttachable for anything that is not the doctor's own unclaimed upload of this
    purpose for their own patient, NotUploadable when the case is not waiting for records."""
    if not can_upload(doctor):
        raise NotAttachable
    with transaction.atomic():
        patient, work_order = _locked_waiting(doctor, code)
        obj = (StoredObject.objects.select_for_update()
               .filter(pk=object_id, purpose=PURPOSE, uploaded_by_id=doctor.pk,
                       owner__isnull=True, delete_requested_at__isnull=True).first())
        if obj is None:
            raise NotAttachable
        obj.owner_id = doctor.pk
        obj.save(update_fields=['owner'])
        actor = doctor_actor(doctor)
        row = CaseRecords.objects.select_for_update().filter(work_order=work_order).first()
        earlier = None
        if row is None:
            CaseRecords.objects.create(work_order=work_order, stored_object=obj,
                                       actor_type=actor.type, actor_uuid=actor.uuid)
        else:
            earlier = row.stored_object
            row.stored_object = obj
            row.actor_type, row.actor_uuid = actor.type, actor.uuid
            row.attached_at = timezone.now()
            row.save()
        if earlier is not None and earlier.pk != obj.pk:
            _delete(earlier, doctor, patient)
        return work_order


def complete(doctor, code, expected_version):
    """Records complete: the doctor's move `records_complete`, once a clean file is
    attached. Returns the moved work order. Raises NotAttachable (not the doctor's
    patient), NotUploadable (the case is not waiting for records), NotReady (no clean
    file: none, still being checked, refused or being deleted) and
    `patients.service.StaleVersion` / `MoveRefused`, with nothing changed."""
    if not can_upload(doctor):
        raise NotAttachable
    with transaction.atomic():
        _patient, work_order = _locked_waiting(doctor, code)
        row = (CaseRecords.objects.select_for_update().filter(work_order=work_order)
               .select_related('stored_object').first())
        obj = row.stored_object if row is not None else None
        if (obj is None or obj.state != StoredObject.CLEAN
                or obj.delete_requested_at is not None):
            raise NotReady
        return patients_service.apply_move(work_order, MOVE, expected_version,
                                           doctor_actor(doctor))


# --- Showing it -----------------------------------------------------------------------

def waiting_work_order(patient):
    """The patient's live work order when it waits for records, else None."""
    work_order = patients_service.live_work_order(patient)
    if work_order is not None and work_order.stage == stages.PENDING_REGISTERS_UPLOAD:
        return work_order
    return None


def statuses(patient):
    """The file status of each work order of `patient` that has a records file, newest
    first: dicts with `work_order`, `service` (the product's public name, else the work
    order's code, as the patient file names it), `status` (CHECKING, WAITING, PROCESSED or
    REFUSED) and `reason` (`unscannable`, `infected` or '' for a refused file). A row whose object is gone shows nothing."""
    rows = (CaseRecords.objects.filter(work_order__patient_id=patient.pk,
                                       stored_object__isnull=False)
            .select_related('work_order', 'stored_object')
            .order_by('-work_order__created_at', '-work_order_id'))
    done = set(StageChange.objects.filter(
        work_order__patient_id=patient.pk, to_stage=stages.PRESCRIPTION_DONE)
        .values_list('work_order_id', flat=True))
    result = []
    for row in rows:
        obj = row.stored_object
        if obj.delete_requested_at is not None:
            continue
        if obj.state == StoredObject.REJECTED:
            status = REFUSED
        elif obj.state == StoredObject.QUARANTINE:
            status = CHECKING
        elif row.work_order_id in done:
            status = PROCESSED
        else:
            status = WAITING
        order = row.work_order
        product = resolver.product(order.product_id) if order.product_id else None
        result.append({'work_order': order, 'status': status,
                       'service': product.public_name if product is not None else order.code,
                       'reason': obj.reject_reason or ''})
    return result
