"""System checks of the payment settings (`manage.py check`, `migrate`):

- payments.E001: PAYMENTS_FAKE_GATEWAY on a public site (DEBUG off and ROBOTS_NOINDEX off):
  the fake gateway marks any order paid and is for development and staging only;
- payments.E002: PAYMENTS_TEST_ORDERS on a public site, likewise;
- payments.E003: a gateway adapter available on a public site whose secret (one of its
  `required_settings`, from settings.env) is not set;
- payments.E004: the Redsys adapter enabled on a public site with REDSYS_ENVIRONMENT not
  `production`, or with Redsys's public test terminal (its published key or merchant
  code): no real payment could be taken (epic-payments entry 3).
"""
from django.conf import settings
from django.core.checks import Error, register

FAKE_ON_PUBLIC_SITE = 'payments.E001'
TEST_ORDERS_ON_PUBLIC_SITE = 'payments.E002'
GATEWAY_SECRET_MISSING = 'payments.E003'
REDSYS_TEST_ON_PUBLIC_SITE = 'payments.E004'


def public_site():
    return not settings.DEBUG and not getattr(settings, 'ROBOTS_NOINDEX', False)


@register()
def check_payment_flags(app_configs=None, **kwargs):
    errors = []
    if not public_site():
        return errors
    for name, check_id in (('PAYMENTS_FAKE_GATEWAY', FAKE_ON_PUBLIC_SITE),
                           ('PAYMENTS_TEST_ORDERS', TEST_ORDERS_ON_PUBLIC_SITE)):
        if getattr(settings, name, False):
            errors.append(Error(
                f'{name} is True on a public site (DEBUG and ROBOTS_NOINDEX are off): it is '
                'for development and staging only.',
                hint=f'Remove {name} from settings.env.', id=check_id))
    return errors


@register()
def check_gateway_secrets(app_configs=None, **kwargs):
    from . import gateways
    errors = []
    if not public_site():
        return errors
    for found in gateways.available_adapters():
        for name in found.required_settings:
            if not getattr(settings, name, ''):
                errors.append(Error(
                    f'The {found.name} gateway is enabled on a public site and {name} is '
                    'not set.',
                    hint=f'Set {name} in settings.env, or do not enable the gateway.',
                    id=GATEWAY_SECRET_MISSING))
    return errors


def redsys_test_on_public_site():
    """Redsys enabled on a public site with its test environment or its public test
    terminal (payments.E004; also refused at start, PaymentsConfig.ready)."""
    from . import redsys
    if not public_site() or not redsys.enabled():
        return False
    configured = str(getattr(settings, 'REDSYS_ENVIRONMENT', '') or '').strip().lower()
    return (configured != redsys.PRODUCTION or redsys.secret_key() == redsys.PUBLIC_TEST_KEY
            or redsys.merchant_code() == redsys.PUBLIC_TEST_MERCHANT_CODE)


@register()
def check_redsys_environment(app_configs=None, **kwargs):
    if redsys_test_on_public_site():
        return [Error(
            'The Redsys gateway is enabled on a public site with its test environment or '
            'its public test terminal.',
            hint='Set REDSYS_ENVIRONMENT=production and the company\'s own merchant code, '
                 'terminal and key in settings.env.', id=REDSYS_TEST_ON_PUBLIC_SITE)]
    return []
