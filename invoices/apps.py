from django.apps import AppConfig


class InvoicesConfig(AppConfig):
    """Invoices (epic-payments entry 6, AD-7): structured, immutable invoices with a
    gapless number per billing company, instance and year (invoices.models), issued by
    invoices.service from the order's `paid` hook or, for a sale with no order, by hand."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'invoices'
    verbose_name = 'Invoices'

    def ready(self):
        from payments import hooks

        from . import service
        hooks.register('paid', 'invoice_issue', service.issue_for_order)
