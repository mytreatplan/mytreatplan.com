"""Print what a doctor of a country is offered under a subscription tier (epic-catalogue
entry 2), priced by catalogue.resolver from the applied configuration snapshot.

    python manage.py price_catalogue --country ES --tier basic [--language es]

One line per offer (code, public name, category, price net of VAT), or "no offers" when
no active subscription of that tier applies to the country. The name is in `--language`
(English by default), falling back to English (epic-catalogue entry 5). A product that is
inactive or outside its validity dates (Madrid time) is not offered, so not listed. A
product priced per unit shows the price of one unit and its quantity range, and each
offer is followed by the add-ons offered on it (epic-catalogue entry 6).

    python manage.py price_catalogue --product <id> [--language es] [--cost [--on DATE]]

One product by its id whatever its state (a deactivated one, or one outside its validity
dates, stays readable for past orders): its code, name, category, net price, status,
validity, size, quantity range, calls, the add-ons offered on it and its description.
With `--cost`: what it costs the company today in Madrid time (or `--on YYYY-MM-DD`): the
sum of its cost links, or the partial sum with a warning naming the provider products
without a cost on that date (never the cost then), or "no cost links" when there is none
to sum (never 0.00), and the monthly links apart. The date chooses the provider costs
only: the links are the product's current ones.

    python manage.py price_catalogue --product <id> --country ES --tier basic \\
        [--add-on CODE ...] [--quantity N]

The price of one line of that product for a doctor of the country under the tier, with
the chosen add-ons (`--add-on`, once per add-on) and quantity (1 by default; within the
range of a product priced per unit). A quantity the product is not sold in, or an add-on
that is not offered on it, is refused with an error. Reads only.

Subscriptions in full (epic-catalogue entry 8): `--country` and `--tier` give the
standard subscription of that tier and the country's group, and list only the products
linked to it. The offers are followed by the subscription's line (its code, public name
and own price, or "free") and each offer by its calls under the subscription (the
product's calls plus the subscription's extras for its category). A manual subscription
is never the answer for a country and a tier: give its id or code instead,

    python manage.py price_catalogue --subscription <id or code> [--language es]
    python manage.py price_catalogue --subscription <id or code> --product <id> \\
        [--add-on CODE ...] [--quantity N]

which read any subscription, standard or manual, whatever its state (an inactive one
offers nothing). A product the subscription does not give access to is refused.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from catalogue import resolver
from catalogue.models import Subscription


def _date(text):
    return date.fromisoformat(text)


class Command(BaseCommand):
    help = ('Print the catalogue offers for a country (ISO alpha-2) and a subscription '
            'tier, one product by its id (with its cost), or the price of one line of it.')

    def add_arguments(self, parser):
        parser.add_argument('--country', help='ISO 3166-1 alpha-2, e.g. ES.')
        parser.add_argument('--tier', choices=Subscription.TIERS,
                            type=resolver.normal_tier, help='basic, advanced or premium.')
        parser.add_argument('--subscription', metavar='ID_OR_CODE',
                            help='A subscription id (UUID) or code, standard or manual, '
                                 'in place of --country and --tier: what it offers, or '
                                 'with --product the price of one line under it.')
        parser.add_argument('--product', help='A product id (UUID): print that product, '
                                              'offered or not; with --country and --tier, '
                                              'or --subscription, the price of one line '
                                              'of it.')
        parser.add_argument('--language', default='en',
                            help='The language of the texts, e.g. es (default: en; a '
                                 'missing text falls back to English).')
        parser.add_argument('--add-on', action='append', default=[], dest='add_ons',
                            metavar='CODE', help='With --product, --country and --tier: an '
                                                 'add-on of the line, by its code '
                                                 '(repeat for several).')
        parser.add_argument('--quantity', type=int,
                            help='With --product, --country and --tier: the quantity of '
                                 'the line (default 1).')
        parser.add_argument('--cost', action='store_true',
                            help='With --product alone: its cost at a date.')
        parser.add_argument('--on', type=_date, metavar='YYYY-MM-DD',
                            help='With --cost: the date (default: today in Madrid).')

    def handle(self, *, country, tier, subscription, product, language, add_ons, quantity,
               cost, on, **options):
        if bool(country) != bool(tier):
            raise CommandError('Give --country and --tier together.')
        if subscription and country:
            raise CommandError('Give --subscription, or --country and --tier.')
        line = bool(product and (country or subscription))
        if (add_ons or quantity is not None) and not line:
            raise CommandError('--add-on and --quantity go with --product and --country '
                               'and --tier, or --subscription.')
        if cost and (not product or line):
            raise CommandError('--cost goes with --product alone.')
        if on and not cost:
            raise CommandError('--on goes with --cost.')
        if product and not line:
            return self.print_product(product, language, cost, on)
        if subscription:
            applies = self.found_subscription(subscription)
            title = self.subscription_title(applies)
        elif country:
            country = resolver.normal_country(country)
            applies = resolver.subscription(country, tier)
            title = f'{country} {tier}'
            if applies is not None:
                title += f' ({resolver.country_group(country).name})'
        else:
            raise CommandError('Give --country and --tier, --subscription, or --product.')
        if line:
            found = self.found(product)
            if applies is None:
                raise CommandError(f'No active {tier} subscription applies to {country}.')
            return self.print_line(found, applies, title, language, add_ons,
                                   1 if quantity is None else quantity)
        offers = [] if applies is None else resolver.offers_under(applies)
        if not offers:
            self.stdout.write(f'no offers for {title}')
            if applies is not None:
                self.print_subscription(applies, language)
            return
        self.stdout.write(f'{title}: {len(offers)} offer(s)')
        self.print_subscription(applies, language)
        for offered, price in offers:
            per_unit = (f'  per unit, {offered.min_quantity} to {offered.max_quantity}'
                        if offered.per_unit else '')
            self.stdout.write(f'  {offered.code or "-"}  {offered.name_in(language)}  '
                              f'{offered.category}  {price}{per_unit}')
            calls = resolver.calls(offered, applies)
            if calls is not None:
                self.stdout.write(f'      calls: {calls[0]} of {calls[1]} minutes')
            for add_on in resolver.add_ons(offered):
                self.stdout.write(f'      + {self.add_on_text(add_on, language)}')

    @staticmethod
    def add_on_text(add_on, language):
        return f'{add_on.code}  {add_on.name_in(language)}  {add_on.value_text}'

    def found_subscription(self, text):
        """The subscription of this id, else of this code, whatever its state."""
        found = resolver.subscription_by_id(text) or resolver.subscription_by_code(text)
        if found is None:
            raise CommandError(f'No subscription {text}.')
        return found

    @staticmethod
    def subscription_title(found):
        """"ORMCO-PREMIUM premium (Iberia), manual": a subscription read by its id or
        code."""
        kind = 'manual' if found.manual else 'standard'
        state = '' if found.active else ', inactive'
        return (f'{found.code or found.pk} {found.tier} ({found.country_group.name}), '
                f'{kind}{state}')

    def print_subscription(self, found, language):
        """The subscription's line under the title: its code, public name and own price
        (free, or its monthly and upfront amounts)."""
        parts = []
        if found.monthly_price is not None:
            parts.append(f'{found.monthly_price} a month')
        if found.upfront_price is not None:
            parts.append(f'{found.upfront_price} upfront for a year')
        self.stdout.write(f'  subscription: {found.code or "-"}  '
                          f'{found.name_in(language) or found.tier}  '
                          f'{", ".join(parts) or "free"}')

    def found(self, product_id):
        found = resolver.product(product_id)
        if found is None:
            raise CommandError(f'No product {product_id}.')
        return found

    def print_product(self, product_id, language, cost=False, on=None):
        found = self.found(product_id)
        today = resolver.today()
        self.stdout.write(f'{found.pk}  {found.code or "-"}  {found.name_in(language)}  '
                          f'{found.category}  {found.net_price}')
        self.stdout.write(f'  status: {"active" if found.active else "inactive"}')
        self.stdout.write(f'  valid: {found.valid_from or "-"} to {found.valid_to or "-"} '
                          f'(today in Madrid: {today})')
        offered = found.active and found.offered_on(today)
        self.stdout.write(f'  offered for new orders: {"yes" if offered else "no"}')
        if found.size:
            self.stdout.write(f'  size: {found.size}')
        if found.per_unit:
            self.stdout.write(f'  per unit: {found.min_quantity} to {found.max_quantity}')
        if found.calls is not None:
            self.stdout.write(f'  calls: {found.calls} of {found.call_minutes} minutes')
        for add_on in resolver.add_ons(found):
            self.stdout.write(f'  add-on: {self.add_on_text(add_on, language)}')
        if cost:
            self.print_cost(found, on or today)
        for line in found.description_in(language).split('\n'):
            if line:
                self.stdout.write(f'  | {line}')

    def print_cost(self, found, day):
        cost = resolver.cost(found, day)
        if cost.empty:
            self.stdout.write(f'  cost on {day}: no cost links')
        elif cost.complete:
            self.stdout.write(f'  cost on {day}: {cost.total}')
        else:
            self.stdout.write(f'  warning: {cost.warning}')
            self.stdout.write(f'  partial sum on {day}, not the cost: {cost.total}')
        for monthly in cost.monthly:
            amount = (f'{monthly.cost} a month' if monthly.cost is not None
                      else 'no cost on this date')
            self.stdout.write(f'  monthly, not in the sum: {monthly.label} x '
                              f'{monthly.quantity}: {amount}')

    def print_line(self, found, applies, title, language, codes, quantity):
        """The price of one line of the product `found` under the subscription
        `applies` (`title`: how it was asked for)."""
        chosen = []
        for code in codes:
            add_on = resolver.add_on(code)
            if add_on is None:
                raise CommandError(f'No add-on {code}.')
            chosen.append(add_on)
        try:
            price = resolver.price(found, applies, chosen, quantity)
        except (resolver.QuantityRefused, resolver.AddOnRefused,
                resolver.ProductRefused) as refusal:
            raise CommandError(f'Refused: {refusal}.') from None
        self.stdout.write(f'{title}: {found.code or "-"}  '
                          f'{found.name_in(language)}  {found.category}')
        self.print_subscription(applies, language)
        self.stdout.write(f'  net price: {found.net_price}'
                          f'{" per unit" if found.per_unit else ""}')
        self.stdout.write(f'  quantity: {quantity}')
        self.stdout.write('  subscription discount: '
                          f'{resolver.discount_percent(found, applies):.2f} %')
        calls = resolver.calls(found, applies)
        if calls is not None:
            self.stdout.write(f'  calls: {calls[0]} of {calls[1]} minutes')
        for add_on in chosen:
            self.stdout.write(f'  add-on: {self.add_on_text(add_on, language)}')
        self.stdout.write(f'  line price: {price}')
