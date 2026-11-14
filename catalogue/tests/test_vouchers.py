"""Vouchers on the portal (epic-catalogue entry 10, mytp-core 0.24.0): the read-only table
the snapshot apply replaces (catalogue.applier.apply_vouchers; a voucher of another region
skipped; an absent list empties it), the resolver (a voucher by its code as a doctor types
it; whether it is usable on a day) and `price_catalogue --voucher`. One test (or more) per
row of the plan's matrix that the portal decides. The admin site is the fake of
integration/tests/snapshot_rows.py (no network)."""
import logging
from datetime import date
from decimal import Decimal
from functools import partial

import pytest
from django.core.management import CommandError

from catalogue import resolver
from catalogue.models import Voucher
from catalogue.tests.test_catalogue import run
from integration import config
from integration.tests import snapshot_rows
from integration.tests.snapshot_rows import (ADMIN, GENERAL, TRY_ME, snapshot, state,
                                             voucher)
from mytp_core import snapshots
from mytp_core.money import Money

FOR_ANA = 'f0000002-0000-4000-8000-000000000002'
OTHER_REGION = 'f0000003-0000-4000-8000-000000000003'
ANA = '9a000000-0000-4000-8000-000000000001'
DAY = date(2026, 11, 12)
DOCTOR = voucher(FOR_ANA, 'ABCDE23456', type_='subscription', amount='100.00',
                 client=ANA, valid_to='2026-12-31')


# pull(admin, version, vouchers=None, **changes): the snapshot with that vouchers list.
pull = partial(snapshot_rows.pull, key='vouchers')


# --- The apply ----------------------------------------------------------------------------

@pytest.mark.django_db
def test_the_vouchers_of_this_region_are_stored_and_read_by_code(admin):
    assert pull(admin, 1, [GENERAL, DOCTOR]) == ('applied', 1)
    general = resolver.voucher_by_code('K7QM2XH9TR')
    assert (general.type, general.amount, general.currency, general.client) == (
        'try_me', Decimal('50.00'), 'EUR', None)
    assert general.money == Money('50.00', 'EUR') and general.display_code == 'K7QM2-XH9TR'
    doctor = resolver.voucher_by_code('ABCDE-23456')
    assert str(doctor.client) == ANA and doctor.valid_to == date(2026, 12, 31)


@pytest.mark.django_db
@pytest.mark.parametrize('typed', ['k7qm2-xh9tr', 'K7QM2XH9TR', ' k7qm2xh9tr ', 'K7QM2 XH9TR'])
def test_a_code_is_read_with_or_without_the_hyphen_in_any_case(admin, typed):
    pull(admin, 1, [GENERAL])
    assert resolver.voucher_by_code(typed).pk is not None


@pytest.mark.django_db
@pytest.mark.parametrize('typed', ['', None, 'K7QM2_XH9TR', 'NOPE23456X'])
def test_an_unknown_or_malformed_code_is_none(admin, typed):
    pull(admin, 1, [GENERAL])
    assert resolver.voucher_by_code(typed) is None


@pytest.mark.django_db
def test_a_voucher_of_another_region_is_skipped_with_a_warning(admin, caplog):
    other = voucher(OTHER_REGION, 'ZZZZZ99999', region='ae', currency='AED')
    with caplog.at_level(logging.WARNING, logger='catalogue.applier'):
        assert pull(admin, 1, [GENERAL, other]) == ('applied', 1)
    assert list(Voucher.objects.values_list('code', flat=True)) == ['K7QM2XH9TR']
    assert resolver.voucher_by_code('ZZZZZ99999') is None
    assert '1 voucher(s) of another region skipped' in caplog.text
    assert 'ZZZZZ99999' not in caplog.text


@pytest.mark.django_db
def test_on_dev_ae_only_the_ae_voucher_is_kept(admin, settings):
    settings.REGION = 'ae'
    other = voucher(OTHER_REGION, 'ZZZZZ99999', region='ae', amount='200.00',
                    currency='AED')
    pull(admin, 1, [GENERAL, other])
    assert list(Voucher.objects.values_list('code', flat=True)) == ['ZZZZZ99999']


@pytest.mark.django_db
def test_a_snapshot_without_vouchers_empties_the_table(admin):
    pull(admin, 1, [GENERAL, DOCTOR])
    # An admin site before 0.24.0, or a region left without vouchers: no list.
    assert pull(admin, 2) == ('applied', 2)
    assert not Voucher.objects.exists()


@pytest.mark.django_db
def test_an_old_snapshot_keeps_the_table_empty_and_works(admin):
    assert pull(admin, 1) == ('applied', 1)
    assert not Voucher.objects.exists() and resolver.voucher_by_code('K7QM2XH9TR') is None
    assert run('--voucher', 'K7QM2XH9TR').strip() == 'no voucher with this code'


@pytest.mark.django_db
def test_a_release_without_the_applier_applies_and_ignores_them(admin, monkeypatch):
    # A portal before this entry has no applier for the list: it applies the snapshot.
    monkeypatch.delitem(config._SECTION_APPLIERS, 'vouchers')
    assert pull(admin, 1, [GENERAL]) == ('applied', 1)
    assert state().version == 1 and not Voucher.objects.exists()


@pytest.mark.django_db
def test_an_invalid_list_is_refused_and_the_applied_one_kept(admin):
    import jwt
    pull(admin, 1, [GENERAL])

    def invalid(answer):
        # Signed correctly over a snapshot whose vouchers list a code twice.
        bad = {**snapshot(version=2), 'vouchers': [GENERAL, voucher(FOR_ANA, 'K7QM2XH9TR')]}
        claims = jwt.decode(answer['signature'], options={'verify_signature': False})
        claims['sha'] = snapshots.snapshot_sha(bad)
        answer['snapshot'] = bad
        answer['signature'] = ADMIN.sign(claims)
    admin.tamper = invalid
    with pytest.raises(config.PullFailed) as error:
        pull(admin, 2, [GENERAL])
    assert error.value.code == 'invalid_body'
    assert state().version == 1 and Voucher.objects.count() == 1


@pytest.mark.django_db
def test_no_write_path_outside_the_snapshot_apply(admin):
    pull(admin, 1, [GENERAL])
    stored = Voucher.objects.get()
    with pytest.raises(TypeError):
        stored.save()
    with pytest.raises(TypeError):
        stored.delete()


@pytest.mark.django_db
def test_the_applied_log_line_counts_the_vouchers(admin, caplog):
    with caplog.at_level(logging.INFO):
        pull(admin, 1, [GENERAL, DOCTOR])
    assert 'vouchers: 2' in caplog.text and 'K7QM2XH9TR' not in caplog.text


# --- Usable on a day ----------------------------------------------------------------------

@pytest.mark.django_db
def test_an_inactive_voucher_is_sent_and_not_usable_then_usable_again(admin):
    pull(admin, 1, [GENERAL])
    assert resolver.voucher_state(resolver.voucher_by_code('K7QM2XH9TR'), DAY) == 'usable'
    pull(admin, 2, [{**GENERAL, 'active': False}])
    found = resolver.voucher_by_code('K7QM2XH9TR')
    assert found.active is False and resolver.voucher_state(found, DAY) == 'inactive'
    assert not resolver.voucher_usable(found, DAY)
    pull(admin, 3, [GENERAL])
    assert resolver.voucher_usable(resolver.voucher_by_code('K7QM2XH9TR'), DAY)


@pytest.mark.django_db
def test_outside_its_dates_a_voucher_is_not_in_force(admin):
    pull(admin, 1, [DOCTOR])
    found = resolver.voucher_by_code('ABCDE23456')
    assert resolver.voucher_state(found, date(2026, 11, 11)) == 'not_in_force'
    assert resolver.voucher_state(found, date(2026, 11, 12)) == 'usable'
    assert resolver.voucher_state(found, date(2026, 12, 31)) == 'usable'
    assert resolver.voucher_state(found, date(2027, 1, 1)) == 'not_in_force'


@pytest.mark.django_db
def test_today_is_a_madrid_day(admin, monkeypatch):
    from datetime import datetime, timezone as dt_timezone
    pull(admin, 1, [GENERAL])
    # 23:30 UTC on 11 November is 12 November in Madrid: the first day.
    monkeypatch.setattr(resolver.timezone, 'now',
                        lambda: datetime(2026, 11, 11, 23, 30, tzinfo=dt_timezone.utc))
    assert resolver.voucher_usable(resolver.voucher_by_code('K7QM2XH9TR'))


@pytest.mark.django_db
def test_a_type_or_currency_this_release_does_not_know_is_kept_and_not_usable(admin):
    pull(admin, 1, [voucher(TRY_ME, 'LOYAL23456', type_='loyalty'),
                    voucher(FOR_ANA, 'POUND23456', currency='GBP')])
    loyalty = resolver.voucher_by_code('LOYAL23456')
    assert loyalty.type == 'loyalty' and resolver.voucher_state(loyalty, DAY) == 'unknown_type'
    pound = resolver.voucher_by_code('POUND23456')
    assert pound.money is None and resolver.voucher_state(pound, DAY) == 'unknown_currency'


# --- price_catalogue --voucher --------------------------------------------------------------

@pytest.mark.django_db
def test_price_catalogue_prints_each_voucher(admin):
    pull(admin, 1, [GENERAL, DOCTOR])
    general = run('--voucher', 'k7qm2-xh9tr', '--on', '2026-11-12')
    assert general.splitlines() == [
        'K7QM2-XH9TR  try_me  50.00 EUR',
        '  doctor: any doctor',
        '  valid: 2026-11-12 to no end',
        '  active; on 2026-11-12: usable']
    doctor = run('--voucher', 'ABCDE23456', '--on', '2027-01-01')
    assert 'ABCDE-23456  subscription  100.00 EUR' in doctor
    assert f'doctor: {ANA}' in doctor and 'valid: 2026-11-12 to 2026-12-31' in doctor
    assert 'on 2027-01-01: not usable: not in force' in doctor


@pytest.mark.django_db
@pytest.mark.parametrize('others', [('--country', 'ES', '--tier', 'basic'),
                                    ('--language', 'es')])
def test_price_catalogue_voucher_goes_alone(admin, others):
    pull(admin, 1, [GENERAL])
    with pytest.raises(CommandError, match='--voucher goes alone'):
        run('--voucher', 'K7QM2XH9TR', *others)
