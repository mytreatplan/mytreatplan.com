from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    """Orders and checkout (epic-payments entry 1, AD-6, AD-15, AD-16, AD-20): the order
    model (payments.models), the one service that creates and moves orders
    (payments.service), the hook registry other apps plug into (payments.hooks), the gateway
    adapter registry and the fake gateway for development (payments.gateways,
    payments.fake), the doctor's checkout pages (payments.views) and the `expire_orders`
    command."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'payments'
    verbose_name = 'Payments'

    def ready(self):
        from . import checks, fake  # noqa: F401  (registers payments.E001, E002)

        # `check` is not run by the web server: refuse to start with a flag on a public site.
        from django.conf import settings
        from django.core.exceptions import ImproperlyConfigured
        if checks.public_site() and (getattr(settings, 'PAYMENTS_FAKE_GATEWAY', False)
                                     or getattr(settings, 'PAYMENTS_TEST_ORDERS', False)):
            raise ImproperlyConfigured(
                'PAYMENTS_FAKE_GATEWAY and PAYMENTS_TEST_ORDERS are for development and '
                'staging only: remove them from settings.env on a public site.')

        fake.register()
