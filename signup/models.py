import uuid

from django.db import models


class PendingRegistration(models.Model):
    """A signup in progress. No user exists until the email code is correct.

    The wizard keeps only this row's id in the session. The password is stored as a
    Django hash (never plaintext); the new user later receives the same hash.
    `created_at` / `updated_at` let the purge job (signup entry 10) drop stale rows.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField()
    password = models.CharField(max_length=128)
    # Slug of the first step not yet saved (see signup.steps.STEPS).
    step = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Pending registration {self.pk}'
