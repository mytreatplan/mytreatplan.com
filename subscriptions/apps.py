from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    """The subscription a doctor holds (epic-subscriptions entry 1, AD-15): the record and
    its change rows (subscriptions.models), the one service every caller uses
    (subscriptions.service: Basic at signup, the effective tier, a doctor's offers and
    order prices, the versioned change, the upgrade screen's options), the commands
    `assign_basic` and `doctor_subscription`, and the upgrade screen (entry 2:
    subscriptions.views)."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'subscriptions'
    verbose_name = 'Subscriptions'
