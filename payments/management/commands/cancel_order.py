"""Cancel an unpaid order, as the system actor (epic-payments entry 7), for staff and staging
until the admin buttons exist.

    python manage.py cancel_order <reference>

A paid order is refused (refund it with `refund_order`); so is one whose card session is
still live.
"""
from django.core.management.base import BaseCommand, CommandError

from payments import refunds, service
from payments.models import Order


class Command(BaseCommand):
    help = 'Cancel an unpaid order.'

    def add_arguments(self, parser):
        parser.add_argument('reference')

    def handle(self, *args, **options):
        order = Order.objects.filter(reference=options['reference']).first()
        if order is None:
            raise CommandError('no such order')
        try:
            done = refunds.staff_cancel(order, expected_version=order.version,
                                        actor=service.SYSTEM_ACTOR)
        except service.OrderError as failure:
            raise CommandError(f'{type(failure).__name__}: {failure}') from None
        self.stdout.write(f'order {done.reference}: {done.state}')
