from django.apps import AppConfig


class StorageConfig(AppConfig):
    name = 'storage'
    verbose_name = 'Stored files'

    def ready(self):
        from . import checks, sample  # noqa: F401 - registers the system checks

        sample.register()
