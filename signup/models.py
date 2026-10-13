import uuid

from django.db import models
from django_countries.fields import CountryField

from accounts.crypto import BLIND_INDEX_LENGTH, EncryptedCharField, EncryptedEmailField, email_index
from accounts.models import (HOW_FOUND_OTHER_MAX_LENGTH, PHONE_MAX_LENGTH, HowFound, Title,
                             phone_index_or_blank)


class PendingRegistration(models.Model):
    """A signup in progress. No user exists until the email code is correct.

    The wizard keeps only this row's id in the session. The password is stored as a
    Django hash (never plaintext); the new user later receives the same hash.
    Email, names, mobile (E.164) and the "Others" text are encrypted (accounts.crypto);
    `email_index` and `phone_index` are their blind indexes, set on every save (not
    unique: two signups may be in progress for one address or number; signup entry 5
    checks them). Title, country, prefix region and how-found-us are codes.
    `created_at` / `updated_at` let the purge job (signup entry 10) drop stale rows.
    `attempt` is this signup's event log attempt (eventlog), kept after this row is gone.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = EncryptedEmailField(max_length=254)
    email_index = models.CharField(max_length=BLIND_INDEX_LENGTH, db_index=True, editable=False)
    password = models.CharField(max_length=128)
    title = models.CharField(max_length=10, choices=Title.choices, blank=True)
    first_name = EncryptedCharField(max_length=150, blank=True)
    middle_name = EncryptedCharField(max_length=150, blank=True)
    last_name = EncryptedCharField(max_length=150, blank=True)
    country = CountryField(blank=True)
    # The region whose international prefix was chosen (e.g. "ES"), to refill step 1.
    phone_region = models.CharField(max_length=2, blank=True)
    phone = EncryptedCharField(max_length=PHONE_MAX_LENGTH, blank=True)
    phone_index = models.CharField(max_length=BLIND_INDEX_LENGTH, blank=True, db_index=True,
                                   editable=False)
    how_found = models.CharField(max_length=30, choices=HowFound.choices, blank=True)
    how_found_other = EncryptedCharField(max_length=HOW_FOUND_OTHER_MAX_LENGTH, blank=True)
    # Slug of the first step not yet saved (see signup.steps.STEPS).
    step = models.CharField(max_length=50)
    attempt = models.ForeignKey('eventlog.Attempt', null=True, blank=True,
                                on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Copied to the new user at account creation (signup.views).
    USER_FIELDS = ('title', 'first_name', 'middle_name', 'last_name', 'country', 'phone',
                   'how_found', 'how_found_other')

    def __str__(self):
        return f'Pending registration {self.pk}'

    def set_blind_indexes(self):
        self.email_index = email_index(self.email)
        self.phone_index = phone_index_or_blank(self.phone)

    def save(self, *args, **kwargs):
        self.set_blind_indexes()
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            extra = {'email': 'email_index', 'phone': 'phone_index'}
            kwargs['update_fields'] = {*update_fields,
                                       *(extra[f] for f in update_fields if f in extra)}
        super().save(*args, **kwargs)
