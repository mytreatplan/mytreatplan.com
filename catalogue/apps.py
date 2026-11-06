from django.apps import AppConfig


class CatalogueConfig(AppConfig):
    """The catalogue the admin site sends in the configuration snapshot (epic-catalogue
    entry 2): read-only tables (catalogue.models) filled by the snapshot apply
    (catalogue.applier, registered here with integration.config), the resolver that
    prices (catalogue.resolver) and the command `price_catalogue`."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'catalogue'
    verbose_name = 'Catalogue'

    def ready(self):
        from integration import config

        from . import applier
        config.register_section_applier('catalogue', applier.apply)
