"""Refund a paid order in full, as the system actor (epic-payments entry 7), for staff and
staging until the admin buttons exist. Replaces `redsys_refund`.

    python manage.py refund_order <reference> --reason {cancellation,goodwill,error}
    python manage.py refund_order <reference> --reason goodwill --manual --note "Refunded by bank transfer on 21 Nov"

Without `--manual` the gateway refunds the confirmed payment (the fake one on dev, Redsys
over REST); with it nothing is sent to a gateway (a bank transfer, a 0.00 order) and a note
is required. Prints the refund's state and the credit note's number. A refund the gateway
did not answer is left `needs_attention`: the order stays blocked for refunds (manual ones
too) until staff resolve it, which the admin site's tools (epic-sales-finance) will do.
"""
from django.core.management.base import BaseCommand, CommandError

from invoices.service import InvoiceError
from payments import refunds, service
from payments.models import Order


class Command(BaseCommand):
    help = 'Refund a paid order in full (the gateway, or by hand with --manual --note).'

    def add_arguments(self, parser):
        parser.add_argument('reference')
        parser.add_argument('--reason', required=True, choices=refunds.REASONS)
        parser.add_argument('--manual', action='store_true')
        parser.add_argument('--note', default='')

    def handle(self, *args, **options):
        order = Order.objects.filter(reference=options['reference']).first()
        if order is None:
            raise CommandError('no such order')
        try:
            done = refunds.refund(
                order, expected_version=order.version, actor=service.SYSTEM_ACTOR,
                reason=options['reason'], manual=options['manual'], note=options['note'])
        except refunds.RefundFailed:
            raise CommandError('the gateway refused the refund: nothing was refunded') from None
        except (service.OrderError, InvoiceError) as failure:
            raise CommandError(f'{type(failure).__name__}: {failure}') from None
        number = done.credit_note.number if done.credit_note_id else '-'
        self.stdout.write(f'refund {done.state} for order {order.reference}: '
                          f'credit note {number}')
        if done.state == done.NEEDS_ATTENTION:
            self.stderr.write('the gateway gave no usable answer: the order stays blocked for refunds until staff resolve it')
