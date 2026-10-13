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


class User(AbstractUser):
    """Project user model. The email address is the username (signup story 2.1).

    Email, names, mobile and the "Others" text are encrypted in the app (story 2.2,
    accounts.crypto). The email is found, and kept unique whatever its case, through
    `email_index`, its blind index, set on every save. The mobile (E.164) has its blind
    index `phone_index`, indexed but not unique (uniqueness is checked by signup entry 5).
    Title, country and how-found-us are stored as codes.
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

    USERNAME_FIELD = 'email'
    EMAIL_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email

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
