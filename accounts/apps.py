from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from . import login_guard
        from .sessions import on_user_logged_in

        # The field key checks (mytp_core.E001, E002) are registered by the mytp_core app.

        # One session per account (accounts.sessions): every login, whichever its way.
        user_logged_in.connect(on_user_logged_in, dispatch_uid='accounts.one_session')
        # Signup's auto-login is recorded as a login (accounts.login_guard, QA A2).
        user_logged_in.connect(login_guard.on_user_logged_in,
                               dispatch_uid='accounts.signup_login_record')
