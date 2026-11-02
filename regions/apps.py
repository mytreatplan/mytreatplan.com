from django.apps import AppConfig


class RegionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'regions'
    verbose_name = 'Regions'

    def ready(self):
        from . import checks  # noqa: F401  (registers regions.E001)
