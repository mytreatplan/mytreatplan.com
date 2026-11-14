"""The upgrade screen (epic-subscriptions entry 2): `service.upgrade_options`, the views
`upgrade` and `upgrade_purchase`, signup's redirect and the `money` filter. One test (or
more) per row of the plan's matrix. The admin site is the fake of
integration/tests/snapshot_rows.py (no network)."""
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import Client
from django.utils import translation

from accounts.tests.helpers import _login_with_code
from catalogue import resolver
from eventlog.models import Attempt, Event
from integration.tests.snapshot_rows import ADA, pull, rules
from mytp_core.money import Money
from signup.tests.helpers import NORMALIZED, _complete
from subscriptions import service

from .helpers import (ADVANCED, BASIC_IB, MANUAL, PASSWORD, PREMIUM, catalogue, doctor, load)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]
User = get_user_model()
SYSTEM = service.SYSTEM_ACTOR
UPGRADE, WELCOME = '/en/upgrade/', '/en/welcome/'


def browser(user):
    client = Client()
    client.force_login(user)
    return client


def on(user, tier, subscription_id, **fields):
    """The doctor moved to `tier` (by the system)."""
    record = service.record_of(user)
    return service.change(user, expected_version=record.version, actor=SYSTEM,
                          reason='test', tier=tier, subscription_id=subscription_id,
                          billing_period='monthly', **fields)


def manual(user):
    return service.assign_manual(user, resolver.subscription_by_id(MANUAL),
                                 expected_version=service.record_of(user).version,
                                 actor=service.admin_actor(ADA))


def levels(options):
    return [(level.tier, level.free, level.monthly, level.upfront, level.current,
             level.can_upgrade) for level in options.levels]


def html(response):
    assert response.status_code == 200
    return response.content.decode()


def eur(amount):
    return Money(Decimal(amount), 'EUR')


# --- The service ----------------------------------------------------------------------------

def test_a_basic_doctor_of_a_full_group_is_offered_advanced_and_premium(admin):
    load(admin)
    options = service.upgrade_options(doctor())
    assert options.on_offer and options.can_switch and not options.manual
    assert options.tier == 'basic' and options.subscription.code == 'BAS-0'
    assert levels(options) == [
        ('basic', True, None, None, True, False),
        ('advanced', False, eur('60.00'), eur('600.00'), False, True),
        ('premium', False, eur('120.00'), eur('1200.00'), False, True)]


def test_a_missing_period_is_left_out(admin):
    load(admin, advanced_price={'currency': 'EUR', 'monthly': '60.00'})
    advanced = service.upgrade_options(doctor()).level('advanced')
    assert (advanced.monthly, advanced.upfront) == (eur('60.00'), None)


@pytest.mark.parametrize('options, listed', [
    ({'premium': False}, ['basic', 'advanced']),
    ({'premium_active': False}, ['basic', 'advanced']),
    ({'advanced_active': False}, ['basic', 'premium']),
], ids=['no premium', 'premium inactive', 'advanced inactive'])
def test_a_level_missing_or_inactive_is_not_listed(admin, options, listed):
    load(admin, **options)
    found = service.upgrade_options(doctor())
    assert found.on_offer and [level.tier for level in found.levels] == listed


def edited(edit):
    """The test catalogue with `edit(section, rows)` applied (`rows`: the subscriptions by
    id)."""
    section = catalogue()
    edit(section, {row['id']: row for row in section['subscriptions']})
    return section


def subscription_rule(**changes):
    """The rules with Iberia's `subscription` rule changed."""
    return [{**rule, **changes} if rule['category'] == 'subscription' else rule
            for rule in rules()]


@pytest.mark.parametrize('edit, listed', [
    (lambda section, rows: rows[PREMIUM].pop('price'), ['basic', 'advanced']),
    (lambda section, rows: rows[PREMIUM]['price'].update(currency='USD'),
     ['basic', 'advanced']),
], ids=['premium without a price', 'premium in another currency than the rule'])
def test_a_level_that_cannot_be_bought_is_not_listed(admin, edit, listed):
    assert pull(admin, 1, edited(edit)) == ('applied', 1)
    found = service.upgrade_options(doctor())
    assert found.on_offer and [level.tier for level in found.levels] == listed
    assert [level.free for level in found.levels] == [True, False]


@pytest.mark.parametrize('section', [
    lambda: catalogue(advanced=False, premium=False),
    # Neither paid level has a price: neither can be bought.
    lambda: edited(lambda section, rows: (rows[ADVANCED].pop('price'),
                                          rows[PREMIUM].pop('price'))),
    # The `subscription` rule is not available, or in another currency than the rows.
    lambda: {**catalogue(), 'billing_rules': subscription_rule(available=False)},
    lambda: {**catalogue(), 'billing_rules': subscription_rule(currency='USD')},
    lambda: catalogue(advanced_active=False, premium_active=False),
    # The group sells both, but has no `subscription` billing rule.
    lambda: {**catalogue(), 'billing_rules': [
        rule for rule in rules() if rule['category'] != 'subscription']},
], ids=['neither sold', 'neither priced', 'rule unavailable', 'rule in USD',
        'neither active', 'no subscription rule'])
def test_nothing_on_offer_without_a_sellable_paid_level(admin, section):
    assert pull(admin, 1, section()) == ('applied', 1)
    assert service.upgrade_options(doctor()) == service._NOTHING


@pytest.mark.parametrize('country', ['SE', 'AE', ''])
def test_nothing_on_offer_without_a_group_that_sells(admin, country):
    """SE: the Nordics (no Basic, no rule); AE: the UAE (no rule); '': no group."""
    load(admin)
    assert not service.upgrade_options(doctor(country=country)).on_offer


def test_nothing_on_offer_without_a_snapshot():
    assert not service.upgrade_options(doctor()).on_offer


def staff_account(kind):
    """A staff account that is not a superuser, or a superuser."""
    if kind == 'superuser':
        return User.objects.create_superuser('root@example.net', PASSWORD, country='ES')
    return User.objects.create_user('staff@example.net', PASSWORD, country='ES',
                                    is_staff=True)


@pytest.mark.parametrize('kind', ['staff', 'superuser'])
def test_nothing_on_offer_for_staff(admin, kind):
    load(admin)
    assert service.upgrade_options(staff_account(kind)) == service._NOTHING


def test_a_manual_doctor_sees_the_levels_without_buttons(admin):
    load(admin)
    user = doctor()
    manual(user)
    options = service.upgrade_options(user)
    assert options.on_offer and options.manual and not options.can_switch
    assert (options.tier, options.subscription.code) == ('premium', 'ORMCO')
    assert [(level.tier, level.current, level.can_upgrade) for level in options.levels] == [
        ('basic', False, False), ('advanced', False, False), ('premium', False, False)]


def test_an_advanced_doctor_may_upgrade_to_premium_only(admin):
    load(admin)
    user = doctor()
    on(user, 'advanced', ADVANCED)
    options = service.upgrade_options(user)
    assert (options.tier, options.subscription.code) == ('advanced', 'ADV-IB')
    assert [(level.tier, level.current, level.can_upgrade) for level in options.levels] == [
        ('basic', False, False), ('advanced', True, False), ('premium', False, True)]


def test_a_premium_doctor_has_no_upgrade(admin):
    load(admin)
    user = doctor()
    on(user, 'premium', PREMIUM)
    options = service.upgrade_options(user)
    assert options.on_offer and options.tier == 'premium'
    assert [(level.tier, level.current, level.can_upgrade) for level in options.levels] == [
        ('basic', False, False), ('advanced', False, False), ('premium', True, False)]


# --- Signup and login -----------------------------------------------------------------------

@pytest.fixture
def signed_up(client, capture_codes, clear_cache):
    def run(landing):
        response = _complete(client)
        assert response['Location'] == landing
        return User.objects.by_email(NORMALIZED).get()
    return run


def test_signup_in_a_full_group_lands_on_the_upgrade_screen(admin, client, signed_up):
    load(admin)
    signed_up(UPGRADE)
    page = html(client.get(UPGRADE))
    assert 'Your current level' in page
    assert 'data-leave>Not now</a>' in page and f'href="{WELCOME}"' in page


def test_signup_with_nothing_on_offer_lands_on_welcome(admin, signed_up):
    load(admin, advanced=False, premium=False)
    signed_up(WELCOME)


def test_signup_lands_on_welcome_when_the_options_fail(admin, signed_up, monkeypatch,
                                                      caplog):
    """The account exists by then: a catalogue error is logged, never a failed signup."""
    load(admin)

    def broken(user, day=None):
        raise RuntimeError('broken catalogue')
    monkeypatch.setattr(service, 'upgrade_options', broken)
    user = signed_up(WELCOME)
    assert service.record_of(user).tier == 'basic'
    [logged] = [record for record in caplog.records if record.name == 'signup.views']
    assert logged.levelname == 'ERROR' and 'upgrade options' in logged.getMessage()
    assert NORMALIZED not in logged.getMessage()  # no personal values


def test_a_later_login_lands_on_welcome_and_the_screen_stays_open(admin, client, signed_up):
    load(admin)
    signed_up(UPGRADE)
    client.logout()
    assert _login_with_code(client)['Location'] == WELCOME
    assert 'Subscription levels' in html(client.get(UPGRADE))


# --- The screen -----------------------------------------------------------------------------

def test_the_screen_of_a_basic_doctor(admin):
    load(admin)
    page = html(browser(doctor()).get(UPGRADE))
    assert 'Your current level is <strong class="text-ink">Basic</strong>.' in page
    assert page.count('data-current>') == 1
    assert 'data-level="basic" aria-current="true"' in page
    for text in ('Free', '60.00 EUR per month', '600.00 EUR for a year, paid upfront',
                 '120.00 EUR per month', '1,200.00 EUR for a year, paid upfront',
                 '12-month commitment', '+ VAT where applicable'):
        assert text in page, text
    assert page.count('+ VAT where applicable') == 2  # not on Basic
    assert 'href="/en/upgrade/advanced/"' in page and 'href="/en/upgrade/premium/"' in page
    assert 'Upgrade to Advanced' in page and 'Upgrade to Premium' in page
    assert 'Not now' in page and 'data-manual-note' not in page
    assert 'max-w-[1040px]' in page and 'max-w-[480px]' not in page


def test_the_screen_leaves_out_a_missing_period(admin):
    load(admin, advanced_price={'currency': 'EUR', 'monthly': '60.00'})
    page = html(browser(doctor()).get(UPGRADE))
    assert '60.00 EUR per month' in page and '600.00 EUR' not in page


@pytest.mark.parametrize('options', [{'advanced': False, 'premium': False}, None],
                         ids=['nothing sold', 'no snapshot'])
def test_the_screen_redirects_to_welcome_with_nothing_on_offer(admin, options):
    if options is not None:
        load(admin, **options)
    user = doctor()
    for url in (UPGRADE, '/en/upgrade/advanced/'):
        response = browser(user).get(url)
        assert (response.status_code, response['Location']) == (302, WELCOME)


def test_a_manual_doctor_sees_its_name_no_buttons_and_continue(admin):
    load(admin)
    user = doctor()
    manual(user)
    page = html(browser(user).get(UPGRADE))
    assert 'Your current level is <strong class="text-ink">Ormco</strong>.' in page
    assert 'data-manual-note' in page and 'data-current>' not in page
    assert '/en/upgrade/advanced/' not in page and 'Upgrade to' not in page
    assert 'Continue' in page and 'Not now' not in page
    assert '120.00 EUR per month' in page  # the standard levels, for information
    for level in ('advanced', 'premium', 'basic', 'gold'):
        response = browser(user).get(f'/en/upgrade/{level}/')
        assert (response.status_code, response['Location']) == (302, UPGRADE)


def test_an_advanced_doctor_has_a_button_on_premium_only(admin):
    load(admin)
    user = doctor()
    on(user, 'advanced', ADVANCED)
    page = html(browser(user).get(UPGRADE))
    assert 'data-level="advanced" aria-current="true"' in page
    assert '/en/upgrade/premium/' in page and '/en/upgrade/advanced/' not in page


def test_a_premium_doctor_has_no_button(admin):
    load(admin)
    user = doctor()
    on(user, 'premium', PREMIUM)
    page = html(browser(user).get(UPGRADE))
    assert 'data-level="premium" aria-current="true"' in page
    assert 'Upgrade to' not in page and 'Not now' in page


@pytest.mark.parametrize('kind', ['staff', 'superuser'])
def test_staff_are_sent_to_welcome(admin, kind):
    load(admin)
    response = browser(staff_account(kind)).get(UPGRADE)
    assert (response.status_code, response['Location']) == (302, WELCOME)


@pytest.mark.parametrize('url', [UPGRADE, '/en/upgrade/advanced/'])
def test_signed_out_goes_to_login(url):
    response = Client().get(url)
    assert response.status_code == 302
    assert response['Location'] == f'/en/login/?next={url}'


def test_the_spanish_screen(admin):
    section = catalogue()
    for row in section['subscriptions']:
        if row['id'] == ADVANCED:
            row['texts']['es'] = {'name': 'Avanzada plus', 'description': 'Más descuento'}
        if row['id'] == BASIC_IB:
            del row['texts']  # none sent: the translated tier name
    assert pull(admin, 1, section) == ('applied', 1)
    page = html(browser(doctor()).get('/es/upgrade/'))
    for text in ('Niveles de suscripción', 'Avanzada plus', 'Más descuento',
                 '60,00 EUR al mes', '600,00 EUR por un año', '+ IVA cuando corresponda',
                 'Compromiso de 12 meses', 'Gratis', 'Pasar a Premium', 'Ahora no',
                 'Su nivel actual es <strong class="text-ink">Básica</strong>.',
                 'href="/es/upgrade/advanced/"', 'href="/es/welcome/"'):
        assert text in page, text


def test_the_screen_is_a_recorded_portal_page(admin):
    load(admin)
    user = doctor()
    html(browser(user).get(UPGRADE))
    html(browser(user).get('/en/upgrade/advanced/'))
    records = Event.objects.filter(attempt__kind=Attempt.PAGE).order_by('created_at', 'id')
    assert [(record.step, record.reason, record.attempt.account_id) for record in records] \
        == [('upgrade', '200', user.pk), ('upgrade_purchase', '200', user.pk)]


# --- The placeholder ------------------------------------------------------------------------

def test_the_placeholder_of_a_higher_level(admin):
    load(admin)
    page = html(browser(doctor()).get('/en/upgrade/advanced/'))
    assert '<h1' in page and 'Upgrade to Advanced' in page
    assert 'Online purchase of Advanced is coming soon.' in page
    assert 'href="/en/upgrade/"' in page


@pytest.mark.parametrize('tier, level', [
    (None, 'basic'), (None, 'gold'), ('advanced', 'advanced'), ('advanced', 'basic'),
    ('premium', 'premium'), ('premium', 'advanced'),
])
def test_the_placeholder_refuses_any_other_level(admin, tier, level):
    load(admin)
    user = doctor()
    if tier:
        on(user, tier, {'advanced': ADVANCED, 'premium': PREMIUM}[tier])
    assert browser(user).get(f'/en/upgrade/{level}/').status_code == 404


def test_the_placeholder_refuses_a_level_not_listed(admin):
    load(admin, premium=False)
    assert browser(doctor()).get('/en/upgrade/premium/').status_code == 404


# --- The money filter -----------------------------------------------------------------------

@pytest.mark.parametrize('language, value, text', [
    ('en', eur('60.00'), '60.00 EUR'), ('es', eur('60.00'), '60,00 EUR'),
    # Django's Spanish format groups with a no-break space (U+00A0).
    ('es', Money(Decimal('1200.5'), 'USD'), '1\u00a0200,50 USD'),
    ('en', eur('1200.00'), '1,200.00 EUR'), ('en', eur('12000.00'), '12,000.00 EUR'),
    ('en', None, ''), ('en', Decimal('60.00'), ''), ('es', '60.00', ''),
])
def test_the_money_filter(language, value, text):
    with translation.override(language):
        assert Template('{% load money %}{{ value|money }}').render(
            Context({'value': value})) == text

