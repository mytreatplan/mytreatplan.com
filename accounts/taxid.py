"""Offline checks of a fiscal entity's tax id and VAT id (epic-doctor-profile entry 6), pure
functions on python-stdnum. No network, no logging: a value never leaves a function except
as its return value.

- Spain: a person gives a DNI-NIF, an NIE or a K, L or M NIF; a company a CIF (checksum
  and kind);
- Portugal: the NIF/NIPC mod-11 checksum;
- the UAE: the VAT id is exactly 15 digits;
- any other EU country: free text, never refused here; `vies_plan` says whether it is a
  well-formed VAT number for its country (stdnum `eu.vat`) and so worth asking VIES.

The id may be typed with or without its country prefix (`EL` for Greece, `GR` is accepted
as the same country). The checks run on the national part. Another EU country's prefix is refused only when the id is a well-formed
VAT number of that country; any other id is read as typed.
"""
import re
from collections import namedtuple

from stdnum.eu import vat as eu_vat
from stdnum.es import cif as es_cif, dni as es_dni, nie as es_nie, nif as es_nif
from stdnum.exceptions import ValidationError as StdnumError
from stdnum.pt import nif as pt_nif

EU_COUNTRIES = frozenset({
    'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 'GR', 'HU', 'IE', 'IT',
    'LV', 'LT', 'LU', 'MT', 'NL', 'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE'})
# The VAT prefix of a country when it is not its ISO code.
_VAT_PREFIX = {'GR': 'EL'}
# The prefixes that name a country: the EU-27's (Greece's both ways).
_PREFIXES = {(_VAT_PREFIX.get(code, code)) for code in EU_COUNTRIES} | {'GR'}

VAT_ID_DIGITS = re.compile(r'[0-9]{15}')

# Spanish person NIFs that are neither a DNI nor an NIE: K (minors under 14), L (Spaniards
# living abroad), M (foreigners without an NIE) (user, 2026-11-17).
PERSON_NIF_LETTERS = frozenset('KLM')

# Offline problem codes (the keys of fiscal.problems' values).
INVALID, KIND, PREFIX = 'tax_id_invalid', 'tax_id_kind', 'tax_id_prefix'
VAT_INVALID = 'vat_id_invalid'

# What `vies_plan` decides: NOT_APPLICABLE (no check), INVALID (not well-formed, no call) or
# CHECK (ask VIES with `country` and `number`).
NOT_APPLICABLE, BAD, CHECK = 'not_applicable', 'invalid', 'check'
Plan = namedtuple('Plan', 'action country number')


def is_eu(country):
    return (country or '').upper() in EU_COUNTRIES


def vies_prefix(country):
    """The code VIES and VAT numbers use for the EU billing country (Greece is EL)."""
    country = (country or '').upper()
    return _VAT_PREFIX.get(country, country)


def split_prefix(tax_id, country):
    """(national part, prefix problem) of `tax_id` (normalised: uppercase, no spaces) for
    the EU billing `country`. A leading country code of this country (Greece: EL or GR) is
    dropped; one of another EU country is a prefix problem (PREFIX). A country outside the
    EU keeps the id as typed."""
    value = tax_id or ''
    country = (country or '').upper()
    if country not in EU_COUNTRIES:
        return value, None
    head = value[:2]
    if head not in _PREFIXES or not value[2:3]:
        return value, None
    own = {vies_prefix(country)} | ({'GR'} if country == 'GR' else set())
    if head in own:
        return value[2:], None
    # Another country's code is a prefix problem only when the id, read with it, is a
    # well-formed VAT number of that country; else it is free text (an Italian codice
    # fiscale may start with PL or NL).
    try:
        foreign = bool(eu_vat.is_valid(value))
    except StdnumError:
        foreign = False
    return (value, PREFIX) if foreign else (value, None)


def _valid(module, number):
    try:
        return bool(module.is_valid(number))
    except StdnumError:
        return False


def tax_id_problem(tax_id, country, kind):
    """The offline problem code of `tax_id` for billing `country` and entity `kind`
    ('person' or 'company'), or None. Only ES, PT and a foreign prefix are refused."""
    country = (country or '').upper()
    national, bad_prefix = split_prefix(tax_id, country)
    if bad_prefix:
        return bad_prefix
    if country == 'ES':
        person = (_valid(es_dni, national) or _valid(es_nie, national)
                  or (national[:1] in PERSON_NIF_LETTERS and _valid(es_nif, national)))
        company = _valid(es_cif, national)
        if not person and not company:
            return INVALID
        if kind == 'company' and not company:
            return KIND
        if kind == 'person' and not person:
            return KIND
        return None
    if country == 'PT':
        return None if _valid(pt_nif, national) else INVALID
    return None


def vat_id_problem(vat_id, country):
    """VAT_INVALID when an AE VAT id is given and is not exactly 15 digits, else None."""
    if (country or '').upper() == 'AE' and vat_id and not VAT_ID_DIGITS.fullmatch(vat_id):
        return VAT_INVALID
    return None


def vies_plan(tax_id, country):
    """Whether and how to ask VIES about `tax_id` for billing `country`: NOT_APPLICABLE
    outside the EU-27, BAD when it is not a well-formed VAT number for the country (or
    carries another country's prefix), else CHECK with VIES's country code and the national
    number."""
    country = (country or '').upper()
    if country not in EU_COUNTRIES:
        return Plan(NOT_APPLICABLE, '', '')
    national, bad_prefix = split_prefix(tax_id, country)
    code = vies_prefix(country)
    if bad_prefix or not national:
        return Plan(BAD, code, '')
    try:
        full = eu_vat.compact(code + national)
        well_formed = full.startswith(code) and eu_vat.is_valid(full)
    except StdnumError:
        return Plan(BAD, code, '')
    if not well_formed:
        return Plan(BAD, code, '')
    return Plan(CHECK, code, full[len(code):])
