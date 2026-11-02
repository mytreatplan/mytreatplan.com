"""The regions app's system checks, registered from RegionsConfig.ready()."""
from django.conf import settings
from django.core.checks import Error, register

GEOIP_OVERRIDE_ON_PUBLIC_SITE = 'regions.E001'


@register('regions')
def check_geoip_override(app_configs, **kwargs):
    """Fail every `check` (and `migrate`) while the staging geo-IP override header is on
    and ROBOTS_NOINDEX is off, i.e. on a public site: any visitor could fake a country."""
    if not getattr(settings, 'GEOIP_OVERRIDE_HEADER_ENABLED', False):
        return []
    if getattr(settings, 'ROBOTS_NOINDEX', False):
        return []
    return [Error(
        'GEOIP_OVERRIDE_HEADER_ENABLED is True on a public site (ROBOTS_NOINDEX is False): '
        'any visitor could fake their country with the X-MyTP-Geo-Country header.',
        hint='The override is for staging only: remove GEOIP_OVERRIDE_HEADER_ENABLED from '
             'settings.env, or set ROBOTS_NOINDEX=True on a staging site.',
        id=GEOIP_OVERRIDE_ON_PUBLIC_SITE,
    )]
