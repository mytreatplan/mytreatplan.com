"""System checks of the interface settings (`manage.py check`, `migrate`): each malformed
setting is an error naming it, so the runbook's `check` shows it before a call fails.

- integration.E001: INTERFACE_SIGNING_KMS_KEY / INTERFACE_SIGNING_KEY malformed, or both set
  (a missing key is no error: /api/ then answers 503);
- integration.E002: INTERFACE_PEERS malformed (an empty entry, http:// without DEBUG);
- integration.E003: INTERFACE_PEER_KEYS malformed (an empty entry, a bad key);
- integration.E004: retired with the staging-only sample event (entry 8);
- integration.E005: the outbox cannot tell the admin site (`outbox.admin_site()`):
  INTERFACE_ADMIN_SITE not in INTERFACE_PEERS, or several INTERFACE_PEERS without it;
- integration.E006: INTERFACE_SAMPLE_RECORDS on a public site (ROBOTS_NOINDEX off): the
  sample record is for staging only (entry 5).
"""
from django.core.checks import Error, register

from mytp_core import interface

ADMIN_SITE_NOT_A_PEER = 'integration.E005'
SAMPLE_ON_PUBLIC_SITE = 'integration.E006'


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
    peers = None
    try:
        peers = parse_peers(getattr(settings, 'INTERFACE_PEERS', '') or '')
    except interface.InterfaceConfigurationError as exc:
        errors.append(Error(str(exc), id='integration.E002'))
    try:
        interface.Keyring.from_settings()
    except interface.InterfaceConfigurationError as exc:
        errors.append(Error(str(exc), id='integration.E003'))
    admin = (getattr(settings, 'INTERFACE_ADMIN_SITE', '') or '').strip()
    # No peers and no admin site: the interface is not set up yet (no error). Otherwise
    # the outbox must be able to tell where its events go.
    if peers is not None and (admin or peers):
        from .outbox import admin_site
        try:
            admin_site()
        except interface.InterfaceConfigurationError as exc:
            errors.append(Error(
                f'{exc} The outbox has nowhere to send its events.',
                hint='Set INTERFACE_ADMIN_SITE to the admin site\'s INTERFACE_PEERS entry.',
                id=ADMIN_SITE_NOT_A_PEER))
    if (getattr(settings, 'INTERFACE_SAMPLE_RECORDS', False)
            and not getattr(settings, 'ROBOTS_NOINDEX', False)):
        errors.append(Error(
            'INTERFACE_SAMPLE_RECORDS is True on a public site (ROBOTS_NOINDEX is False): '
            'the sample record is for staging only.',
            hint='Remove INTERFACE_SAMPLE_RECORDS from settings.env.',
            id=SAMPLE_ON_PUBLIC_SITE))
    return errors
