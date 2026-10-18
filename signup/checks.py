"""The signup's system checks, registered from SignupConfig.ready()."""
from django.conf import settings
from django.core.checks import Error, register

from .twilio_verify import CREDENTIAL_SETTINGS, TWILIO_PROVIDER

TWILIO_CREDENTIALS_MISSING = 'signup.E001'


@register('signup')
def check_twilio_credentials(app_configs, **kwargs):
    """Fail every `check` (and `migrate`) while Twilio Verify is the verification provider
    and one of its credentials is empty. Names the settings only, never a value."""
    if getattr(settings, 'VERIFICATION_PROVIDER', '') != TWILIO_PROVIDER:
        return []
    missing = [name for name in CREDENTIAL_SETTINGS
               if not str(getattr(settings, name, '') or '').strip()]
    if not missing:
        return []
    return [Error(
        f"Twilio Verify is the verification provider but {', '.join(missing)} "
        f"{'is' if len(missing) == 1 else 'are'} empty: no signup code can be sent.",
        hint='Type the Twilio credentials into settings.env on the server '
             '(deploy/README.md, "Twilio Verify").',
        id=TWILIO_CREDENTIALS_MISSING,
    )]
