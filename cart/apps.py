from django.apps import AppConfig


class CartConfig(AppConfig):
    """The doctor's cart (epic-ordering 9.3, AD-2): the regional cart items the case wizard
    fills and the bare cart page; checkout, vouchers and the rest come with 9.6."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cart'
    verbose_name = 'Cart'

    def ready(self):
        from payments import hooks

        from . import emails, service
        # Epic-ordering 9.6: a paid order empties the cart of its patients.
        hooks.register('paid', 'cart_paid', service.paid)
        # Epic-ordering 9.17: so does an order the doctor approved by bank transfer.
        hooks.register('transfer_approved', 'cart_transfer_approved', service.transfer_approved)
        # Epic-ordering 9.20: the confirmation and the receipt emails, queued after commit,
        # after the receivers above (and after `invoice_issue` on `paid`).
        hooks.register('transfer_approved', 'order_confirmation_email', emails.on_transfer_approved)
        hooks.register('paid', 'payment_receipt_email', emails.on_paid)
