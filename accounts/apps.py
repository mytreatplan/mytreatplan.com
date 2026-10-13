from django.apps import AppConfig
from django.core import checks


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        from .crypto import check_field_keys

        # Missing or malformed field keys stop `check`, `runserver` and `migrate`.
        checks.register(check_field_keys, checks.Tags.security)
