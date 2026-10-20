from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_countries.fields import CountryField

from .crypto import (BLIND_INDEX_LENGTH, EncryptedCharField, EncryptedEmailField, email_index,
                     phone_index)
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


def erased_email_tombstone(pk):
    """The `email_index` of an erased account: unique, and never a real blind index (those
    are hex), so signup and the duplicate checks no longer find the account."""
    return f'{ERASED_EMAIL_INDEX_PREFIX}{pk}'


class User(AbstractUser):
    """Project user model. The email address is the username (signup story 2.1).

    Email, names, mobile and the "Others" text are encrypted in the app (story 2.2,
    accounts.crypto). The email is found, and kept unique whatever its case, through
    `email_index`, its blind index, set on every save. The mobile (E.164) has its blind
    index `phone_index`, indexed but not unique (uniqueness is checked by signup entry 5).
    Title, country and how-found-us are stored as codes.

    An erased account (accounts.erasure) keeps its email until `purge_after`, as evidence
    with its legal choices: its `email_index` becomes a tombstone (`erased:<pk>`), so the
    same email can register again, and the real index moves to `erased_email_index`
    (not unique), where the admin's exact-email search still finds it. Its `phone_index`
    is blank.
    """

    username = None
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

    USERNAME_FIELD = 'email'
    EMAIL_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

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
        if update_fields is not None:
            kwargs['update_fields'] = {
                *update_fields,
                *(index for f in update_fields for index in self._INDEXES_OF.get(f, ()))}
        super().save(*args, **kwargs)
