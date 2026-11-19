"""Send the fake gateway's webhook for an order, to try the webhook handling on dev and
dev-ae (epic-payments entry 2; needs PAYMENTS_FAKE_GATEWAY).

    python manage.py fake_webhook <reference> [--bad-signature | --stale | --amount X | --replay]

`<reference>` is the order's reference (`KM-482915`) in `pending_payment` with the fake
gateway chosen and its attempt open (the doctor pressed Pay on the checkout). Without an
option it sends a valid webhook: the fake gateway marks the payment paid, the webhook is
accepted and the order is paid once. The options send each refusal on purpose:

- `--bad-signature`: signed with the wrong secret (refused, `bad_signature`);
- `--stale`: signed correctly with a timestamp an hour old (refused, `stale`);
- `--amount X`: the fake gateway holds a payment of X, so the re-read differs from the
  order's total (accepted, then `amount_mismatch`; the payment needs attention);
- `--replay`: the same valid webhook twice (the second is a `duplicate`).

The webhook goes through the same handler a real gateway's does; the accepted one is also
processed here at once (the task does the same on the worker, and finds it done). Prints the
HTTP status and the log result of each; nothing else of the webhook.
"""
import time

from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from payments import fake, gateways
from payments.models import Order, Payment, WebhookLog


class Command(BaseCommand):
    help = 'Send the fake gateway\'s webhook for an order (dev and staging).'

    def add_arguments(self, parser):
        parser.add_argument('reference')
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--bad-signature', action='store_true')
        group.add_argument('--stale', action='store_true')
        group.add_argument('--amount')
        group.add_argument('--replay', action='store_true')

    def handle(self, *args, **options):
        if gateways.adapter(gateways.FAKE) is None:
            raise CommandError('the fake gateway is off (PAYMENTS_FAKE_GATEWAY)')
        order = Order.objects.filter(reference=options['reference']).first()
        if order is None:
            raise CommandError('no such order')
        payment = Payment.objects.filter(order=order, gateway=gateways.FAKE).exclude(
            gateway_payment_id='').order_by('-created_at').first()
        if payment is None:
            raise CommandError('the order has no fake payment attempt')
        amount = options['amount']
        if amount is not None:
            try:
                if not Decimal(amount).is_finite() or Decimal(amount) < 0:
                    raise InvalidOperation
            except InvalidOperation:
                raise CommandError('--amount is a decimal number, such as 1.00') from None
        if not (options['bad_signature'] or options['stale']):
            # Only a webhook that should be accepted comes with a payment the gateway holds
            # as paid; a refused one leaves the gateway's state as it was.
            fake.set_state(payment.gateway_payment_id, 'paid', amount=amount)
        body = fake.webhook_body(payment, amount=amount)
        timestamp, key = int(time.time()), None
        if options['stale']:
            timestamp -= 3600
        if options['bad_signature']:
            key = 'not-the-secret'
        sends = 2 if options['replay'] else 1
        for number in range(1, sends + 1):
            before = WebhookLog.objects.order_by('-id').values_list('id', flat=True).first() or 0
            response = fake.send_webhook(body, timestamp=timestamp, key=key)
            row = WebhookLog.objects.filter(id__gt=before).order_by('id').first()
            line = (f'webhook {number}: HTTP {response.status_code}, '
                    f'{row.result if row else "no log row"}')
            if row is not None and row.result == WebhookLog.ACCEPTED:
                outcome = service_outcome(row)
                line += f', processed: {outcome}'
            self.stdout.write(line)
        order.refresh_from_db()
        self.stdout.write(f'order is {order.state}')


def service_outcome(row):
    from payments import webhooks
    outcome = webhooks.process(row.pk)
    return outcome or 'already processed'
