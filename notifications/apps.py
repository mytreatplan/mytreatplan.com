from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """Messages to doctors and staff about their cases (epic-case-lifecycle 10.2): one
    `Message` row per message and channel, written in the case move's transaction by the
    receivers registered here, and sent after the commit by `notifications.tasks`. Later
    stories add the SMS fallback (10.3) and the "assigned" message (10.10) on the same row."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'notifications'
    verbose_name = 'Notifications'

    def ready(self):
        from . import receivers
        receivers.register_all()
