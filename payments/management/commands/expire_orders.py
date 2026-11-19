"""Expire the orders left unpaid (epic-payments entry 1): every order in `checkout` or
`pending_payment` created more than 24 hours ago becomes `expired`, through
payments.service.expire_stale (each in its own transaction, firing its `expired` hook).

    python manage.py expire_orders

Prints how many were expired. Running it again expires none more. No order reference,
name or amount is printed.
"""
from django.core.management.base import BaseCommand

from payments import service


class Command(BaseCommand):
    help = 'Expire the orders left in checkout or pending payment for more than 24 hours.'

    def handle(self, *args, **options):
        self.stdout.write(f'expire_orders: {service.expire_stale()} order(s) expired')
