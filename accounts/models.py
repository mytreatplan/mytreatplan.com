from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from .managers import UserManager


class User(AbstractUser):
    """Project user model. The email address is the username (signup story 2.1)."""

    username = None
    email = models.EmailField(_('email address'), unique=True)

    USERNAME_FIELD = 'email'
    EMAIL_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            # `unique=True` is case-sensitive in PostgreSQL; Ana@x.com and ana@x.com are one address.
            models.UniqueConstraint(Lower('email'), name='accounts_user_email_ci_unique'),
        ]

    def __str__(self):
        return self.email
