from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from .crypto import BLIND_INDEX_LENGTH, EncryptedCharField, EncryptedEmailField, email_index
from .managers import UserManager


class User(AbstractUser):
    """Project user model. The email address is the username (signup story 2.1).

    Email and names are encrypted in the app (story 2.2, accounts.crypto). The email is
    found, and kept unique whatever its case, through `email_index`, its blind index,
    set on every save.
    """

    username = None
    email = EncryptedEmailField(_('email address'), max_length=254)
    email_index = models.CharField(max_length=BLIND_INDEX_LENGTH, unique=True, editable=False)
    first_name = EncryptedCharField(_('first name'), max_length=150, blank=True)
    last_name = EncryptedCharField(_('last name'), max_length=150, blank=True)

    USERNAME_FIELD = 'email'
    EMAIL_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email

    def set_blind_indexes(self):
        self.email_index = email_index(self.email)

    def save(self, *args, **kwargs):
        self.set_blind_indexes()
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'email' in update_fields:
            kwargs['update_fields'] = {*update_fields, 'email_index'}
        super().save(*args, **kwargs)
