"""The offline tax id checks (accounts.taxid, epic-doctor-profile entry 6): the plan's matrix
rows 1-4 and 8-12 as pure functions."""
import pytest

from accounts import taxid

DNI, NIE, CIF = '12345678Z', 'X1234567L', 'B12345674'


@pytest.mark.parametrize('tax_id,kind,code', [
    ('12345678A', 'person', taxid.INVALID),         # 1: a wrong letter
    (DNI, 'person', None), (NIE, 'person', None), ('ES' + DNI, 'person', None),
    (DNI, 'company', taxid.KIND),                   # 2: a DNI for a company
    (NIE, 'company', taxid.KIND),
    (CIF, 'person', taxid.KIND),                    # 2: a CIF for a person
    (CIF, 'company', None), ('ES' + CIF, 'company', None),   # 3
    ('B12345675', 'company', taxid.INVALID),
    ('', 'company', taxid.INVALID),
    ('FR' + CIF, 'company', taxid.INVALID),
    # K, L and M person NIFs (user, 2026-11-17): valid for a person, not for a company.
    ('K1234567L', 'person', None), ('L1234567L', 'person', None),
    ('M1234567L', 'person', None), ('ESM1234567L', 'person', None),
    ('M1234567L', 'company', taxid.KIND), ('M1234567A', 'person', taxid.INVALID),
])
def test_spain(tax_id, kind, code):
    assert taxid.tax_id_problem(tax_id, 'ES', kind) == code


@pytest.mark.parametrize('tax_id,code', [
    ('123456789', None), ('PT123456789', None), ('123456788', taxid.INVALID),   # 4
    ('12345678', taxid.INVALID), ('ES' + CIF, taxid.PREFIX), ('ES123456789', taxid.INVALID)])
def test_portugal(tax_id, code):
    assert taxid.tax_id_problem(tax_id, 'PT', 'person') == code


def test_other_countries_are_free_text_but_a_prefix_of_another_country_is_refused():
    assert taxid.tax_id_problem('anything-goes', 'DE', 'company') is None
    assert taxid.tax_id_problem('GB123', 'GB', 'company') is None
    assert taxid.tax_id_problem('ES123', 'AE', 'company') is None
    assert taxid.tax_id_problem('ESB12345674', 'FR', 'company') == taxid.PREFIX   # 10
    assert taxid.tax_id_problem('X1', 'US', 'person') is None


def test_greece_takes_el_or_gr_or_nothing():
    for tax_id in ('EL094259216', 'GR094259216', '094259216'):
        assert taxid.tax_id_problem(tax_id, 'GR', 'company') is None
        plan = taxid.vies_plan(tax_id, 'GR')
        assert plan == (taxid.CHECK, 'EL', '094259216')                              # 9
    assert taxid.tax_id_problem('DE136695976', 'GR', 'company') == taxid.PREFIX


@pytest.mark.parametrize('vat_id,code', [
    ('100200300400031', None), ('', None), ('10012345678901', taxid.VAT_INVALID),   # 12
    ('1001234567890A1', taxid.VAT_INVALID), ('1001234567890123', taxid.VAT_INVALID),
    ('١٠٠٢٠٠٣٠٠٤٠٠٠٣١', taxid.VAT_INVALID)])
def test_ae_vat_id(vat_id, code):
    assert taxid.vat_id_problem(vat_id, 'AE') == code


def test_a_vat_id_elsewhere_is_not_checked():
    assert taxid.vat_id_problem('12', 'ES') is None


def test_vies_plan():
    assert taxid.vies_plan('DE136695976', 'DE') == (taxid.CHECK, 'DE', '136695976')
    assert taxid.vies_plan('136695976', 'DE') == (taxid.CHECK, 'DE', '136695976')
    assert taxid.vies_plan('ES' + CIF, 'ES') == (taxid.CHECK, 'ES', CIF)             # 3
    assert taxid.vies_plan('123', 'DE').action == taxid.BAD                          # 8
    assert taxid.vies_plan('ES' + CIF, 'FR').action == taxid.BAD
    assert taxid.vies_plan('', 'DE').action == taxid.BAD
    for country in ('GB', 'CH', 'NO', 'AE', 'US', ''):                               # 11
        assert taxid.vies_plan('DE136695976', country).action == taxid.NOT_APPLICABLE


def test_the_eu_list_is_the_27_members_without_the_united_kingdom():
    assert len(taxid.EU_COUNTRIES) == 27
    assert 'GB' not in taxid.EU_COUNTRIES and 'GR' in taxid.EU_COUNTRIES
    assert taxid.vies_prefix('gr') == 'EL' and taxid.vies_prefix('DE') == 'DE'


@pytest.mark.parametrize('tax_id', ['PLLMRA85T10A562S', 'NLSMRA80A01H501U'])
def test_an_italian_codice_fiscale_starting_with_a_country_code_is_free_text(tax_id):
    assert taxid.tax_id_problem(tax_id, 'IT', 'person') is None
    assert taxid.vies_plan(tax_id, 'IT').action == taxid.BAD
