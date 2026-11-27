from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    """The subscription a doctor holds (epic-subscriptions entry 1, AD-15): the record and
    its change rows (subscriptions.models), the one service every caller uses
    (subscriptions.service: Basic at signup, the effective tier, a doctor's offers and
    order prices, the versioned change, the upgrade screen's options), the commands
    `assign_basic` and `doctor_subscription`, the upgrade screen (entry 2:
    subscriptions.views), the voucher redemptions (entry 4: subscriptions.vouchers, whose
    receivers of the order hooks are registered in `ready`) and buying Advanced or Premium
    (entry 5: the purchase page, `service.activate` on `paid`, the confirmation email of
    subscriptions.emails and subscriptions.tasks) and the included cases (epic-subscriptions
    13: subscriptions.included, whose receivers are registered in `ready`)."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'subscriptions'
    verbose_name = 'Subscriptions'

    def ready(self):
        from payments import hooks

        from . import included, service, vouchers
        # The lifecycle of a voucher redemption follows its order (epic-subscriptions
        # entry 4): reserved when created, consumed when paid, released when cancelled or
        # expired. A free order fires `created` then `paid` in one transaction.
        hooks.register('created', 'voucher_reserve', vouchers.reserve)
        hooks.register('paid', 'voucher_consume', vouchers.consume)
        hooks.register('cancelled', 'voucher_release', vouchers.release)
        hooks.register('expired', 'voucher_release', vouchers.release)
        # Buying Advanced or Premium (epic-subscriptions entry 5): a paid subscription
        # order moves the doctor's record to the bought tier and queues its confirmation
        # email after commit.
        hooks.register('paid', 'subscriptions_activate', service.activate)
        # Included cases (epic-subscriptions 13): a use per included order line, reserved
        # when created (none available: the order is not created), consumed when paid,
        # released when cancelled or expired, and given back on a refund while its grant's
        # term runs.
        hooks.register('created', 'included_reserve', included.reserve)
        hooks.register('paid', 'included_consume', included.consume)
        hooks.register('cancelled', 'included_release', included.release)
        hooks.register('expired', 'included_release', included.release)
        hooks.register('refunded', 'included_refund', included.refund)
