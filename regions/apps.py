from django.apps import AppConfig


class RegionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'regions'
    verbose_name = 'Regions'

    def ready(self):
        from integration import config

        from . import applier, checks  # noqa: F401  (checks registers regions.E001)
        # The snapshot's country map (epic-catalogue entry 3): regions.applier.
        config.register_section_applier('country_map', applier.apply)
