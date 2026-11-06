"""Print what a doctor of a country is offered under a subscription tier (epic-catalogue
entry 2), priced by catalogue.resolver from the applied configuration snapshot.

    python manage.py price_catalogue --country ES --tier basic

One line per offer (product, category, price, net of VAT), or "no offers" when no active
subscription of that tier applies to the country. Reads only.
"""
from django.core.management.base import BaseCommand

from catalogue import resolver
from catalogue.models import Subscription


class Command(BaseCommand):
    help = 'Print the catalogue offers for a country (ISO alpha-2) and a subscription tier.'

    def add_arguments(self, parser):
        parser.add_argument('--country', required=True, help='ISO 3166-1 alpha-2, e.g. ES.')
        parser.add_argument('--tier', required=True, choices=Subscription.TIERS,
                            type=resolver.normal_tier, help='basic, advanced or premium.')

    def handle(self, *, country, tier, **options):
        country = resolver.normal_country(country)
        offers = resolver.offers(country, tier)
        if not offers:
            self.stdout.write(f'no offers for {country} {tier}')
            return
        group = resolver.country_group(country)
        self.stdout.write(f'{country} {tier} ({group.name}): {len(offers)} offer(s)')
        for product, price in offers:
            self.stdout.write(f'  {product.name}  {product.category}  {price}')
