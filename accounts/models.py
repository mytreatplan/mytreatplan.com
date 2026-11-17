import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import UUID4
from django.utils.translation import gettext_lazy as _
from django_countries.fields import CountryField

from catalogue.pricing import VIES_NOT_APPLICABLE, VIES_STATUSES
from regions.profiles import REGION_CHOICES

from .crypto import (BLIND_INDEX_LENGTH, EncryptedCharField, EncryptedEmailField, blind_index,
                     email_index, phone_index)
from .managers import UserManager


class Title(models.TextChoices):
    """Title / treatment (signup-fields.md, step 1). Stored as the code."""

    DR = 'dr', _('Dr.')
    PROF = 'prof', _('Prof.')
    MR = 'mr', _('Mr')
    MRS = 'mrs', _('Mrs')
    MS = 'ms', _('Ms')
    ENG = 'eng', _('Eng.')


class HowFound(models.TextChoices):
    """"How did you find out about us?" (signup-fields.md, step 1). Stored as the code."""

    SEARCH_ENGINE = 'search_engine', _('Search Engine (e.g. Google)')
    SOCIAL_MEDIA = 'social_media', _('Social Media (Instagram, Facebook)')
    LINKEDIN = 'linkedin', _('LinkedIn')
    WHATSAPP = 'whatsapp', _('WhatsApp')
    EMAIL = 'email', _('E-mail')
    PHONE_CALL = 'phone_call', _('Telephone Call')
    CONFERENCE = 'conference', _('Conference or Congress')
    OTHERS = 'others', _('Others')


# Longest E.164 number: "+" and 15 digits.
PHONE_MAX_LENGTH = 16
HOW_FOUND_OTHER_MAX_LENGTH = 200


def phone_index_or_blank(phone):
    """The phone's blind index, or '' when there is no phone."""
    return phone_index(phone) if phone else ''


ERASED_EMAIL_INDEX_PREFIX = 'erased:'


def instance_region():
    """The region of this instance (settings.REGION_PROFILE): a new user's `home_region`."""
    return settings.REGION_PROFILE.code


def erased_email_tombstone(pk):
    """The `email_index` of an erased account: unique, and never a real blind index (those
    are hex), so signup and the duplicate checks no longer find the account."""
    return f'{ERASED_EMAIL_INDEX_PREFIX}{pk}'


class User(AbstractUser):
    """Project user model. The email address is the username (signup story 2.1).

    Email, names, mobile and the "Others" text are encrypted in the app (story 2.2,
    accounts.crypto). The email is found, and kept unique whatever its case, through
    `email_index`, its blind index, set on every save. The mobile (E.164) has its blind
    index `phone_index`, unique among the accounts that have one (signup entry 5 checks it
    first; the database rule since epic-doctor-profile entry 3).
    Title, country and how-found-us are stored as codes.

    An erased account (accounts.erasure) keeps its email until `purge_after`, as evidence
    with its legal choices: its `email_index` becomes a tombstone (`erased:<pk>`), so the
    same email can register again, and the real index moves to `erased_email_index`
    (not unique), where the admin's exact-email search still finds it. Its `phone_index`
    is blank.
    """

    CROSSES_SITES = True

    username = None
    # AD-5: the id other sites know this record by (the integer key never leaves the
    # instance). Set once, never changed, never shown in a form. The database default
    # gives rows inserted without it (the previous release during a deploy) their own.
    uid = models.UUIDField(default=uuid.uuid4, db_default=UUID4(), unique=True,
                           editable=False)
    # R2 / AD-14: the region whose instance created the account, set once at creation
    # (whatever the country) and never recomputed nor shown in a form. Its database default
    # is region-neutral ('', so the migrations are the same on every instance): rows the
    # previous release inserts while a deploy migrates get '', which accounts 0013 fills with
    # the instance region. A row the previous release inserts after that fill (the seconds
    # before release.sh switches) keeps '': read '' as "created by the previous release",
    # and re-run the fill if `User.objects.filter(home_region='')` finds one after a deploy.
    home_region = models.CharField(_('home region'), max_length=2, choices=REGION_CHOICES,
                                   default=instance_region, db_default='', editable=False)
    email = EncryptedEmailField(_('email address'), max_length=254)
    email_index = models.CharField(max_length=BLIND_INDEX_LENGTH, unique=True, editable=False)
    title = models.CharField(_('title'), max_length=10, choices=Title.choices, blank=True)
    first_name = EncryptedCharField(_('first name'), max_length=150, blank=True)
    middle_name = EncryptedCharField(_('middle name or initial'), max_length=150, blank=True)
    last_name = EncryptedCharField(_('last name'), max_length=150, blank=True)
    country = CountryField(_('country / region'), blank=True)
    phone = EncryptedCharField(_('mobile phone number'), max_length=PHONE_MAX_LENGTH,
                               blank=True)
    phone_index = models.CharField(max_length=BLIND_INDEX_LENGTH, blank=True, db_index=True,
                                   editable=False)
    how_found = models.CharField(_('how they found out about us'), max_length=30,
                                 choices=HowFound.choices, blank=True)
    how_found_other = EncryptedCharField(_('how they found out about us (others)'),
                                         max_length=HOW_FOUND_OTHER_MAX_LENGTH, blank=True)
    # Account erasure (accounts.erasure): when it was erased, and when the daily
    # `purgeerased` job deletes it for good with its legal choices.
    erased_at = models.DateTimeField(_('erased at'), null=True, blank=True, editable=False)
    purge_after = models.DateTimeField(_('purge after'), null=True, blank=True,
                                       db_index=True, editable=False)
    erased_email_index = models.CharField(max_length=BLIND_INDEX_LENGTH, blank=True,
                                          db_index=True, editable=False)
    # The key of the account's one active session (accounts.sessions, epic-login entry 6):
    # set at every login, cleared by `end_all_sessions`. Never shown.
    current_session_key = models.CharField(max_length=40, null=True, blank=True,
                                           editable=False)
    # The client record sync (accounts.client_sync, epic-admin-interface entry 8): the
    # version of the last client event sent to the admin site (its aggregate version),
    # bumped in the transaction of every client write. 0: never sent. The database default
    # lets the previous release insert rows while a deploy migrates.
    sync_version = models.BigIntegerField(default=0, db_default=0, editable=False)
    # The remembered devices (accounts.devices) given before this time are forgotten: set
    # by a mobile change (accounts.profile.confirm_contact_change, epic-doctor-profile
    # entry 3), since the mobile is a second-factor channel. None: none forgotten so far.
    devices_valid_after = models.DateTimeField(null=True, blank=True, editable=False)

    USERNAME_FIELD = 'email'
    EMAIL_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            # No two accounts share a mobile (epic-doctor-profile entry 3, decision of
            # 2026-11-15); erased accounts and accounts without a mobile have a blank
            # index and are left out. Migration 0017 stops, listing the account ids, when
            # duplicates already exist.
            models.UniqueConstraint(fields=('phone_index',), condition=~models.Q(phone_index=''),
                                    name='accounts_user_phone_index_unique'),
        ]

    def __str__(self):
        return self.email

    @property
    def is_erased(self):
        return self.erased_at is not None

    def set_blind_indexes(self):
        if self.is_erased:
            if self.pk is None:
                raise ValueError('An erased account must already be saved.')
            self.email_index = erased_email_tombstone(self.pk)
            self.erased_email_index = email_index(self.email)
            self.phone_index = ''
        else:
            self.email_index = email_index(self.email)
            self.erased_email_index = ''
            self.phone_index = phone_index_or_blank(self.phone)

    # The blind indexes that follow each field, added to a save's `update_fields`.
    _INDEXES_OF = {
        'email': ('email_index', 'erased_email_index'),
        'phone': ('phone_index',),
        'erased_at': ('email_index', 'erased_email_index', 'phone_index'),
    }

    def save(self, *args, **kwargs):
        self.set_blind_indexes()
        update_fields = kwargs.get('update_fields')
        if (update_fields is None and not self._state.adding and self.pk is not None
                and not kwargs.get('force_insert')):
            # `sync_version` is written only by accounts.client_sync, under the row's lock:
            # an ordinary save of a loaded account never writes back the value it read
            # (a concurrent sync would otherwise lose its version).
            deferred = self.get_deferred_fields()
            update_fields = [field.name for field in self._meta.concrete_fields
                             if not field.primary_key and field.name != 'sync_version'
                             and field.attname not in deferred]
            kwargs['update_fields'] = update_fields
        if update_fields is not None:
            kwargs['update_fields'] = {
                *update_fields,
                *(index for f in update_fields for index in self._INDEXES_OF.get(f, ()))}
        super().save(*args, **kwargs)


class Profession(models.TextChoices):
    """The doctor's profession (epic-doctor-profile entry 2, user decision 2026-11-15).
    Stored as the code; the code travels to the admin site (`client.updated`)."""

    ORTHODONTIST = 'orthodontist', _('Orthodontist')
    GENERAL_DENTIST = 'general_dentist', _('General dentist')
    PAEDIATRIC_DENTIST = 'paediatric_dentist', _('Paediatric dentist')
    PROSTHODONTIST = 'prosthodontist', _('Prosthodontist')
    PERIODONTIST = 'periodontist', _('Periodontist')
    IMPLANTOLOGIST = 'implantologist', _('Implantologist')
    ORAL_SURGEON = 'oral_maxillofacial_surgeon', _('Oral and maxillofacial surgeon')
    OTHER = 'other', _('Other')


class Profile(models.Model):
    """The doctor's personal data beyond the account (epic-doctor-profile entry 2): one
    row per user, created on the first save of the personal-data page (accounts.profile,
    the only doctor-side writer). No backfill: a user without a row reads as blank.

    `city`, `landline` (E.164, as the mobile) and `alternative_email` are contact data,
    encrypted as the account's email and phone (no blind index: nothing looks a user up
    by them). `language` is the preferred language code (one of the site's languages);
    `time_zone` an IANA zone name, stored only (pages keep the site's zone). All six
    travel to the admin site in `client.updated` (accounts.client_sync). Deleted with
    the user; cleared when the account is erased (accounts.erasure).

    `photo` and `photo_pending` (entry 4, accounts.photo) are the doctor's profile photo
    and the upload waiting for its scan, stored objects (storage.StoredObject, purpose
    `profile_photo`) in the region's bucket. They stay regional: never in SYNCED_FIELDS,
    and a photo change emits no event. SET_NULL: the object's deletion (always through
    storage.service.delete) unlinks it."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='profile')
    language = models.CharField(_('language'), max_length=10, blank=True)
    city = EncryptedCharField(_('city'), max_length=150, blank=True)
    time_zone = models.CharField(_('time zone'), max_length=64, blank=True)
    profession = models.CharField(_('profession'), max_length=30,
                                  choices=Profession.choices, blank=True)
    landline = EncryptedCharField(_('landline'), max_length=PHONE_MAX_LENGTH, blank=True)
    alternative_email = EncryptedEmailField(_('alternative email'), max_length=254,
                                            blank=True)
    photo = models.ForeignKey('storage.StoredObject', on_delete=models.SET_NULL, null=True,
                              blank=True, related_name='+')
    photo_pending = models.ForeignKey('storage.StoredObject', on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    # The fields that travel in `client.updated`.
    SYNCED_FIELDS = ('city', 'time_zone', 'profession', 'landline', 'alternative_email',
                     'language')

    def __str__(self):
        # Never a name or contact data.
        return f'Profile {self.pk}'


class PendingContactChange(models.Model):
    """A mobile or contact-email change waiting for its code (epic-doctor-profile entry 3,
    accounts.profile): the new value, encrypted, and its blind index, for one account and
    kind. One row per account and kind (a new start replaces it); it lives
    PENDING_CHANGE_SECONDS (the code's validity) from `created_at`, and is deleted once
    confirmed, refused or found expired. The session holds only the row's id; nothing
    logs the value."""

    MOBILE = 'mobile'
    EMAIL = 'email'
    KINDS = [(MOBILE, _('mobile phone number')), (EMAIL, _('contact email'))]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='pending_contact_changes')
    kind = models.CharField(max_length=10, choices=KINDS)
    value = EncryptedCharField(max_length=254)
    value_index = models.CharField(max_length=BLIND_INDEX_LENGTH, editable=False)
    created_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('user', 'kind'),
                                    name='accounts_pending_contact_change_one_per_kind'),
        ]

    def __str__(self):
        # Never the value.
        return f'Pending {self.kind} change {self.pk}'

    def save(self, *args, **kwargs):
        self.value_index = (phone_index(self.value) if self.kind == self.MOBILE
                            else email_index(self.value))
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'value' in update_fields:
            kwargs['update_fields'] = {*update_fields, 'value_index'}
        super().save(*args, **kwargs)


class AddressFields(models.Model):
    """A postal address (epic-doctor-profile entry 5): the lines, city, postal code and
    province are encrypted (AD-2); the country is a plain `CountryField`, since queries and
    the region and banned checks need it. Every part may be blank at the model level: the
    rules of which are required depend on the country and live with the writers
    (accounts.fiscal). The shipping addresses (entry 7) reuse it."""

    address_line1 = EncryptedCharField(_('address line 1'), max_length=200, blank=True)
    address_line2 = EncryptedCharField(_('address line 2'), max_length=200, blank=True)
    city = EncryptedCharField(_('city'), max_length=150, blank=True)
    postal_code = EncryptedCharField(_('postal code'), max_length=30, blank=True)
    province = EncryptedCharField(_('province'), max_length=150, blank=True)
    country = CountryField(_('country'), blank=True)

    class Meta:
        abstract = True


def normalize_fiscal_id(value):
    """A tax id or VAT id as stored and indexed: uppercase, without spaces or dashes."""
    return ''.join(ch for ch in (value or '').upper() if not ch.isspace() and ch != '-')


def fiscal_id_index_or_blank(value):
    """The blind index of a fiscal id, or '' when there is none."""
    normalized = normalize_fiscal_id(value)
    return blind_index(normalized) if normalized else ''


class FiscalEntity(AddressFields):
    """Who a doctor bills as (epic-doctor-profile entry 5, accounts.fiscal, the only
    writer): a person (first and last name) or a company (legal name), with a tax id, an
    address and an optional billing email; for billing country AE also a VAT id. Several
    per doctor, at most one default among the non-archived ones (a database rule).

    The names, tax id, VAT id, billing email and the address parts are encrypted. The tax
    id and the VAT id each have a non-unique blind index (`tax_id_index`, `vat_id_index`,
    set in `save()`, of the normalised value) for the per-country checks (entry 6) and
    staff search. `version` goes up on each change (AD-15; entry 9 copies the entity to
    the admin client record). `used_at` is set by epic-payments (`fiscal.mark_used`) when
    an order uses the entity; from then on "delete" archives (`archived_at`) instead.
    Regional: never in `client.updated`. Deleted with the user and by the erasure.
    """

    PERSON = 'person'
    COMPANY = 'company'
    KINDS = [(PERSON, _('Individual')), (COMPANY, _('Company'))]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='fiscal_entities')
    kind = models.CharField(_('kind'), max_length=10, choices=KINDS)
    first_name = EncryptedCharField(_('name'), max_length=150, blank=True)
    last_name = EncryptedCharField(_('surname(s)'), max_length=150, blank=True)
    legal_name = EncryptedCharField(_('legal name'), max_length=200, blank=True)
    tax_id = EncryptedCharField(_('tax id'), max_length=40, blank=True)
    tax_id_index = models.CharField(max_length=BLIND_INDEX_LENGTH, blank=True, db_index=True,
                                    editable=False)
    vat_id = EncryptedCharField(_('VAT id'), max_length=40, blank=True)
    vat_id_index = models.CharField(max_length=BLIND_INDEX_LENGTH, blank=True, db_index=True,
                                    editable=False)
    billing_email = EncryptedEmailField(_('billing email'), max_length=254, blank=True)
    is_default = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    used_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    # The VIES check of an EU billing country (entry 6; accounts.fiscal is the only
    # writer, never the editor): the status uses the pricing resolver's own values, and the
    # retry fields drive the `retry_vies` job while it is `pending`.
    vies_status = models.CharField(max_length=14, choices=[(v, v) for v in VIES_STATUSES],
                                   default=VIES_NOT_APPLICABLE, editable=False)
    vies_checked_at = models.DateTimeField(null=True, blank=True, editable=False)
    vies_pending_since = models.DateTimeField(null=True, blank=True, editable=False)
    vies_attempts = models.PositiveSmallIntegerField(default=0, editable=False)
    vies_next_try_at = models.DateTimeField(null=True, blank=True, db_index=True,
                                            editable=False)
    vies_alerted_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('user',),
                condition=models.Q(is_default=True, archived_at__isnull=True),
                name='accounts_fiscalentity_one_default'),
        ]

    def __str__(self):
        # Never a name or an id number.
        return f'Fiscal entity {self.pk}'

    @property
    def display_name(self):
        if self.kind == self.COMPANY:
            return self.legal_name
        return f'{self.first_name} {self.last_name}'.strip()

    @property
    def masked_tax_id(self):
        """The tax id with all but its last 4 characters hidden."""
        value = self.tax_id or ''
        if len(value) <= 4:
            return '•' * len(value)
        return '••••' + value[-4:]

    def save(self, *args, **kwargs):
        self.tax_id_index = fiscal_id_index_or_blank(self.tax_id)
        self.vat_id_index = fiscal_id_index_or_blank(self.vat_id)
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            extra = {'tax_id_index'} if 'tax_id' in update_fields else set()
            if 'vat_id' in update_fields:
                extra.add('vat_id_index')
            kwargs['update_fields'] = {*update_fields, *extra}
        super().save(*args, **kwargs)
