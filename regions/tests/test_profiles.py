"""Epic-platform entry 6: the region profiles (AD-13) and their lookup."""
import dataclasses

import pytest

from regions.profiles import DEFAULT_REGION, PROFILES, UnknownRegion, get_profile


def test_eu_profile():
    p = get_profile('eu')

    assert p.code == 'eu'
    assert p.domains == ('mytreatplan.com', 'www.mytreatplan.com')
    assert p.company.name == 'MyTPDSO Limited'
    assert p.company.registration == 'Company number 815526 (Ireland)'
    assert p.time_zone == 'Europe/Madrid'
    assert p.languages == ('en', 'es')
    assert p.language_choices == [('en', 'English'), ('es', 'Español')]
    assert p.currencies == ('EUR', 'USD')
    assert p.storage_endpoint == 'https://s3.gra.io.cloud.ovh.net'
    assert p.gateways == ('redsys', 'revolut', 'bank_transfer')
    assert p.patient_code_prefix == 'EU' and p.invoice_prefix == 'EU'
    assert p.site_url == 'https://mytreatplan.com'
    assert p.origins == ('https://mytreatplan.com', 'https://www.mytreatplan.com')


def test_ae_profile():
    p = get_profile('ae')

    assert p.code == 'ae'
    assert p.domains == ('mytreatplan.ae', 'www.mytreatplan.ae')
    assert p.company.name == 'MyTPDSO L.L.C-FZ'
    assert p.company.registration == 'License 2542361.01 (Dubai)'
    assert p.time_zone == 'Asia/Dubai'
    assert p.language_choices == [('en', 'English')]
    assert p.currencies == ('AED', 'USD')
    assert p.storage_endpoint == ''
    assert p.gateways == ()
    assert p.patient_code_prefix == 'AE' and p.invoice_prefix == 'AE'
    assert p.site_url == 'https://mytreatplan.ae'


def test_default_region_is_eu():
    assert DEFAULT_REGION in PROFILES and get_profile(DEFAULT_REGION).code == 'eu'


@pytest.mark.parametrize('code', ['us', '', None, 'EU', ' eu'])
def test_unknown_region_names_the_valid_ones(code):
    with pytest.raises(UnknownRegion, match=r"REGION must be one of ae, eu, got"):
        get_profile(code)


def test_profiles_are_immutable_and_keyed_by_code():
    for code, profile in PROFILES.items():
        assert profile.code == code
        with pytest.raises(dataclasses.FrozenInstanceError):
            profile.time_zone = 'UTC'


def test_profiles_hold_no_secrets_or_env_only_values():
    names = {f.name for f in dataclasses.fields(next(iter(PROFILES.values())))}
    assert not names & {'storage_bucket', 'kms_endpoint'}
    assert not {n for n in names if any(w in n for w in ('key', 'secret', 'password', 'token'))}


def test_profiles_module_has_no_django_imports():
    """settings.py imports it before the apps load."""
    import ast
    import regions.profiles as module

    tree = ast.parse(open(module.__file__, encoding='utf-8').read())
    imported = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names]
    imported += [node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [name for name in imported if name.startswith('django')]
