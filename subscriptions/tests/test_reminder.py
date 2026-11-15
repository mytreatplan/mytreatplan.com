"""The upgrade reminder (epic-subscriptions entry 3): `service.reminder`, the
`upgrade_reminder` tag, the doctor header's item and the home's line. One test (or more)
per row of the plan's matrix. The admin site is the fake of
integration/tests/snapshot_rows.py (no network)."""
import logging
import re

import pytest
from django.template import Context, Template
from django.test import RequestFactory

from integration.tests.snapshot_rows import pull
from subscriptions import service

from .helpers import ADVANCED, BASIC_IB, PREMIUM, catalogue, doctor, load
from .test_upgrade import browser, html, manual, on, staff_account

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('portal_urls')]

HEADER = 'data-upgrade-reminder'
LINE = 'data-upgrade-line'


def header_item(page):
    """The header's reminder link (`(tier, href, text)`), or None."""
    found = re.search(r'<a href="([^"]+)" class="[^"]*" data-upgrade-reminder="(\w+)">'
                      r'([^<]*)</a>', page)
    return found and (found.group(2), found.group(1), found.group(3))


def home_line(page):
    """The home's reminder line (`(tier, text without tags, href)`), or None."""
    found = re.search(r'<span class="[^"]*" data-upgrade-line="(\w+)">(.*?)</span>', page,
                      re.S)
    if not found:
        return None
    href = re.search(r'href="([^"]+)"', found.group(2)).group(1)
    return found.group(1), re.sub(r'<[^>]+>', '', found.group(2)), href


def no_reminder(page):
    assert HEADER not in page and LINE not in page
    assert '· Upgrade' not in page and 'See upgrade options' not in page


def advanced_doctor():
    user = doctor()
    on(user, 'advanced', ADVANCED)
    return user


# --- The service ----------------------------------------------------------------------------

def test_a_basic_doctor_is_reminded_of_basic(admin):
    load(admin)
    found = service.reminder(doctor())
    assert (found.tier, found.subscription.code) == ('basic', 'BAS-0')


def test_an_advanced_doctor_is_reminded_when_premium_is_sold(admin):
    load(admin)
    found = service.reminder(advanced_doctor())
    assert (found.tier, found.subscription.code) == ('advanced', 'ADV-IB')


def test_an_advanced_doctor_is_not_reminded_without_premium(admin):
    load(admin, premium=False)
    user = advanced_doctor()
    assert service.upgrade_options(user).on_offer  # the screen still shows the levels
    assert service.reminder(user) is None


def test_a_premium_doctor_is_not_reminded(admin):
    load(admin)
    user = doctor()
    on(user, 'premium', PREMIUM)
    assert service.reminder(user) is None


def test_a_manual_doctor_is_not_reminded(admin):
    load(admin)
    user = doctor()
    manual(user)
    assert service.reminder(user) is None


@pytest.mark.parametrize('kind', ['staff', 'superuser'])
def test_staff_are_not_reminded(admin, kind):
    load(admin)
    assert service.reminder(staff_account(kind)) is None


@pytest.mark.parametrize('country', ['SE', 'AE', ''])
def test_no_reminder_without_a_group_that_sells(admin, country):
    load(admin)
    assert service.reminder(doctor(country=country)) is None


@pytest.mark.parametrize('options', [{'advanced': False, 'premium': False},
                                     {'advanced_active': False, 'premium_active': False}],
                         ids=['neither sold', 'neither active'])
def test_no_reminder_without_a_sellable_paid_level(admin, options):
    load(admin, **options)
    assert service.reminder(doctor()) is None


def test_no_reminder_without_a_snapshot():
    assert service.reminder(doctor()) is None


# --- The pages ------------------------------------------------------------------------------

def test_a_basic_doctor_sees_the_reminder_in_the_header_and_on_home(admin):
    load(admin)
    page = html(browser(doctor()).get('/en/home/'))
    assert header_item(page) == ('basic', '/en/upgrade/', 'Basic · Upgrade')
    assert home_line(page) == ('basic', 'Level: Basic · See upgrade options',
                               '/en/upgrade/')
    # Between the area's links and the avatar, inside the one nav (the phone menu too).
    nav = re.search(r'<nav id="site-menu".*?</nav>', page, re.S).group(0)
    assert nav.index('data-doctor-nav') < nav.index(HEADER) < nav.index('data-avatar')
    # Never a purchase link: only the upgrade screen.
    assert '/en/upgrade/advanced/' not in page and '/en/upgrade/premium/' not in page


@pytest.mark.parametrize('path', ['welcome/', 'patients/', 'settings/', 'cart/'])
def test_the_header_item_is_on_every_doctor_page(admin, path):
    load(admin)
    page = html(browser(doctor()).get(f'/en/{path}'))
    assert header_item(page) == ('basic', '/en/upgrade/', 'Basic · Upgrade')
    assert LINE not in page  # the line is the home's only


def test_an_advanced_doctor_sees_advanced(admin):
    load(admin)
    page = html(browser(advanced_doctor()).get('/en/home/'))
    assert header_item(page) == ('advanced', '/en/upgrade/', 'Advanced · Upgrade')
    assert home_line(page) == ('advanced', 'Level: Advanced · See upgrade options',
                               '/en/upgrade/')


def test_an_advanced_doctor_without_premium_sold_sees_nothing(admin):
    load(admin, premium=False)
    no_reminder(html(browser(advanced_doctor()).get('/en/home/')))


def test_a_premium_doctor_sees_nothing(admin):
    load(admin)
    user = doctor()
    on(user, 'premium', PREMIUM)
    page = html(browser(user).get('/en/home/'))
    no_reminder(page)
    assert 'Premium' not in page  # not even the level


def test_a_manual_doctor_sees_nothing(admin):
    load(admin)
    user = doctor()
    manual(user)
    page = html(browser(user).get('/en/home/'))
    no_reminder(page)
    assert 'Ormco' not in page


@pytest.mark.parametrize('kind', ['staff', 'superuser'])
def test_staff_see_nothing(admin, kind):
    load(admin)
    no_reminder(html(browser(staff_account(kind)).get('/en/home/')))


@pytest.mark.parametrize('country, options', [
    ('SE', {}), ('AE', {}), ('', {}), ('ES', {'advanced': False, 'premium': False}),
    ('ES', None),
], ids=['nordics', 'uae', 'no country', 'nothing sold', 'no snapshot'])
def test_nothing_to_offer_shows_nothing(admin, country, options):
    if options is not None:
        load(admin, **options)
    no_reminder(html(browser(doctor(country=country)).get('/en/home/')))


@pytest.mark.parametrize('url', ['/en/upgrade/', '/en/upgrade/advanced/',
                                 '/es/upgrade/', '/es/upgrade/premium/'])
def test_no_header_item_on_the_upgrade_pages(admin, url):
    load(admin)
    page = html(browser(doctor()).get(url))
    assert 'data-doctor-header' in page
    assert HEADER not in page and '· Upgrade' not in page and '· Mejorar' not in page


def spanish_catalogue(admin):
    """The catalogue with no text on Iberia's Basic and Advanced: the translated tier
    names."""
    section = catalogue()
    for row in section['subscriptions']:
        if row['id'] in (BASIC_IB, ADVANCED):
            del row['texts']
    assert pull(admin, 1, section) == ('applied', 1)


def test_the_spanish_reminder_of_a_basic_doctor(admin):
    spanish_catalogue(admin)
    page = html(browser(doctor()).get('/es/home/'))
    assert header_item(page) == ('basic', '/es/upgrade/', 'Básica · Mejorar')
    assert home_line(page) == ('basic', 'Nivel: Básica · Ver opciones de mejora',
                               '/es/upgrade/')


def test_the_spanish_reminder_of_an_advanced_doctor(admin):
    spanish_catalogue(admin)
    page = html(browser(advanced_doctor()).get('/es/home/'))
    assert header_item(page) == ('advanced', '/es/upgrade/', 'Avanzada · Mejorar')
    assert home_line(page) == ('advanced', 'Nivel: Avanzada · Ver opciones de mejora',
                               '/es/upgrade/')


def test_the_row_name_in_the_page_language_wins(admin):
    section = catalogue()
    for row in section['subscriptions']:
        if row['id'] == BASIC_IB:
            row['texts']['es'] = {'name': 'Básica plus', 'description': ''}
    assert pull(admin, 1, section) == ('applied', 1)
    page = html(browser(doctor()).get('/es/home/'))
    assert header_item(page)[2] == 'Básica plus · Mejorar'


def test_a_catalogue_fault_leaves_the_page_without_the_reminder(admin, monkeypatch,
                                                                 caplog):
    load(admin)
    user = doctor()

    def broken(user, day=None, **kwargs):
        raise RuntimeError('catalogue down')

    monkeypatch.setattr(service, 'upgrade_options', broken)
    with caplog.at_level(logging.ERROR, logger='subscriptions.templatetags'):
        for path in ('home/', 'patients/'):
            no_reminder(html(browser(user).get(f'/en/{path}')))
    errors = [record for record in caplog.records
              if record.name == 'subscriptions.templatetags.upgrade_reminder']
    assert errors and all(record.levelno == logging.ERROR and record.exc_info
                          for record in errors)
    # Once per request: one error for each page.
    assert len(errors) == 2


def test_the_options_are_read_once_for_header_and_home(admin, monkeypatch):
    load(admin)
    user = doctor()
    calls = []
    real = service.upgrade_options

    def counted(user, day=None, **kwargs):
        calls.append(user.pk)
        return real(user, day, **kwargs)

    monkeypatch.setattr(service, 'upgrade_options', counted)
    page = html(browser(user).get('/en/home/'))
    assert header_item(page) and home_line(page)
    assert calls == [user.pk]


def test_a_failure_in_the_name_lookup_leaves_the_page_without_the_reminder(
        admin, monkeypatch, caplog):
    from subscriptions.templatetags import upgrade_reminder as tag
    load(admin)
    user = doctor()

    def broken(row, tier, language):
        raise KeyError(tier)

    monkeypatch.setattr(tag, 'level_name', broken)
    with caplog.at_level(logging.ERROR, logger='subscriptions.templatetags'):
        no_reminder(html(browser(user).get('/en/home/')))
    errors = [record for record in caplog.records
              if record.name == 'subscriptions.templatetags.upgrade_reminder']
    assert len(errors) == 1 and errors[0].exc_info  # once per request, header and home


@pytest.mark.parametrize('drift', ['gone', 'edited'])
def test_a_drifted_held_row_is_not_logged_by_the_reminder(admin, caplog, drift):
    """The held row is missing from the snapshot, or now another tier under the same id:
    the reminder (every doctor page) stays quiet; `effective` still logs it."""
    load(admin)
    user = advanced_doctor()
    if drift == 'gone':
        load(admin, version=2, advanced=False)
    else:
        section = catalogue(premium=False)  # the edited row is the group's Premium
        for row in section['subscriptions']:
            if row['id'] == ADVANCED:
                row['tier'] = 'premium'
        assert pull(admin, 2, section) == ('applied', 2)
    with caplog.at_level(logging.ERROR, logger='subscriptions'):
        page = html(browser(user).get('/en/home/'))
    assert header_item(page) == ('advanced', '/en/upgrade/', 'Advanced · Upgrade')
    assert [record for record in caplog.records if record.levelno >= logging.ERROR] == []
    with caplog.at_level(logging.ERROR, logger='subscriptions'):
        service.effective(user)
    assert [record.name for record in caplog.records
            if record.levelno >= logging.ERROR] == ['subscriptions.service']


# --- The tag --------------------------------------------------------------------------------

TAG = Template('{% load upgrade_reminder %}{% upgrade_reminder as r %}'
               '{% if r %}{{ r.tier }} {{ r.name }} {{ r.url }}{% else %}none{% endif %}')


def test_the_tag_without_a_request_or_signed_out(admin):
    from django.contrib.auth.models import AnonymousUser
    load(admin)
    assert TAG.render(Context({})) == 'none'
    request = RequestFactory().get('/en/home/')
    request.user = AnonymousUser()
    assert TAG.render(Context({'request': request})) == 'none'


def test_the_tag_keeps_its_result_on_the_request(admin, monkeypatch):
    load(admin)
    request = RequestFactory().get('/en/home/')
    request.user = doctor()
    calls = []
    real = service.reminder
    monkeypatch.setattr(service, 'reminder',
                        lambda user, day=None, **kwargs: calls.append(1) or real(user, day))
    assert TAG.render(Context({'request': request})) == 'basic Basic /en/upgrade/'
    assert TAG.render(Context({'request': request})) == 'basic Basic /en/upgrade/'
    assert calls == [1]
