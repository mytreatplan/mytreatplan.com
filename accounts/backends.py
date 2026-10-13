from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    """Log in by email address.

    `User.email` is encrypted, so the database cannot make it unique (each ciphertext
    differs); uniqueness lives on `User.email_index`, the email's blind index. Django
    requires a non-default backend for a non-unique USERNAME_FIELD (auth.E003): this one
    is ModelBackend unchanged, and `UserManager.get_by_natural_key` finds the user
    through the index.
    """
