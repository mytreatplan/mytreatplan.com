from django.contrib.auth.base_user import BaseUserManager


class UserManager(BaseUserManager):
    """Manager for `accounts.User`, whose username is the email address."""

    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        """Strip surrounding spaces and lowercase the domain (the local part keeps its case)."""
        return super().normalize_email((email or '').strip())

    def get_by_natural_key(self, username):
        """Log in by email whatever its case (signup treats case variants as the same address)."""
        return self.get(**{f'{self.model.USERNAME_FIELD}__iexact': username})

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
