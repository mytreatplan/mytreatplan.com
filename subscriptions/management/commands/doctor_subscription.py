"""Print a doctor's subscription (epic-subscriptions entry 1), through
subscriptions.service. Reads only.

    python manage.py doctor_subscription <uid or email>

The record (tier, status, subscription id, dates, period, auto-renew, manual, version,
who made the last change; "no record" for a doctor the previous release created, who
counts as Basic; a staff account's record is ignored), then what the doctor holds today (`service.effective`): the effective
tier, the subscription's code (or "none": offered nothing), whether it is the record's own
row, manual and whether the doctor may switch on their own.

    python manage.py doctor_subscription <uid or email> --offers

Adds what the doctor is offered (`service.offers_for`): one line per product (code,
name, category, price net of VAT), or "no offers".

    python manage.py doctor_subscription <uid or email> --order --product <UUID or code> \\
        [--quantity N] [--vies valid|invalid|pending|not_applicable]

Adds the price of an order of one line (`service.price_order_for`; VIES `not_applicable`
by default): the company, each total and the gateways; a refusal is an error naming its
reason. The doctor's email is never printed.
"""
import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts.client_sync import is_client
from catalogue import resolver
from catalogue.models import Product
from subscriptions import service


class Command(BaseCommand):
    help = "Print a doctor's subscription, offers or the price of an order. Reads only."

    def add_arguments(self, parser):
        parser.add_argument('doctor', help="The doctor's uid or email.")
        parser.add_argument('--offers', action='store_true', help='List the offers.')
        parser.add_argument('--order', action='store_true', help='Price an order of one line.')
        parser.add_argument('--product', help='The product of the line: its UUID or code.')
        parser.add_argument('--quantity', type=int, default=1, help='The quantity (1).')
        parser.add_argument('--vies', choices=resolver.VIES_STATUSES,
                            default=resolver.VIES_NOT_APPLICABLE,
                            help='The VIES status (not_applicable).')

    def handle(self, *args, doctor, offers, order, product, quantity, vies, **options):
        if order != bool(product):
            raise CommandError('--order goes with --product, and --product with --order.')
        user = self.found_user(doctor)
        day = resolver.today()
        self.print_record(user, day)
        if offers:
            self.print_offers(user, day)
        if order:
            self.print_order(user, self.found_product(product), quantity, vies, day)

    def found_user(self, text):
        User = get_user_model()
        try:
            users = User.objects.filter(uid=uuid.UUID(text.strip()))
        except ValueError:
            users = User.objects.by_email(text.strip())
        user = users.first()
        if user is None:
            raise CommandError('No such user.')
        return user

    @staticmethod
    def found_product(text):
        """The product of this UUID, else of this code, whatever its state."""
        found = resolver.product(text)
        if found is None:
            matches = list(Product.objects.filter(code=text.strip())[:2])
            if len(matches) > 1:
                raise CommandError(f'Two products have the code {text}: give its UUID.')
            found = matches[0] if matches else None
        if found is None:
            raise CommandError(f'No product {text}.')
        return found

    def print_record(self, user, day):
        held = service.effective(user, day)
        record = held.record
        self.stdout.write(f'doctor {user.uid}: country {user.country.code or "-"}, '
                          f'on {day.isoformat()}')
        if record is None and not is_client(user):
            self.stdout.write('  record: none (staff account: any record is ignored)')
        elif record is None:
            self.stdout.write('  record: none (counts as Basic; assign_basic gives one)')
        else:
            self.stdout.write(
                f'  record: {record.tier}, {record.status}, subscription '
                f'{record.subscription_id or "-"}, started {record.started_on.isoformat()}, '
                f'ends {record.ends_on.isoformat() if record.ends_on else "-"}, period '
                f'{record.billing_period or "-"}, auto-renew '
                f'{"on" if record.auto_renew else "off"}, manual '
                f'{"yes" if record.manual else "no"}, version {record.version}, last change '
                f'by {record.actor_type}{f" {record.actor_uuid}" if record.actor_uuid else ""}')
        subscription = held.subscription
        if subscription is None:
            name = 'none (offered nothing)'
        else:
            name = str(subscription.code or subscription.pk)
            if held.held:
                name += ' (held' + ('' if subscription.active else ', deactivated') + ')'
        self.stdout.write(f'  effective: {held.tier}, subscription {name}, manual '
                          f'{"yes" if held.manual else "no"}, can switch '
                          f'{"yes" if held.can_self_switch else "no"}')

    def print_offers(self, user, day):
        found = service.offers_for(user, day)
        if not found:
            self.stdout.write('  no offers')
            return
        self.stdout.write('  offers:')
        for product, price in found:
            self.stdout.write(f'    {product.code or "-"}  {product.name}  {product.category}'
                              f'  {price}')

    def print_order(self, user, product, quantity, vies, day):
        try:
            priced = service.price_order_for(
                user, [resolver.OrderLine(product, quantity)], vies=vies, day=day)
        except resolver.OrderRefused as refusal:
            raise CommandError(f'Refused ({refusal.reason}): {refusal}.') from None
        line = priced.lines[0]
        self.stdout.write(f'  order: {product.code or product.pk} x {quantity}, VIES {vies}')
        self.stdout.write(f'    company: {priced.company.code}  {priced.company.legal_name}')
        self.stdout.write(f'    list {line.list_amount}  discount '
                          f'{line.discount_percent:.2f} % -{line.discount}  net {line.net}')
        self.stdout.write(f'    VAT {line.vat_percent:.2f} %'
                          + (f': {line.vat_text}' if line.vat_text else ''))
        self.stdout.write(f'    net total: {priced.net_total}  VAT total: {priced.vat_total}'
                          f'  total: {priced.total}')
        if not priced.needs_payment:
            self.stdout.write('    gateways: nothing to pay')
        elif priced.no_payment_method:
            self.stdout.write('    gateways: none: no payment method')
        else:
            self.stdout.write(f'    gateways: {", ".join(priced.gateways)}')
