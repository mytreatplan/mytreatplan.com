from django.apps import AppConfig


class RecordsConfig(AppConfig):
    """The records a doctor uploads for a case (epic-case-lifecycle 10.5): the
    `case_records` storage purpose, its link to the work order, and the case-actions slot
    of the patient file that shows the upload, its status and Records complete."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'records'
    verbose_name = 'Case records'

    def ready(self):
        from patients import slots

        from . import service, views
        service.register()
        slots.register(slots.CASE_ACTIONS, 'case_records', 'records/_case_records.html',
                       views.slot_context)
