from django.apps import AppConfig


class SignupConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'signup'

    def ready(self):
        from . import checks  # noqa: F401  (registers signup.E001)
