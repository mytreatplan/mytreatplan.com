# Epic-regions entry 1: seed the country map with the agreed lists (user, 2026-10-30 and
# 2026-11-01). One row per ISO 3166-1 country (django-countries 9.1.0): ae for the AE
# list, banned for every other African country plus IR, SY, KP, CU, RU and BY, eu for
# every other. Frozen copies of the ISO code list and of regions/countries.py's lists as
# they are at this migration; a later change is a new data migration
# (regions/tests/test_country_map.py checks the migrated map against the lists). Going
# back empties the table.

from django.db import migrations

# Every ISO 3166-1 alpha-2 code (django-countries 9.1.0), frozen here: a later package
# change needs a new data migration, not a different seed.
ISO_CODES = frozenset({
    'AD', 'AE', 'AF', 'AG', 'AI', 'AL', 'AM', 'AO', 'AQ', 'AR', 'AS', 'AT', 'AU', 'AW',
    'AX', 'AZ', 'BA', 'BB', 'BD', 'BE', 'BF', 'BG', 'BH', 'BI', 'BJ', 'BL', 'BM', 'BN',
    'BO', 'BQ', 'BR', 'BS', 'BT', 'BV', 'BW', 'BY', 'BZ', 'CA', 'CC', 'CD', 'CF', 'CG',
    'CH', 'CI', 'CK', 'CL', 'CM', 'CN', 'CO', 'CR', 'CU', 'CV', 'CW', 'CX', 'CY', 'CZ',
    'DE', 'DJ', 'DK', 'DM', 'DO', 'DZ', 'EC', 'EE', 'EG', 'EH', 'ER', 'ES', 'ET', 'FI',
    'FJ', 'FK', 'FM', 'FO', 'FR', 'GA', 'GB', 'GD', 'GE', 'GF', 'GG', 'GH', 'GI', 'GL',
    'GM', 'GN', 'GP', 'GQ', 'GR', 'GS', 'GT', 'GU', 'GW', 'GY', 'HK', 'HM', 'HN', 'HR',
    'HT', 'HU', 'ID', 'IE', 'IL', 'IM', 'IN', 'IO', 'IQ', 'IR', 'IS', 'IT', 'JE', 'JM',
    'JO', 'JP', 'KE', 'KG', 'KH', 'KI', 'KM', 'KN', 'KP', 'KR', 'KW', 'KY', 'KZ', 'LA',
    'LB', 'LC', 'LI', 'LK', 'LR', 'LS', 'LT', 'LU', 'LV', 'LY', 'MA', 'MC', 'MD', 'ME',
    'MF', 'MG', 'MH', 'MK', 'ML', 'MM', 'MN', 'MO', 'MP', 'MQ', 'MR', 'MS', 'MT', 'MU',
    'MV', 'MW', 'MX', 'MY', 'MZ', 'NA', 'NC', 'NE', 'NF', 'NG', 'NI', 'NL', 'NO', 'NP',
    'NR', 'NU', 'NZ', 'OM', 'PA', 'PE', 'PF', 'PG', 'PH', 'PK', 'PL', 'PM', 'PN', 'PR',
    'PS', 'PT', 'PW', 'PY', 'QA', 'RE', 'RO', 'RS', 'RU', 'RW', 'SA', 'SB', 'SC', 'SD',
    'SE', 'SG', 'SH', 'SI', 'SJ', 'SK', 'SL', 'SM', 'SN', 'SO', 'SR', 'SS', 'ST', 'SV',
    'SX', 'SY', 'SZ', 'TC', 'TD', 'TF', 'TG', 'TH', 'TJ', 'TK', 'TL', 'TM', 'TN', 'TO',
    'TR', 'TT', 'TV', 'TW', 'TZ', 'UA', 'UG', 'UM', 'US', 'UY', 'UZ', 'VA', 'VC', 'VE',
    'VG', 'VI', 'VN', 'VU', 'WF', 'WS', 'YE', 'YT', 'ZA', 'ZM', 'ZW',
})

AE = frozenset({
    'AE', 'SA', 'KW', 'QA', 'BH', 'OM', 'JO', 'LB', 'IQ', 'PS', 'YE', 'TR', 'EG', 'MA', 'ZA',
})
BANNED = frozenset({
    # The 54 sovereign African states, less EG, MA and ZA (ae).
    'DZ', 'AO', 'BJ', 'BW', 'BF', 'BI', 'CV', 'CM', 'CF', 'TD', 'KM', 'CG', 'CD', 'CI',
    'DJ', 'GQ', 'ER', 'SZ', 'ET', 'GA', 'GM', 'GH', 'GN', 'GW', 'KE', 'LS', 'LR', 'LY',
    'MG', 'MW', 'ML', 'MR', 'MU', 'MZ', 'NA', 'NE', 'NG', 'RW', 'ST', 'SN', 'SC', 'SL',
    'SO', 'SS', 'SD', 'TZ', 'TG', 'TN', 'UG', 'ZM', 'ZW',
    # African territories (AC and TA: Saint Helena's Ascension and Tristan da Cunha).
    'EH', 'RE', 'YT', 'SH', 'AC', 'TA', 'IO',
    # Outside Africa.
    'IR', 'SY', 'KP', 'CU', 'RU', 'BY',
})


def state(code):
    if code in AE:
        return 'ae'
    if code in BANNED:
        return 'banned'
    return 'eu'


def seed(apps, schema_editor):
    Country = apps.get_model('regions', 'Country')
    codes = ISO_CODES | AE | BANNED
    Country.objects.bulk_create([Country(code=code, state=state(code)) for code in sorted(codes)])


def unseed(apps, schema_editor):
    apps.get_model('regions', 'Country').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('regions', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
