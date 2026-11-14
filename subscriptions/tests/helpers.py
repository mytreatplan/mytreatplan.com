"""The subscriptions tests' catalogue and doctors: billing's catalogue
(integration/tests/snapshot_rows.py: a Basic per country group, the manual Ormco premium
of Iberia, the companies and the rules), with an Advanced of Iberia at 6 % on TPS
branded and a group (Nordics) that has no Basic."""
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
NEW_BASIC = 'e0000011-0000-4000-8000-000000000011'
NORDICS = 'a3333333-3333-4333-8333-333333333333'
BASIC_IB = SUBSCRIPTIONS[IBERIA]
DAY = date(2026, 11, 14)

__all__ = ['ADVANCED', 'BASIC_IB', 'DAY', 'MANUAL', 'NEW_BASIC', 'NORDICS', 'TPS',
           'catalogue', 'doctor', 'load', 'priced']


def catalogue(advanced_active=True, advanced=True, basic_ib=BASIC_IB):
    """Billing's catalogue with Iberia's Advanced (`advanced_active`; left out without
    `advanced`), Iberia's Basic under the id `basic_ib`, and the Nordics without a
    Basic."""
    section = billed()
    section['country_groups'].append(group(NORDICS, 'Nordics', ['SE']))
    for row in section['subscriptions']:
        if row['id'] == BASIC_IB:
            row['id'] = basic_ib
    if advanced:
        section['subscriptions'].append(full_subscription(
            ADVANCED, 'advanced', IBERIA, 'ADV-IB', 'Advanced', ALL,
            {'tps_branded': '6.00'}, active=advanced_active,
            price={'currency': 'EUR', 'monthly': '60.00'}))
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
