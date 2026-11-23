"""The patients tests' fixtures: the payments fixtures (doctor, fiscal entity, fake gateway,
hook recorder) over a catalogue that also sells an ICE (category `initial_case_evaluation`,
50.00 EUR) to Iberia, billed by the Spanish company, exempt."""
import copy

import pytest
from django.contrib.auth import get_user_model

from integration.tests.snapshot_rows import (ADD_ONS, EXEMPT, IBERIA, ODB, RETAINERS, SPAIN,
                                             SUBSCRIPTIONS, VIRTUA, VIRTUA_AED, add_on, eur,
                                             product, pull, rule)
from payments.tests.conftest import *  # noqa: F401,F403
from patients import service
from subscriptions.tests import helpers
from subscriptions.tests.helpers import ADVANCED, PREMIUM

ICE = 'b0000020-0000-4000-8000-000000000020'
# The TPS products of the case wizard (epic-ordering 9.3): branded and in-house in three
# sizes, the Junior add-ons being offered on `medium` only (snapshot_rows.ADD_ONS).
BRANDED = {'short': 'b0000031-0000-4000-8000-000000000031',
           'medium': 'b0000032-0000-4000-8000-000000000032',
           'long': 'b0000033-0000-4000-8000-000000000033'}
IN_HOUSE = {'short': 'b0000034-0000-4000-8000-000000000034',
            'medium': 'b0000035-0000-4000-8000-000000000035',
            'long': 'b0000036-0000-4000-8000-000000000036'}


def ice_catalogue():
    section = helpers.catalogue()
    section['products'].append(
        product(ICE, 'Initial case evaluation', 'initial_case_evaluation', '50.00',
                code='ICE-1'))
    for row in section['subscriptions'] + section.get('manual_subscriptions', []):
        row['products'] = [*row['products'], ICE]
    section['billing_rules'].append(
        rule(41, IBERIA, 'initial_case_evaluation', SPAIN, '0.00', ['redsys', 'bank_transfer'],
             exemption_text=EXEMPT))
    ids = []
    for size, amount in (('short', '80.00'), ('medium', '100.00'), ('long', '120.00')):
        section['products'] += [
            product(BRANDED[size], f'TPS branded {size}', 'tps_branded', amount,
                    code=f'TPS-BR-{size.upper()}', size=size),
            product(IN_HOUSE[size], f'TPS in-house {size}', 'tps_in_house', amount,
                    code=f'TPS-IH-{size.upper()}', size=size)]
        ids += [BRANDED[size], IN_HOUSE[size]]
    for row in section['subscriptions'] + section.get('manual_subscriptions', []):
        row['products'] = [*row['products'], *ids]
    section['billing_rules'].append(
        rule(42, IBERIA, 'tps_in_house', SPAIN, '0.00', ['redsys', 'bank_transfer'],
             exemption_text=EXEMPT))
    section['add_ons'] = copy.deepcopy(ADD_ONS)
    return copy.deepcopy(section)


@pytest.fixture
def catalogue_loaded(admin):
    assert pull(admin, 1, ice_catalogue()) == ('applied', 1)


@pytest.fixture
def other_doctor(catalogue_loaded):
    return helpers.doctor('carlos@example.com')


@pytest.fixture
def patient(doctor):
    return service.create_patient(doctor, first_name='Ana', surname='Pérez',
                                  actor=service.Actor('doctor', doctor.uid))


# The VirtuaOrtho products of the case wizard (epic-ordering 9.4): One and DigitalTwin in
# three sizes, ODB, retainers and Spark on Demand (per aligner, 4 to 9), offered to Iberia's
# Advanced and Premium only (Basic has none, as in the real catalogue).
VO_ONE = {'short': 'b0000041-0000-4000-8000-000000000041',
          'medium': 'b0000042-0000-4000-8000-000000000042',
          'long': 'b0000043-0000-4000-8000-000000000043'}
VO_TWIN = {'short': 'b0000044-0000-4000-8000-000000000044',
           'medium': 'b0000045-0000-4000-8000-000000000045',
           'long': 'b0000046-0000-4000-8000-000000000046'}
ODB_TWO = 'b0000047-0000-4000-8000-000000000047'
ODB_ONE = 'b0000048-0000-4000-8000-000000000048'
RETAINERS_3 = 'b0000049-0000-4000-8000-000000000049'
SPARK_ONE = 'b000004a-0000-4000-8000-00000000004a'
SPARK_TWO = 'b000004b-0000-4000-8000-00000000004b'
VO_ALL = [*VO_ONE.values(), *VO_TWIN.values(), ODB_TWO, ODB_ONE, RETAINERS_3, SPARK_ONE,
          SPARK_TWO]
BYTE_SYNC_VO = 'a1111111-1111-4111-8111-111111111111'
ODB_DT_ID = 'a5555555-5555-4555-8555-555555555555'
VO_CATEGORIES = ['tps_branded', 'tps_in_house', 'initial_case_evaluation', 'virtuaortho_one',
                 'virtuaortho_digitaltwin', 'spark_on_demand']
PER_ALIGNER = {'min_quantity': 4, 'max_quantity': 9}


def virtuaortho_catalogue():
    """`ice_catalogue()` with the VirtuaOrtho products: the unsized One, the ODB and the
    retainers of the shared rows leave, the sized ones are linked to Advanced and Premium."""
    section = ice_catalogue()
    legacy = {VIRTUA, VIRTUA_AED, ODB, RETAINERS}
    section['products'] = [row for row in section['products'] if row['id'] not in legacy]
    for row in section['subscriptions'] + section.get('manual_subscriptions', []):
        row['products'] = [id_ for id_ in row['products'] if id_ not in legacy]
    for size, amount in (('short', '800.00'), ('medium', '1200.00'), ('long', '1699.00')):
        section['products'] += [
            product(VO_ONE[size], f'VirtuaOrtho One {size}', 'virtuaortho_one', amount,
                    code=f'VO1-{size.upper()}', size=size),
            product(VO_TWIN[size], f'VirtuaOrtho DigitalTwin {size}', 'virtuaortho_digitaltwin',
                    amount, code=f'VODT-{size.upper()}', size=size)]
    section['products'] += [
        product(ODB_TWO, 'ODB two arches', 'odb', '559.00', code='ODB-TWO-ARCHES'),
        product(ODB_ONE, 'ODB one arch', 'odb', '445.00', code='ODB-ONE-ARCH'),
        product(RETAINERS_3, 'Retainers, set of 3', 'retainers', '180.00', code='RETAINERS-3'),
        product(SPARK_ONE, 'Spark one arch', 'spark_on_demand', '50.00',
                code='SPARK-ONE-ARCH', per_unit=dict(PER_ALIGNER)),
        product(SPARK_TWO, 'Spark two arches', 'spark_on_demand', '45.00',
                code='SPARK-TWO-ARCHES', per_unit=dict(PER_ALIGNER))]
    for row in section['subscriptions']:
        if row['id'] in (ADVANCED, PREMIUM):
            row['products'] = [*row['products'], *VO_ALL]
    rules = [row for row in section['billing_rules'] if row['category'] != 'odb']
    for number, category in ((32, 'odb'), (51, 'virtuaortho_digitaltwin'), (52, 'retainers'),
                             (53, 'spark_on_demand')):
        rules.append(rule(number, IBERIA, category, SPAIN, '0.00', ['redsys', 'bank_transfer'],
                          exemption_text=EXEMPT))
    section['billing_rules'] = rules
    section['add_ons'] = [
        add_on(BYTE_SYNC_VO, 'BYTE-SYNC', 'Byte Sync', VO_CATEGORIES, amount=eur('120.00')),
        add_on(ODB_DT_ID, 'ODB-DT', 'ODB DigitalTwin', ['odb'], amount=eur('320.00')),
        *[row for row in copy.deepcopy(ADD_ONS) if row['code'] == 'ONE-ARCH'],
        *[{**row, 'categories': [*row['categories'], 'virtuaortho_one',
                                 'virtuaortho_digitaltwin']}
          for row in copy.deepcopy(ADD_ONS) if row['code'].startswith('JUNIOR')]]
    return copy.deepcopy(section)


@pytest.fixture
def advanced(doctor, admin):
    """The doctor, holding Iberia's Advanced, with the VirtuaOrtho catalogue loaded."""
    from subscriptions import service as subscriptions
    assert pull(admin, 2, virtuaortho_catalogue()) == ('applied', 2)
    subscriptions.change(doctor, expected_version=1, actor=subscriptions.SYSTEM_ACTOR,
                         reason='test', tier='advanced', subscription_id=ADVANCED)
    return doctor
