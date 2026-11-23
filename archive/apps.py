from django.apps import AppConfig


class ArchiveConfig(AppConfig):
    """The account archive (epic-ordering 9.12): the write-once, encrypted, per-region
    archive of an erased account and its patients, written by the purge."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'archive'
    verbose_name = 'Account archive'
