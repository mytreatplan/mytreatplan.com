from django.apps import AppConfig


class CartConfig(AppConfig):
    """The doctor's cart (epic-ordering 9.3, AD-2): the regional cart items the case wizard
    fills and the bare cart page; checkout, vouchers and the rest come with 9.6."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cart'
    verbose_name = 'Cart'
