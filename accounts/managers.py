from django.contrib.auth.base_user import BaseUserManager

from .crypto import email_index, phone_index


class UserManager(BaseUserManager):
    """Manager for `accounts.User`, whose username is the email address."""

    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        """Strip surrounding spaces and lowercase the domain (the local part keeps its case)."""
        return super().normalize_email((email or '').strip())

    def by_email(self, email):
        """Users with this email whatever its case, found through the blind index
        (the email itself is encrypted and cannot be queried)."""
        return self.filter(email_index=email_index(email))

    def by_phone(self, phone):
        """Users with this mobile (E.164), found through the blind index (the phone itself
        is encrypted and cannot be queried)."""
        return self.filter(phone_index=phone_index(phone))

    def get_by_natural_key(self, username):
        """Log in by email whatever its case (signup treats case variants as the same address)."""
        return self.by_email(username).get()

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('An email address is required.')
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        return self._create_user(email, password, **extra_fields)
