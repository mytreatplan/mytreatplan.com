"""The subscriptions tests' catalogue and doctors: billing's catalogue
(integration/tests/snapshot_rows.py: a Basic per country group, the manual Ormco premium
of Iberia, the companies and the rules), with an Advanced of Iberia at 6 % on TPS
branded (60.00 EUR a month, 600.00 EUR a year upfront), a Premium of Iberia (120.00 and
1200.00 EUR) and a group (Nordics) that has no Basic. Only Iberia has a `subscription`
billing rule."""
import copy
from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction

from integration.tests.snapshot_rows import (ALL, IBERIA, MANUAL, SUBSCRIPTIONS, TPS,
                                             billed, full_subscription, group, pull)
from subscriptions import service

User = get_user_model()
PASSWORD = 'violet-orbit-canyon-42'

ADVANCED = 'e0000010-0000-4000-8000-000000000010'
PREMIUM = 'e0000012-0000-4000-8000-000000000012'
NEW_BASIC = 'e0000011-0000-4000-8000-000000000011'
NORDICS = 'a3333333-3333-4333-8333-333333333333'
BASIC_IB = SUBSCRIPTIONS[IBERIA]
DAY = date(2026, 11, 14)

__all__ = ['ADVANCED', 'ADVANCED_PRICE', 'BASIC_IB', 'DAY', 'MANUAL', 'NEW_BASIC', 'NORDICS',
           'PREMIUM', 'PREMIUM_PRICE', 'TPS', 'catalogue', 'doctor', 'load', 'priced']

ADVANCED_PRICE = {'currency': 'EUR', 'monthly': '60.00', 'upfront': '600.00'}
PREMIUM_PRICE = {'currency': 'EUR', 'monthly': '120.00', 'upfront': '1200.00'}


def catalogue(advanced_active=True, advanced=True, basic_ib=BASIC_IB, premium=True,
              advanced_price=ADVANCED_PRICE, premium_active=True):
    """Billing's catalogue with Iberia's Advanced (`advanced_active`, `advanced_price`;
    left out without `advanced`), Iberia's Premium (`premium_active`; left out without
    `premium`), Iberia's Basic under the id `basic_ib`, and the Nordics without a
    Basic."""
    section = billed()
    section['country_groups'].append(group(NORDICS, 'Nordics', ['SE']))
    for row in section['subscriptions']:
        if row['id'] == BASIC_IB:
            row['id'] = basic_ib
    if advanced:
        section['subscriptions'].append(full_subscription(
            ADVANCED, 'advanced', IBERIA, 'ADV-IB', 'Advanced', ALL,
            {'tps_branded': '6.00'}, active=advanced_active, price=dict(advanced_price)))
    if premium:
        section['subscriptions'].append(full_subscription(
            PREMIUM, 'premium', IBERIA, 'PRE-IB', 'Premium', ALL,
            {'tps_branded': '12.00'}, active=premium_active, price=dict(PREMIUM_PRICE)))
    return copy.deepcopy(section)


def load(admin, version=1, **options):
    assert pull(admin, version, catalogue(**options)) == ('applied', version)


def doctor(email='bea@example.com', country='ES', **fields):
    """A doctor with a Basic record, as signup gives one (by the doctor)."""
    with transaction.atomic():
        user = User.objects.create_user(email, PASSWORD, country=country, **fields)
        service.ensure_basic(user, service.doctor_actor(user))
    return user


def priced(user, product_id=TPS, day=DAY):
    from catalogue import resolver
    return service.price_order_for(user, [resolver.OrderLine(resolver.product(product_id))],
                                   vies=resolver.VIES_NOT_APPLICABLE, day=day)
