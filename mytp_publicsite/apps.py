from django.apps import AppConfig


class MytpPublicsiteConfig(AppConfig):
    name = 'mytp_publicsite'

    def ready(self):
        from . import checks  # noqa: F401  (registers the deploy check)
