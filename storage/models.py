"""Stored files (AD-9): one StoredObject per completed upload, the uploads still in
progress (PendingUpload), and the trace a deleted object leaves (DeletionTrace).

The object's UUID is its primary key and its S3 key (`quarantine/<uuid>`, later
`clean/<uuid>`, storage.s3). The original file name is field-encrypted (AD-2) and never
logged; nothing here is shown in the admin site (AD-23).
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.crypto import EncryptedCharField

ORIGINAL_NAME_MAX_LENGTH = 255
CONTENT_TYPE_MAX_LENGTH = 255


class StoredObject(models.Model):
    """A file in the region's bucket. Created only when its multipart upload has completed
    and HeadObject confirmed the size (storage.views.complete); deleted only through
    storage.service.delete, which leaves a DeletionTrace.

    Its state (epic-platform entry 12, storage.tasks.scan_object_task): `quarantine`
    until scanned; `clean` once the scan passed it (the object moved to `clean/<uuid>`);
    `rejected` when the scanner found something or could not fully scan it: the object is
    deleted from the bucket (with a DeletionTrace by the system) and the row kept, never
    downloadable (decision 2026-11-07)."""

    QUARANTINE = 'quarantine'
    CLEAN = 'clean'
    REJECTED = 'rejected'
    STATES = [(QUARANTINE, 'Quarantine (not scanned yet)'), (CLEAN, 'Clean (scanned)'),
              (REJECTED, 'Rejected by the scan (deleted from the bucket)')]

    # AD-5: crosses sites; its UUID primary key is its global id.
    CROSSES_SITES = True

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purpose = models.CharField(max_length=40)
    # Whose file it is (a patient's doctor, an order's clinic, ...): set by the real
    # purposes (later epics); none for the staging sample purpose.
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                              blank=True, related_name='stored_objects')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='uploaded_objects')
    size = models.BigIntegerField()
    content_type = models.CharField(max_length=CONTENT_TYPE_MAX_LENGTH, blank=True)
    original_name = EncryptedCharField(max_length=ORIGINAL_NAME_MAX_LENGTH, blank=True)
    state = models.CharField(max_length=20, choices=STATES, default=QUARANTINE)
    created_at = models.DateTimeField(default=timezone.now)
    # Set when its deletion is asked (storage.service.delete): no download URL after that,
    # and the hourly `sweepstorage` job enqueues the deletion again until it succeeds.
    delete_requested_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.purpose} object {self.pk}'

    @property
    def key(self):
        from . import s3
        return s3.clean_key(self.pk) if self.state == self.CLEAN else s3.quarantine_key(self.pk)


class PendingUpload(models.Model):
    """An upload signed but not completed yet: what the browser declared when it started
    (purpose, size, content type), so the later requests and the completion can only name
    the uploader's own key and S3 upload. Removed on completion or abort; an abandoned
    one is removed after STORAGE_ABORT_INCOMPLETE_DAYS (when the next upload starts, and
    by the `sweepstorage` job), as
    the bucket's lifecycle rule removes its parts."""

    id = models.UUIDField(primary_key=True, editable=False)
    purpose = models.CharField(max_length=40)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='pending_uploads')
    size = models.BigIntegerField()
    content_type = models.CharField(max_length=CONTENT_TYPE_MAX_LENGTH, blank=True)
    # S3's UploadId, bound by the first request that names one.
    upload_id = models.CharField(max_length=1024, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    def __str__(self):
        return f'pending {self.purpose} upload {self.pk}'


class DeletionTrace(models.Model):
    """What remains of a deleted object (AD-9): its UUID, who deleted it and when. Kept for
    good (decision 2026-11-06). `patient_code` stays blank until patients exist."""

    USER = 'user'
    SYSTEM = 'system'
    ACTOR_TYPES = [(USER, 'User'), (SYSTEM, 'System')]

    # AD-5: crosses sites; keyed by the object's UUID.
    CROSSES_SITES = True

    object_id = models.UUIDField(primary_key=True, editable=False)
    patient_code = models.CharField(max_length=20, blank=True)
    actor_type = models.CharField(max_length=20, choices=ACTOR_TYPES)
    # The actor's global id (accounts.User.uid); none for the system.
    actor_uuid = models.UUIDField(null=True, blank=True)
    deleted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-deleted_at']

    def __str__(self):
        return f'deleted object {self.object_id}'
