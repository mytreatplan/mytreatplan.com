from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    """Orders and checkout (epic-payments entry 1, AD-6, AD-15, AD-16, AD-20): the order
    model (payments.models), the one service that creates and moves orders
    (payments.service), the hook registry other apps plug into (payments.hooks), the gateway
    adapter registry, the fake gateway for development and the Redsys and Revolut adapters
    (payments.gateways, payments.fake, payments.redsys, payments.revolut), the doctor's checkout pages
    (payments.views) and the `expire_orders` command."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'payments'
    verbose_name = 'Payments'

    def ready(self):
        # Importing checks registers payments.E001-E005.
        from . import bank_transfer, checks, fake, redsys, revolut  # noqa: F401

        # `check` is not run by the web server: refuse to start with a flag on a public site.
        from django.conf import settings
        from django.core.exceptions import ImproperlyConfigured
        if checks.public_site() and (getattr(settings, 'PAYMENTS_FAKE_GATEWAY', False)
                                     or getattr(settings, 'PAYMENTS_TEST_ORDERS', False)):
            raise ImproperlyConfigured(
                'PAYMENTS_FAKE_GATEWAY and PAYMENTS_TEST_ORDERS are for development and '
                'staging only: remove them from settings.env on a public site.')
        if checks.redsys_test_on_public_site():
            raise ImproperlyConfigured(
                'Redsys is enabled on a public site with its test environment or its public '
                'test terminal: set REDSYS_ENVIRONMENT=production and the company\'s own '
                'merchant code, terminal and key in settings.env (payments.E004).')

        if checks.revolut_sandbox_on_public_site():
            raise ImproperlyConfigured(
                'Revolut is enabled on a public site with its sandbox environment: set '
                'REVOLUT_ENVIRONMENT=production and the production key in settings.env '
                '(payments.E005).')

        fake.register()
        redsys.register()
        revolut.register()
        bank_transfer.register()
