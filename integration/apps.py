from django.apps import AppConfig


class IntegrationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'integration'
    verbose_name = 'Interface with the admin site'

    def ready(self):
        from . import checks  # noqa: F401  (registers the interface settings checks)
