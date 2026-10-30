import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone
from django_countries.fields import CountryField

from accounts.crypto import (BLIND_INDEX_LENGTH, EncryptedCharField, EncryptedEmailField,
                             email_index, phone_index)
from accounts.models import (HOW_FOUND_OTHER_MAX_LENGTH, PHONE_MAX_LENGTH, HowFound, Title,
                             phone_index_or_blank)

# How long a pending registration counts as active (warned about, resumable), from its
# `created_at`. The purge job (signup entry 10) uses the same window through `expired()`.
ACTIVE_FOR = timedelta(hours=24)


class PendingRegistrationQuerySet(models.QuerySet):
    def active(self, now=None):
        """Rows created less than ACTIVE_FOR ago."""
        return self.filter(created_at__gt=(now or timezone.now()) - ACTIVE_FOR)

    def expired(self, now=None):
        """Rows created ACTIVE_FOR ago or more: the complement of `active()`, which the
        purge job (signup.retention) deletes."""
        return self.filter(created_at__lte=(now or timezone.now()) - ACTIVE_FOR)

    def matching_email(self, email):
        """Rows with this email whatever its case (through the blind index)."""
        return self.filter(email_index=email_index(email))

    def matching_phone(self, phone):
        """Rows with this mobile (E.164; through the blind index). None without a phone."""
        if not phone:
            return self.none()
        return self.filter(phone_index=phone_index(phone))


class PendingRegistration(models.Model):
    """A signup in progress. No user exists until the email and the mobile codes are
    correct.

    The wizard keeps only this row's id in the session. The password is stored as a
    Django hash (never plaintext); the new user later receives the same hash.
    Email, names, mobile (E.164) and the "Others" text are encrypted (accounts.crypto);
    `email_index` and `phone_index` are their blind indexes, set on every save (not
    unique: two signups may be in progress for one address or number; signup entry 5
    checks them). Title, country, prefix region and how-found-us are codes.
    `created_at` / `updated_at` let the purge job (signup entry 10) drop stale rows.
    `attempt` is this signup's event log attempt (eventlog), kept after this row is gone.
    `email_verified_at` is when control of the email was proven (signup entry 5), and
    `phone_verified_at` that of the mobile (signup entry 7).
    """

    # AD-5: crosses sites; its UUID primary key is its global id.
    CROSSES_SITES = True

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
    # When control of `email` was proven: a correct email-step code, or the resume code
    # (sent to this email). Cleared when step 1 is saved with another email. A step whose
    # contact is verified is skipped (signup.steps.Step.is_done).
    email_verified_at = models.DateTimeField(null=True, blank=True)
    # When control of `phone` was proven: a correct mobile-step code. Cleared when step 1
    # is saved with another mobile. Never copied by a resume: its code proves the email only.
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PendingRegistrationQuerySet.as_manager()

    # Copied to the new user at account creation (signup.views).
    USER_FIELDS = ('title', 'first_name', 'middle_name', 'last_name', 'country', 'phone',
                   'how_found', 'how_found_other')

    # Copied into the new row when a signup is resumed (signup entry 5): the user-facing
    # data, the prefix region and the next unsaved step. Never the password hash: the
    # resume code proves control of the email, not who made the row, so the person
    # resuming sets a password again (plan-fix-resume-takeover).
    RESUMED_FIELDS = ('email', *USER_FIELDS, 'phone_region', 'step')

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

    def copy_to_new_row(self, **fields):
        """A new saved row (new id) holding this row's RESUMED_FIELDS, plus `fields` (e.g.
        the resuming attempt). The resume code went to this row's email, so the new row's
        email is marked verified (now) unless `fields` says otherwise; the mobile is not
        (`phone_verified_at` is not copied). It keeps this row's
        `created_at`, so a resume never extends the 24 h window of `active()` and the
        purge. This row is left as it is."""
        fields.setdefault('email_verified_at', timezone.now())
        new = PendingRegistration(
            **{**{name: getattr(self, name) for name in self.RESUMED_FIELDS}, **fields})
        new.save()
        # auto_now_add sets the save time; carry the original over.
        PendingRegistration.objects.filter(pk=new.pk).update(created_at=self.created_at)
        new.created_at = self.created_at
        return new
