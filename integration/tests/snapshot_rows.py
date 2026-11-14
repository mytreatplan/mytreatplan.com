"""Test-only: the configuration snapshot as the tests send it, and rows of a snapshot-fed
table written directly (epic-catalogue entries 11 and 15).

The admin site is a fake (`FakeAdmin`, installed by the root conftest's `admin` fixture)
at mytp_core.interface._open that checks the portal's signature and answers `snapshot()`
signed by the admin's key (fixed, non-secret test seeds; no network, no KMS); `pull`
makes the portal pull it. The catalogue, provider-cost and voucher sections below are the
ones several apps' tests send (billing's full catalogue, the add-ons, the provider costs,
the general voucher).

The snapshot-fed tables (integration.snapshot_tables) refuse every ORM write outside the
snapshot apply. A test that needs rows writes them through a pull or, when that is out of
reach (a browser test's map row, a country banned between two steps, a release before
this one simulated), with `snapshot_writes`, `put_country` and `ensure_country`, which
mark the apply as running for their block only.

Never imported by production code: it lives in a `tests` package, and
integration/tests/test_snapshot_tables.py fails when a module outside the `tests`
packages imports it or the marker itself (only integration.config may).
"""
import copy
import io
import json
import threading
import urllib.error
from urllib.parse import urlsplit

from integration.snapshot_tables import _snapshot_apply
from mytp_core import interface, snapshots

# --- The fake admin site and the snapshot ---------------------------------------------

ADMIN_SEED = b'test-only-interface-seed-admin!!'
PORTAL_SEED = b'test-only-interface-seed-portal!'
ADMIN = interface.SigningKey('a1', ADMIN_SEED)
PORTAL = interface.SigningKey('p1', PORTAL_SEED)
CONFIG_URL = 'https://admindev.example.test/api/v1/config'
ADA = '0b6f3d1e-5a7c-4f2e-9b1a-2c3d4e5f6a7b'
BOB = '8f14e45f-ceea-4e7a-9f1d-5b6c7d8e9f01'


def snapshot(version=1, min_reader=1, **changes):
    data = {
        'version': version, 'min_reader': min_reader,
        'generated_at': '2026-11-05T10:00:00Z',
        'admin_directory': [
            {'id': ADA, 'display_name': 'Ada Admin', 'title': 'Sysadmin', 'active': True,
             'time_zone': 'Europe/Madrid'},
            {'id': BOB, 'display_name': 'Bob Gone', 'title': '', 'active': False,
             'time_zone': 'Asia/Dubai'},
        ],
    }
    data.update(changes)
    return data


class FakeAdmin:
    """The admin's GET /api/v1/config: verifies the portal's signature, then answers the
    current `snapshot` signed for dev by the admin's key. `down` makes it unreachable,
    `status` answers that HTTP error, `tamper(answer)` changes the answer after signing,
    `key` signs with another key."""

    def __init__(self):
        self.ring = interface.Keyring.from_entries([('dev', 'p1', PORTAL.public_text)])
        self.snapshot = snapshot(version=1)
        self.down = False
        self.status = None
        self.tamper = None
        self.key = ADMIN
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, request, timeout):
        with self.lock:
            self.calls += 1
        if self.down:
            raise urllib.error.URLError(ConnectionRefusedError(61, 'refused'))
        parts = urlsplit(request.full_url)
        assert request.full_url == CONFIG_URL and request.method == 'GET'
        claims = interface.verify_request(
            request.get_header('Authorization'), 'GET',
            interface.request_target(parts.path, parts.query), b'',
            keyring=self.ring, audience='admindev')
        if self.status:
            raise urllib.error.HTTPError(request.full_url, self.status, 'x', {},
                                         io.BytesIO(b'<html>bad gateway</html>'))
        answer = snapshots.sign_snapshot(copy.deepcopy(self.snapshot),
                                         audience=claims['iss'], issuer='admindev',
                                         signing_key=self.key)
        if self.tamper:
            self.tamper(answer)
        response = io.BytesIO(json.dumps(answer).encode())
        response.status = 200
        return response


def state():
    from integration.models import ConfigState
    return ConfigState.objects.get()


def pull(admin, version, section=None, *, key='catalogue', **changes):
    """The fake admin site answers snapshot `version` (with `changes`), its `key` section
    set to `section` (left as `changes` has it when None), and the portal pulls it."""
    from integration import config
    admin.snapshot = snapshot(version=version, **changes)
    if section is not None:
        admin.snapshot[key] = section
    return config.pull()


# --- Rows written directly --------------------------------------------------------------

def snapshot_writes():
    """A context manager: the block writes as the snapshot apply does."""
    return _snapshot_apply()


def put_country(code, state):
    """The country map's row of `code` with `state` (created or changed)."""
    from regions.models import Country
    with snapshot_writes():
        return Country.objects.update_or_create(code=code, defaults={'state': state})[0]


def ensure_country(code, state):
    """The country map's row of `code`, created with `state` when it is missing."""
    from regions.models import Country
    with snapshot_writes():
        return Country.objects.get_or_create(code=code, defaults={'state': state})[0]


# --- Catalogue section builders (epic-catalogue entries 2, 5 and 8) ---------------------

IBERIA = '11111111-1111-4111-8111-111111111111'
WORLD = '22222222-2222-4222-8222-222222222222'


def group(id_, name, countries, catch_all=False, active=True):
    return {'id': id_, 'name': name, 'countries': countries, 'catch_all': catch_all,
            'active': active}


def product(id_, name, category, amount, currency='EUR', active=True, **more):
    """A product as the admin site sends it; `more`: the optional fields of mytp-core
    0.20.0 (texts, code, valid_from, valid_to)."""
    return {'id': id_, 'name': name, 'category': category,
            'net_price': {'amount': amount, 'currency': currency}, 'active': active, **more}


def full_subscription(id_, tier, group_id, code, name, products, discounts=None,
                      active=True, **more):
    """A subscription as an admin site on mytp-core 0.22.0 sends it: the two former
    discount fields equal the map's `tps_branded` and `tps_in_house` values."""
    discounts = dict(discounts or {})
    return {'id': id_, 'tier': tier, 'country_group': group_id,
            'tps_discount_percent': discounts.get('tps_branded', '0.00'),
            'tps_in_house_discount_percent': discounts.get('tps_in_house', '0.00'),
            'active': active, 'code': code,
            'texts': {'en': {'name': name, 'description': ''}},
            'products': list(products), 'discounts': discounts, **more}


# --- Billing's catalogue (epic-catalogue entry 9) ---------------------------------------

EU = 'a1111111-1111-4111-8111-111111111111'
UAE = 'a2222222-2222-4222-8222-222222222222'
TPS = 'b0000001-0000-4000-8000-000000000001'
TPS_USD = 'b0000002-0000-4000-8000-000000000002'
TPS_AED = 'b0000003-0000-4000-8000-000000000003'
VIRTUA = 'b0000004-0000-4000-8000-000000000004'
VIRTUA_AED = 'b0000005-0000-4000-8000-000000000005'
RETAINERS = 'b0000006-0000-4000-8000-000000000006'
ODB = 'b0000007-0000-4000-8000-000000000007'
SPAIN = 'c0000001-0000-4000-8000-000000000001'
IRELAND = 'c0000002-0000-4000-8000-000000000002'
EMIRATES = 'c0000003-0000-4000-8000-000000000003'
SUBSCRIPTIONS = {IBERIA: 'e0000001-0000-4000-8000-000000000001',
                 EU: 'e0000002-0000-4000-8000-000000000002',
                 WORLD: 'e0000003-0000-4000-8000-000000000003',
                 UAE: 'e0000004-0000-4000-8000-000000000004'}
MANUAL = 'e0000005-0000-4000-8000-000000000005'

EXEMPT = 'Exempt from VAT (art. 20 LIVA).'
REVERSE = 'Reverse charge (art. 196 Directive 2006/112/EC).'
EXPORT = 'Export of services: outside the scope of EU VAT.'
CATEGORIES = ('tps_branded', 'virtuaortho_one')

GROUPS = [group(IBERIA, 'Iberia', ['ES', 'PT']), group(EU, 'Rest of EU', ['IE', 'DE']),
          group(UAE, 'UAE', ['AE']), group(WORLD, 'Rest of world', [], catch_all=True)]
PRODUCTS = [
    product(TPS, 'TPS branded', 'tps_branded', '100.00', code='TPS-BR'),
    product(TPS_USD, 'TPS branded USD', 'tps_branded', '110.00', 'USD', code='TPS-BR-USD'),
    product(TPS_AED, 'TPS branded AED', 'tps_branded', '400.00', 'AED', code='TPS-BR-AED'),
    product(VIRTUA, 'VirtuaOrtho One', 'virtuaortho_one', '250.00', code='VO-ONE'),
    product(VIRTUA_AED, 'VirtuaOrtho One AED', 'virtuaortho_one', '1000.00', 'AED',
            code='VO-ONE-AED'),
    product(RETAINERS, 'Retainers', 'retainers', '50.00', code='RET-1'),
    product(ODB, 'ODB', 'odb', '80.00', code='ODB-1'),
]
ALL = [TPS, TPS_USD, TPS_AED, VIRTUA, VIRTUA_AED, RETAINERS, ODB]
COMPANIES = [
    {'id': SPAIN, 'code': 'ES', 'legal_name': 'MyTPDSO Spain S.L.',
     'address': 'Avda. Labradores, 1, Edificio COS, Planta 4, Oficina 6\n'
                'TRES CANTOS 28760 (MADRID)',
     'country': 'ES', 'time_zone': 'Europe/Madrid', 'vat_number': 'B93858371',
     'tax_number': 'B93858371', 'active': True},
    {'id': IRELAND, 'code': 'IE', 'legal_name': 'MyTPDSO Limited', 'address': 'Dublin',
     'country': 'IE', 'time_zone': 'Europe/Dublin',
     'company_number': 'Company number 815526 (Ireland)', 'active': True},
    {'id': EMIRATES, 'code': 'AE', 'legal_name': 'MyTPDSO L.L.C-FZ', 'address': 'Dubai',
     'country': 'AE', 'time_zone': 'Asia/Dubai',
     'company_number': 'License 2542361.01 (Dubai)', 'vat_number': 'VAT-100',
     'tax_number': 'TAX-100', 'active': True},
]


def rule(number, group_id, category, company, percent, gateways, currency='EUR',
         available=True, active=True, **texts):
    return {'id': f'd00000{number:02d}-0000-4000-8000-000000000001',
            'country_group': group_id, 'category': category, 'company': company,
            'vat_percent': percent, 'gateways': list(gateways), 'currency': currency,
            'available': available, 'active': active, **texts}


def rules():
    """The plan's rules, on the TPS branded and VirtuaOrtho One categories: Iberia → the
    Spanish company, exempt, Redsys then bank transfer; Rest of EU → the Irish company,
    23 % with a reverse-charge text, Revolut; Rest of world → the Irish company, rate 0
    with its export text; UAE → the UAE company, 5 %, no gateway, AED. And Iberia's
    `subscription` rule (21 %) and an unavailable ODB rule. Retainers have no rule."""
    found = []
    for index, category in enumerate(CATEGORIES):
        base = index * 10
        found += [
            rule(base + 1, IBERIA, category, SPAIN, '0.00', ['redsys', 'bank_transfer'],
                 exemption_text=EXEMPT),
            rule(base + 2, EU, category, IRELAND, '23.00', ['revolut'],
                 reverse_charge_text=REVERSE),
            rule(base + 3, WORLD, category, IRELAND, '0.00', ['revolut'],
                 exemption_text=EXPORT),
            rule(base + 4, UAE, category, EMIRATES, '5.00', [], 'AED'),
        ]
    return found + [
        rule(31, IBERIA, 'subscription', SPAIN, '21.00', ['redsys']),
        rule(32, IBERIA, 'odb', SPAIN, '21.00', ['redsys'], available=False),
    ]


def billed(rules_=None, companies=COMPANIES, products=PRODUCTS, manual=True):
    """A catalogue with a basic subscription per country group, each linked to every
    product, the companies and the rules (`rules_` None: the plan's; `companies` None and
    `rules_` False: none of either, as an admin site before mytp-core 0.23.0)."""
    section = {
        'country_groups': copy.deepcopy(GROUPS), 'products': copy.deepcopy(products),
        'subscriptions': [
            full_subscription(id_, 'basic', group_id, f'BAS-{index}', 'Basic', ALL,
                              **({'price': {'currency': 'EUR', 'monthly': '60.00'}}
                                 if group_id in (IBERIA, EU) else {}))
            for index, (group_id, id_) in enumerate(SUBSCRIPTIONS.items())],
    }
    if manual:
        section['manual_subscriptions'] = [full_subscription(
            MANUAL, 'premium', IBERIA, 'ORMCO', 'Ormco', [TPS, TPS_USD, RETAINERS],
            {'tps_branded': '20.00'})]
    if companies is not None:
        section['billing_companies'] = copy.deepcopy(companies)
    if rules_ is not False:
        section['billing_rules'] = copy.deepcopy(rules() if rules_ is None else rules_)
    return section


# --- Add-ons and provider costs (epic-catalogue entry 6) --------------------------------

BYTE_SYNC = 'a1111111-1111-4111-8111-111111111111'
ONE_ARCH = 'a2222222-2222-4222-8222-222222222222'
JUNIOR_1 = 'a3333333-3333-4333-8333-333333333333'
JUNIOR_2 = 'a4444444-4444-4444-8444-444444444444'
TWIN = 'a5555555-5555-4555-8555-555555555555'
SCAN = 'c1111111-1111-4111-8111-111111111111'
PRINT = 'c2222222-2222-4222-8222-222222222222'
DM = 'c3333333-3333-4333-8333-333333333333'
USD_SCAN = 'c4444444-4444-4444-8444-444444444444'
CASE_CATEGORIES = ['tps_branded', 'tps_in_house', 'initial_case_evaluation',
                   'virtuaortho_one', 'virtuaortho_digitaltwin', 'odb']


def add_on(id_, code, name, categories, active=True, **more):
    return {'id': id_, 'code': code, 'texts': {'en': {'name': name, 'description': ''}},
            'categories': categories, 'active': active, **more}


def eur(amount):
    return {'amount': amount, 'currency': 'EUR'}


ADD_ONS = [
    add_on(BYTE_SYNC, 'BYTE-SYNC', 'Byte Sync', CASE_CATEGORIES, amount=eur('120.00')),
    add_on(TWIN, 'ODB-TWIN', 'ODB DigitalTwin', ['odb'], amount=eur('320.00')),
    add_on(ONE_ARCH, 'ONE-ARCH', 'One arch', ['tps_in_house'], percent='-25.00'),
    add_on(JUNIOR_1, 'JUNIOR-1', 'Junior I', ['tps_branded', 'tps_in_house'],
           amount=eur('0.00'), sizes=['medium']),
    add_on(JUNIOR_2, 'JUNIOR-2', 'Junior II', ['tps_branded', 'tps_in_house'],
           amount=eur('0.00'), sizes=['medium']),
]


def cost(valid_from, amount, valid_to=None, face=None):
    return {'valid_from': valid_from, 'valid_to': valid_to, 'face': face or amount,
            'discounted': amount}


def provider_product(id_, code, costs, currency='EUR', active=True):
    return {'id': id_, 'code': code, 'currency': currency, 'active': active, 'costs': costs}


COSTS = {'products': [
    provider_product(SCAN, 'SCAN', [cost('2026-01-01', '10.00')]),
    # 8.00 until June, then discounted to 7.50; ended on 31 October.
    provider_product(PRINT, 'PRINT', [cost('2026-01-01', '8.00', '2026-06-30'),
                                      cost('2026-07-01', '7.50', '2026-10-31', '8.00')]),
    provider_product(DM, 'DM', [cost('2026-01-01', '5.00')]),
    provider_product(USD_SCAN, 'USD-SCAN', [cost('2026-01-01', '11.00')], currency='USD'),
]}


# --- Vouchers (epic-catalogue entry 10) -------------------------------------------------

TRY_ME = 'f0000001-0000-4000-8000-000000000001'


def voucher(id_, code, type_='try_me', region='eu', amount='50.00', currency='EUR',
            client=None, valid_from='2026-11-12', valid_to=None, active=True):
    return {'id': id_, 'code': code, 'type': type_, 'region': region,
            'amount': {'amount': amount, 'currency': currency}, 'client': client,
            'valid_from': valid_from, 'valid_to': valid_to, 'active': active}


GENERAL = voucher(TRY_ME, 'K7QM2XH9TR')
