"""Print what a doctor of a country is offered under a subscription tier (epic-catalogue
entry 2), priced by catalogue.resolver from the applied configuration snapshot.

    python manage.py price_catalogue --country ES --tier basic [--language es]

One line per offer (code, public name, category, price net of VAT), or "no offers" when
no active subscription of that tier applies to the country. The name is in `--language`
(English by default), falling back to English (epic-catalogue entry 5). A product that is
inactive or outside its validity dates (Madrid time) is not offered, so not listed.

    python manage.py price_catalogue --product <id> [--language es]

One product by its id whatever its state (a deactivated one, or one outside its validity
dates, stays readable for past orders): its code, name, category, net price, status,
validity and description. Reads only.
"""
from django.core.management.base import BaseCommand, CommandError

from catalogue import resolver
from catalogue.models import Subscription


class Command(BaseCommand):
    help = ('Print the catalogue offers for a country (ISO alpha-2) and a subscription '
            'tier, or one product by its id.')

    def add_arguments(self, parser):
        parser.add_argument('--country', help='ISO 3166-1 alpha-2, e.g. ES.')
        parser.add_argument('--tier', choices=Subscription.TIERS,
                            type=resolver.normal_tier, help='basic, advanced or premium.')
        parser.add_argument('--product', help='A product id (UUID): print that product, '
                                              'offered or not.')
        parser.add_argument('--language', default='en',
                            help='The language of the texts, e.g. es (default: en; a '
                                 'missing text falls back to English).')

    def handle(self, *, country, tier, product, language, **options):
        if product:
            if country or tier:
                raise CommandError('--product goes without --country and --tier.')
            return self.print_product(product, language)
        if not country or not tier:
            raise CommandError('Give --country and --tier, or --product.')
        country = resolver.normal_country(country)
        offers = resolver.offers(country, tier)
        if not offers:
            self.stdout.write(f'no offers for {country} {tier}')
            return
        group = resolver.country_group(country)
        self.stdout.write(f'{country} {tier} ({group.name}): {len(offers)} offer(s)')
        for offered, price in offers:
            self.stdout.write(f'  {offered.code or "-"}  {offered.name_in(language)}  '
                              f'{offered.category}  {price}')

    def print_product(self, product_id, language):
        found = resolver.product(product_id)
        if found is None:
            raise CommandError(f'No product {product_id}.')
        today = resolver.today()
        self.stdout.write(f'{found.pk}  {found.code or "-"}  {found.name_in(language)}  '
                          f'{found.category}  {found.net_price}')
        self.stdout.write(f'  status: {"active" if found.active else "inactive"}')
        self.stdout.write(f'  valid: {found.valid_from or "-"} to {found.valid_to or "-"} '
                          f'(today in Madrid: {today})')
        offered = found.active and found.offered_on(today)
        self.stdout.write(f'  offered for new orders: {"yes" if offered else "no"}')
        for line in found.description_in(language).split('\n'):
            if line:
                self.stdout.write(f'  | {line}')
