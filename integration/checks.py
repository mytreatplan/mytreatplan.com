"""System checks of the interface settings (`manage.py check`, `migrate`): each malformed
setting is an error naming it, so the runbook's `check` shows it before a call fails.

- integration.E001: INTERFACE_SIGNING_KMS_KEY / INTERFACE_SIGNING_KEY malformed, or both set
  (a missing key is no error: /api/ then answers 503);
- integration.E002: INTERFACE_PEERS malformed (an empty entry, http:// without DEBUG);
- integration.E003: INTERFACE_PEER_KEYS malformed (an empty entry, a bad key).
"""
from django.core.checks import Error, register

from mytp_core import interface


@register()
def check_interface_settings(app_configs=None, **kwargs):
    from django.conf import settings

    from .peers import parse_peers

    errors = []
    try:
        interface.signing_key_setting()
    except interface.SigningKeyUnavailable:
        pass
    except interface.InterfaceConfigurationError as exc:
        errors.append(Error(str(exc), id='integration.E001'))
    try:
        parse_peers(getattr(settings, 'INTERFACE_PEERS', '') or '')
    except interface.InterfaceConfigurationError as exc:
        errors.append(Error(str(exc), id='integration.E002'))
    try:
        interface.Keyring.from_settings()
    except interface.InterfaceConfigurationError as exc:
        errors.append(Error(str(exc), id='integration.E003'))
    return errors
