"""System checks of the storage settings (`manage.py check`, `migrate`):

- storage.E001: storage is in use (STORAGE_BUCKET set, or STORAGE_SAMPLE_UPLOADS on) but
  a setting the bucket needs is empty;
- storage.E002: STORAGE_SAMPLE_UPLOADS on a public site (ROBOTS_NOINDEX off): the sample
  purpose is for staging only;
- storage.E003: STORAGE_ABORT_INCOMPLETE_DAYS below 1;
- storage.E004: storage is in use but STORAGE_CLAMD_SOCKET is empty: no upload could be
  scanned, so none would ever become downloadable (epic-platform entry 12).
"""
from django.core.checks import Error, register

SETTING_MISSING = 'storage.E001'
SAMPLE_ON_PUBLIC_SITE = 'storage.E002'
BAD_ABORT_DAYS = 'storage.E003'
CLAMD_SOCKET_MISSING = 'storage.E004'


@register()
def check_storage_settings(app_configs=None, **kwargs):
    from django.conf import settings

    from . import s3

    errors = []
    sample = getattr(settings, 'STORAGE_SAMPLE_UPLOADS', False)
    if getattr(settings, 'STORAGE_BUCKET', '') or sample:
        missing = s3.missing_setting()
        if missing:
            errors.append(Error(
                f'{missing} is not set, so no file can be uploaded or downloaded.',
                hint='Set every STORAGE_* setting in settings.env '
                     '(deploy/README.md, "Object storage").',
                id=SETTING_MISSING))
        if not getattr(settings, 'STORAGE_CLAMD_SOCKET', ''):
            errors.append(Error(
                'STORAGE_CLAMD_SOCKET is not set, so no upload can be scanned and none '
                'becomes downloadable.',
                hint='Set STORAGE_CLAMD_SOCKET=/run/clamav/clamd.ctl in settings.env '
                     '(deploy/README.md, "Object storage" -> "Scanning").',
                id=CLAMD_SOCKET_MISSING))
    if sample and not getattr(settings, 'ROBOTS_NOINDEX', False):
        errors.append(Error(
            'STORAGE_SAMPLE_UPLOADS is True on a public site (ROBOTS_NOINDEX is False): '
            'the sample uploads are for staging only.',
            hint='Remove STORAGE_SAMPLE_UPLOADS from settings.env.',
            id=SAMPLE_ON_PUBLIC_SITE))
    days = getattr(settings, 'STORAGE_ABORT_INCOMPLETE_DAYS', 7)
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        errors.append(Error('STORAGE_ABORT_INCOMPLETE_DAYS must be a whole number of days, '
                            '1 or more.', id=BAD_ABORT_DAYS))
    return errors
