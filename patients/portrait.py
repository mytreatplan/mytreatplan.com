"""A patient's portrait (epic-ordering 9.2), modelled on the doctor's profile photo
(accounts.photo, which it reuses for the image check and the limits).

The storage purpose `patient_portrait` (registered in PatientsConfig.ready): uploads by a
signed-in doctor (a client account, not erased), at most 5 MiB, declared JPEG, PNG or WebP,
checked by `accounts.photo.check_image` after the clean scan; downloads by the object's
owner (the patient's doctor) only. One portrait per patient, on `Patient`: `portrait`
(shown, only once clean) and `portrait_pending` (the newest upload, waiting for its scan).

- the widget uploads through /storage/sign/ and /storage/complete/, then posts the object's
  id to the patient page (`attach`): only an object of this purpose uploaded by the doctor,
  without an owner and not being deleted, and only for the doctor's own patient (404
  otherwise). It becomes the doctor's (`owner`) and the patient's pending portrait; an
  earlier pending upload is deleted. An upload the scan already rejected is deleted and the
  page says so; one already clean becomes the portrait at once;
- an upload never attached is deleted by the hourly sweep (`unclaimed_after`);
- once its scan passes, `on_clean` promotes it and the previous portrait is deleted. Until
  then the old portrait (or the placeholder) stays and the page says "being checked";
- a rejected upload leaves the old portrait; the patient page shows the reject message once
  (`settle`);
- `remove` unlinks the portrait and any pending upload and deletes both.

Every deletion goes through storage.service.delete with the patient's code in the
DeletionTrace (a code, never a name). The doctor's own patient list and patient page show
the portrait through one presigned inline link per image (`storage.service.owner_inline_url`)
with no access record: AD-9's exception for the portraits of a doctor's own patients, shown
on the patient list and patient file pages (user decision 2026-11-22). Nothing here logs a
file name, a key, a URL or a patient's name.
"""
import logging
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q

from accounts import client_sync
from accounts import photo as profile_photo
from storage import purposes, service
from storage.models import DeletionTrace, StoredObject

from .models import Patient

logger = logging.getLogger(__name__)

PURPOSE = 'patient_portrait'
MAX_SIZE = profile_photo.MAX_SIZE
CONTENT_TYPES = profile_photo.CONTENT_TYPES
UNCLAIMED_AFTER = profile_photo.UNCLAIMED_AFTER

PENDING = 'pending'
PROMOTED = 'promoted'
REJECTED = 'rejected'

storage_available = profile_photo.storage_available


class NotAttachable(Exception):
    """The object may not become this patient's portrait (the view answers 404)."""


# --- The purpose ----------------------------------------------------------------------

def can_upload(user):
    return profile_photo.can_upload(user)


def can_download(user, obj):
    """The owner (the patient's doctor) only."""
    return obj.owner_id is not None and obj.owner_id == user.pk


def in_use(obj):
    return Patient.objects.filter(Q(portrait=obj) | Q(portrait_pending=obj)).exists()


def on_clean(obj):
    """After the scan promoted `obj`: if it is still a patient's pending portrait, it
    becomes the portrait."""
    patient_id = (Patient.objects.filter(portrait_pending=obj).values_list('pk', flat=True)
                  .first())
    if patient_id is None:
        return
    with transaction.atomic():
        patient = Patient.objects.select_for_update().filter(pk=patient_id).first()
        if patient is None or patient.portrait_pending_id != obj.pk:
            return
        fresh = StoredObject.objects.filter(pk=obj.pk).first()
        if fresh is None or fresh.state != StoredObject.CLEAN or fresh.delete_requested_at:
            return
        _promote(patient, fresh)


def register():
    purposes.register(PURPOSE, can_upload, can_download, max_size=MAX_SIZE,
                      content_types=CONTENT_TYPES, validate=profile_photo.check_image,
                      on_clean=on_clean, unclaimed_after=UNCLAIMED_AFTER, in_use=in_use)


# --- Changes --------------------------------------------------------------------------

def _delete(obj, actor, patient):
    service.delete(obj, actor, patient.code)
    # A rejected object already has the scan's system trace (no patient code): add it.
    DeletionTrace.objects.filter(object_id=obj.pk, patient_code='').update(
        patient_code=patient.code)


def _promote(patient, obj):
    """Under the patient's lock: `obj` (clean) becomes the portrait; the previous one is
    deleted, by the uploader."""
    old = patient.portrait
    patient.portrait = obj
    patient.portrait_pending = None
    patient.save(update_fields=['portrait', 'portrait_pending'])
    if old is not None and old.pk != obj.pk:
        _delete(old, obj.uploaded_by, patient)


def _locked_patient(doctor, code):
    return (Patient.objects.select_for_update()
            .filter(doctor_id=doctor.pk, code=code)
            .exclude(status=Patient.ERASED).first())


def attach(doctor, code, object_id):
    """Make the uploaded object `object_id` the pending portrait of the doctor's patient
    `code`. Returns PENDING, PROMOTED or REJECTED. Raises NotAttachable for anything that
    is not the doctor's own unclaimed upload of this purpose for their own patient."""
    if not can_upload(doctor):
        raise NotAttachable
    with transaction.atomic():
        get_user_model().objects.select_for_update().filter(pk=doctor.pk).first()
        patient = _locked_patient(doctor, code)
        if patient is None:
            raise NotAttachable
        obj = (StoredObject.objects.select_for_update()
               .filter(pk=object_id, purpose=PURPOSE, uploaded_by_id=doctor.pk,
                       owner__isnull=True, delete_requested_at__isnull=True).first())
        if obj is None:
            raise NotAttachable
        if obj.state == StoredObject.REJECTED:
            _delete(obj, doctor, patient)
            return REJECTED
        obj.owner_id = doctor.pk
        obj.save(update_fields=['owner'])
        earlier = patient.portrait_pending
        patient.portrait_pending = obj
        patient.save(update_fields=['portrait_pending'])
        if earlier is not None and earlier.pk != obj.pk:
            _delete(earlier, doctor, patient)
        if obj.state == StoredObject.CLEAN:
            _promote(patient, obj)
            return PROMOTED
        return PENDING


def has_portrait(patient):
    """Whether the patient links a portrait or a pending upload."""
    return patient.portrait_id is not None or patient.portrait_pending_id is not None


def remove(doctor, code):
    """The confirmed Delete: unlink the portrait and any pending upload and delete both.
    Returns whether there was anything to delete."""
    with transaction.atomic():
        patient = _locked_patient(doctor, code)
        if patient is None:
            raise NotAttachable
        objects = [obj for obj in (patient.portrait, patient.portrait_pending)
                   if obj is not None]
        if objects:
            patient.portrait = patient.portrait_pending = None
            patient.save(update_fields=['portrait', 'portrait_pending'])
        for obj in objects:
            _delete(obj, doctor, patient)
        return bool(objects)


def settle(patient):
    """The patient page's look at the pending upload: one already clean (its hook did not
    run) is promoted now; a rejected one is unlinked and deleted, and True is returned
    once, for the reject message."""
    pending = patient.portrait_pending
    if pending is None or pending.state == StoredObject.QUARANTINE:
        return False
    with transaction.atomic():
        locked = Patient.objects.select_for_update().filter(pk=patient.pk).first()
        if locked is None or locked.portrait_pending_id != pending.pk:
            return False
        pending = StoredObject.objects.filter(pk=pending.pk).first()
        if pending is None:
            return False
        actor = pending.uploaded_by
        if pending.state == StoredObject.CLEAN and pending.delete_requested_at is None:
            _promote(locked, pending)
        else:
            rejected = pending.state == StoredObject.REJECTED
            locked.portrait_pending = None
            locked.save(update_fields=['portrait_pending'])
            _delete(pending, actor, locked)
            if not rejected:
                return False
            patient.portrait_pending = None
            return True
    patient.portrait, patient.portrait_pending = locked.portrait, locked.portrait_pending
    return False


# --- Showing it -----------------------------------------------------------------------

@dataclass(frozen=True)
class Portrait:
    """What the doctor's pages show: `url` (the presigned inline link, '' for the
    placeholder) and `pending` (an upload is being checked)."""

    url: str = ''
    pending: bool = False


def portrait_of(patient, doctor):
    """The Portrait of `patient` for `doctor` now: a link only for a clean portrait that
    `doctor` owns and that belongs to their own patient; the placeholder on any failure
    (logged without the key, the name or the code)."""
    if (patient.status == Patient.ERASED or patient.doctor_id != doctor.pk
            or not storage_available()):
        return Portrait()
    pending = patient.portrait_pending
    is_pending = (pending is not None and pending.state == StoredObject.QUARANTINE
                  and pending.delete_requested_at is None)
    shown = patient.portrait
    url = ''
    if (shown is not None and shown.state == StoredObject.CLEAN
            and shown.delete_requested_at is None and shown.owner_id == doctor.pk):
        try:
            url = service.owner_inline_url(shown, doctor)
        except Exception as exc:  # noqa: BLE001 - the page shows the placeholder instead
            logger.warning('patients: portrait link failed (%s)', type(exc).__name__)
            url = ''
    return Portrait(url=url, pending=is_pending)
