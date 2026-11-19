"""Refund an order's Redsys payment at Redsys (epic-payments entry 3, user decision
2026-11-19).

    python manage.py redsys_refund <reference> [--amount X]

`<reference>` is the order's reference (`KM-482915`) with a confirmed Redsys payment.
Without `--amount` the payment's full amount is refunded; `--amount X` refunds X (more than
0, at most the payment's amount). The refund goes over Redsys REST (transaction type 3)
and its signed answer is verified; prints the refund's id. Nothing changes on the order (no
state change, no credit note: entry 7). Any refusal or HTTP fault is a CommandError with no
part of Redsys's answer.
"""
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from payments import gateways, redsys
from payments.models import Order, Payment


class Command(BaseCommand):
    help = 'Refund an order\'s Redsys payment at Redsys.'

    def add_arguments(self, parser):
        parser.add_argument('reference')
        parser.add_argument('--amount')

    def handle(self, *args, **options):
        adapter = gateways.adapter(redsys.NAME)
        if adapter is None:
            raise CommandError('the Redsys gateway is not set up (REDSYS_MERCHANT_CODE, '
                               'REDSYS_SECRET_KEY)')
        order = Order.objects.filter(reference=options['reference']).first()
        if order is None:
            raise CommandError('no such order')
        payment = Payment.objects.filter(order=order, gateway=redsys.NAME,
                                         state=Payment.CONFIRMED).first()
        if payment is None:
            raise CommandError('the order has no confirmed Redsys payment')
        amount = payment.amount
        if options['amount'] is not None:
            try:
                amount = Decimal(options['amount'])
                if not amount.is_finite():
                    raise InvalidOperation
                redsys.to_cents(amount)
            except (InvalidOperation, ValueError):
                raise CommandError('--amount is a decimal number, such as 10.00') from None
            if amount <= 0 or amount > payment.amount:
                raise CommandError(f'--amount is more than 0 and at most {payment.amount}')
        try:
            refund_id = adapter.call('refund', payment, amount)
        except gateways.GatewayError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(f'refunded {amount} {payment.currency} of order {order.reference}: '
                          f'{refund_id}')
