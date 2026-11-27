from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """Messages to doctors and staff about their cases (epic-case-lifecycle 10.2): one
    `Message` row per message and channel, written in the case move's transaction by the
    receivers registered here, and sent after the commit by `notifications.tasks`. The
    SMS fallback (10.3) is a second row, channel `sms`, written when an email ends `failed`;
    a later story adds the "assigned" message (10.10)."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'notifications'
    verbose_name = 'Notifications'

    def ready(self):
        from . import checks, receivers  # noqa: F401  (checks registers notifications.E001)
        receivers.register_all()
