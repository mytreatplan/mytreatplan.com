import uuid

from django.db import models

from accounts.crypto import BLIND_INDEX_LENGTH, EncryptedEmailField, email_index


class PendingRegistration(models.Model):
    """A signup in progress. No user exists until the email code is correct.

    The wizard keeps only this row's id in the session. The password is stored as a
    Django hash (never plaintext); the new user later receives the same hash.
    The email is encrypted (accounts.crypto); `email_index` is its blind index, set on
    every save (not unique: two signups may be in progress for one address).
    `created_at` / `updated_at` let the purge job (signup entry 10) drop stale rows.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = EncryptedEmailField(max_length=254)
    email_index = models.CharField(max_length=BLIND_INDEX_LENGTH, db_index=True, editable=False)
    password = models.CharField(max_length=128)
    # Slug of the first step not yet saved (see signup.steps.STEPS).
    step = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Pending registration {self.pk}'

    def set_blind_indexes(self):
        self.email_index = email_index(self.email)

    def save(self, *args, **kwargs):
        self.set_blind_indexes()
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'email' in update_fields:
            kwargs['update_fields'] = {*update_fields, 'email_index'}
        super().save(*args, **kwargs)
