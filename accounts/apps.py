from django.apps import AppConfig
from django.core import checks


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from .crypto import check_field_keys
        from . import login_guard
        from .sessions import on_user_logged_in

        # Missing or malformed field keys stop `check`, `runserver` and `migrate`.
        checks.register(check_field_keys, checks.Tags.security)

        # One session per account (accounts.sessions): every login, whichever its way.
        user_logged_in.connect(on_user_logged_in, dispatch_uid='accounts.one_session')
        # Signup's auto-login is recorded as a login (accounts.login_guard, QA A2).
        user_logged_in.connect(login_guard.on_user_logged_in,
                               dispatch_uid='accounts.signup_login_record')
