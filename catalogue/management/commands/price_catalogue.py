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

Billing companies and billing rules (epic-catalogue entry 9): once the catalogue has
rules, a product whose country group and category have no available rule, or that is in
another currency than its rule's, is not offered, so not listed. Each offer is followed by
its billing line (the company, the VAT rate or, for a rate of 0, the exemption text, the
gateways in order of preference and the currency), and the subscription's line by the
billing rule of its own price (the `subscription` category rule of its country group, or
"no billing rule"). A line's price shows the product's billing rule too.

    python manage.py price_catalogue --country ES --category tps_branded

One billing rule: the rule of the country's group and that product category, in full
(company with its identifiers, VAT rate, exemption and reverse-charge texts, gateways,
currency, availability), or "no billing rule". When the billing company of this region
has another legal name or registration than the region profile's, every output starts
with a warning line saying so (nothing is refused).

    python manage.py price_catalogue --voucher CODE [--on YYYY-MM-DD]

One voucher of this region (epic-catalogue entry 10) by its code, typed with or without
the hyphen and in any case, whatever its state: its code, type, amount, the doctor's
client UUID (or "any doctor"), its validity and whether it is usable today in Madrid time
(or `--on`), else why not (inactive, not in force, or a type or currency this release
does not know); "no voucher" when this region has none with that code. Reads only.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from catalogue import billing, resolver
from catalogue.models import Product, Subscription


def _date(text):
    return date.fromisoformat(text)


class Command(BaseCommand):
    help = ('Print the catalogue offers for a country (ISO alpha-2) and a subscription '
            'tier, one product by its id (with its cost), or the price of one line of it.')

    def add_arguments(self, parser):
        parser.add_argument('--country', help='ISO 3166-1 alpha-2, e.g. ES.')
        parser.add_argument('--tier', choices=Subscription.TIERS,
                            type=resolver.normal_tier, help='basic, advanced or premium.')
        parser.add_argument('--category', type=resolver.normal_category,
                            choices=[code for code in Product.CATEGORIES
                                     if code != Product.UNCATEGORISED],
                            help='With --country alone: print the billing rule of the '
                                 'country\'s group and this product category.')
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
                            help='With --cost or --voucher: the date (default: today in '
                                 'Madrid).')
        parser.add_argument('--voucher', metavar='CODE',
                            help='A voucher code of this region (with or without the '
                                 'hyphen): print that voucher and whether it is usable.')

    def handle(self, *, country, tier, subscription, product, language, add_ons, quantity,
               cost, on, category=None, voucher=None, **options):
        mismatch = billing.profile_mismatch()
        if mismatch:
            self.stdout.write(f'warning: {mismatch}')
        if voucher is not None:
            if country or tier or subscription or product or add_ons or cost or category \
                    or quantity is not None or language != 'en':
                raise CommandError('--voucher goes alone (or with --on).')
            return self.print_voucher(voucher, on)
        if category:
            if not country or tier or subscription or product or add_ons or cost or on \
                    or quantity is not None:
                raise CommandError('--category goes with --country alone.')
            return self.print_rule(resolver.normal_country(country), category)
        if bool(country) != bool(tier):
            raise CommandError('Give --country and --tier together (or --country and '
                               '--category for a billing rule).')
        if subscription and country:
            raise CommandError('Give --subscription, or --country and --tier.')
        line = bool(product and (country or subscription))
        if (add_ons or quantity is not None) and not line:
            raise CommandError('--add-on and --quantity go with --product and --country '
                               'and --tier, or --subscription.')
        if cost and (not product or line):
            raise CommandError('--cost goes with --product alone.')
        if on and not cost:
            raise CommandError('--on goes with --cost or --voucher.')
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
        offers = [] if applies is None else resolver.offers_under(
            applies, country=country or None)
        billed = resolver.has_billing_rules()
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
            if billed:
                self.stdout.write('      billing: ' + self.rule_text(
                    self.rule_of(offered, applies, country)))
            calls = resolver.calls(offered, applies)
            if calls is not None:
                self.stdout.write(f'      calls: {calls[0]} of {calls[1]} minutes')
            for add_on in resolver.add_ons(offered):
                self.stdout.write(f'      + {self.add_on_text(add_on, language)}')

    STATE_TEXTS = {resolver.USABLE: 'usable', resolver.INACTIVE: 'not usable: inactive',
                   resolver.NOT_IN_FORCE: 'not usable: not in force',
                   resolver.UNKNOWN_TYPE: 'not usable: a type this release does not know',
                   resolver.UNKNOWN_CURRENCY: 'not usable: a currency this release does '
                                              'not know'}

    def print_voucher(self, code, on):
        """One voucher by its code: its fields and whether it is usable on the day."""
        found = resolver.voucher_by_code(code)
        if found is None:
            self.stdout.write('no voucher with this code')
            return
        day = on or resolver.today()
        end = found.valid_to.isoformat() if found.valid_to else 'no end'
        self.stdout.write(f'{found.display_code}  {found.type}  {found.amount} '
                          f'{found.currency}')
        self.stdout.write(f'  doctor: {found.client or "any doctor"}')
        self.stdout.write(f'  valid: {found.valid_from.isoformat()} to {end}')
        self.stdout.write(f'  {"active" if found.active else "inactive"}; on '
                          f'{day.isoformat()}: '
                          f'{self.STATE_TEXTS[resolver.voucher_state(found, day)]}')

    @staticmethod
    def rule_of(offered, applies, country):
        """The billing rule of a product for the doctor's country group: the one of
        `country` when the offers were asked for a country, else the subscription's."""
        if country:
            return resolver.billing_rule(country, offered.category)
        return resolver.billing_rule_for(applies.country_group_id, offered.category)

    @staticmethod
    def one_line(text):
        return ' / '.join(line.strip() for line in text.split('\n') if line.strip())

    def rule_text(self, rule):
        """A rule in one line: the company, the VAT rate (with the exemption text for a
        rate of 0), the gateways in order and the currency; "no billing rule" for
        None."""
        if rule is None:
            return 'no billing rule'
        vat = f'VAT {rule.vat_percent} %'
        if rule.exempt:
            vat += f': {self.one_line(rule.exemption_text)}'
        return (f'{rule.company.code}  {rule.company.legal_name}  {vat}  gateways: '
                f'{", ".join(rule.known_gateways) or "none"}  {rule.currency}'
                f'{"" if rule.available else "  not available"}')

    def print_rule(self, country, category):
        """The billing rule of the country's group and a product category, in full."""
        group = resolver.country_group(country)
        title = f'{country} {category}' + (f' ({group.name})' if group else '')
        rule = resolver.billing_rule(country, category)
        if rule is None:
            self.stdout.write(f'no billing rule for {title}')
            if not resolver.has_billing_rules():
                self.stdout.write('  the catalogue has no billing rules: every linked '
                                  'product is offered, in every currency')
            return
        company = rule.company
        self.stdout.write(f'{title}: billing rule')
        self.stdout.write(f'  company: {company.code}  {company.legal_name}'
                          f'{"" if company.active else "  (inactive)"}')
        self.stdout.write(f'  address: {self.one_line(company.address)}  ({company.country}, '
                          f'{company.time_zone})')
        for label, value in company.identifiers:
            self.stdout.write(f'  {label}: {value}')
        self.stdout.write(f'  VAT: {rule.vat_percent} %')
        if rule.exemption_text:
            self.stdout.write(f'  exemption text: {self.one_line(rule.exemption_text)}')
        if rule.reverse_charge_text:
            self.stdout.write('  reverse-charge text: '
                              f'{self.one_line(rule.reverse_charge_text)}')
        self.stdout.write(f'  gateways: {", ".join(rule.known_gateways) or "none"}')
        self.stdout.write(f'  currency: {rule.currency}')
        self.stdout.write(f'  available: {"yes" if rule.available else "no: not offered"}')

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
        if resolver.has_billing_rules():
            self.stdout.write('  subscription billing: '
                              + self.rule_text(resolver.subscription_rule(found)))

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
        if resolver.has_billing_rules():
            self.stdout.write('  billing: ' + self.rule_text(
                resolver.billing_rule_for(applies.country_group_id, found.category)))
        self.stdout.write(f'  line price: {price}')
