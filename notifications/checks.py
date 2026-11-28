"""The notifications app's system checks, registered from NotificationsConfig.ready()."""
from django.conf import settings
from django.core.checks import Error, register

from .sms import CREDENTIAL_SETTINGS, TWILIO_SENDER

TWILIO_SMS_SETTINGS_MISSING = 'notifications.E001'


@register('notifications')
def check_twilio_sms_settings(app_configs, **kwargs):
    """Fail every `check` (and `migrate`) while the Twilio SMS sender is selected and one of
    its settings is empty. Names the settings only, never a value."""
    if getattr(settings, 'SMS_SENDER', '') != TWILIO_SENDER:
        return []
    missing = [name for name in CREDENTIAL_SETTINGS
               if not str(getattr(settings, name, '') or '').strip()]
    if not missing:
        return []
    return [Error(
        f"The Twilio SMS sender is selected but {', '.join(missing)} "
        f"{'is' if len(missing) == 1 else 'are'} empty: no SMS fallback can be sent.",
        hint='Type the Twilio settings into settings.env on the server '
             '(deploy/README.md, "Twilio Messaging sender").',
        id=TWILIO_SMS_SETTINGS_MISSING,
    )]
