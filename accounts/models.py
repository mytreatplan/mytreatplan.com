from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Project user model.

    Starts identical to Django's default user so it can be extended later
    (signup story 2.1) without swapping AUTH_USER_MODEL after migrations exist.
    """
