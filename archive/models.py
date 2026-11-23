"""`AccountArchive`: the record that an erased account was written to the archive bucket
(archive.service). It holds no personal data: the account's global id, the object key
(`accounts/<uid>.json.age`), the object's SHA-256 and size, and the dates. The purge
deletes the row once `retain_until` has passed (the bucket's lifecycle rule removes the
object)."""
from django.db import models
from django.utils import timezone


class AccountArchive(models.Model):
    user_uid = models.UUIDField(unique=True, editable=False)
    object_key = models.CharField(max_length=100, editable=False)
    sha256 = models.CharField(max_length=64, editable=False)
    size = models.PositiveBigIntegerField(editable=False)
    written_at = models.DateTimeField(default=timezone.now, editable=False)
    retain_until = models.DateTimeField(db_index=True, editable=False)

    class Meta:
        ordering = ['written_at', 'id']

    def __str__(self):
        return f'Archive {self.user_uid}'
