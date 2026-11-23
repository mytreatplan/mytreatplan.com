"""The patients tests' fixtures: the payments fixtures (doctor, fiscal entity, fake gateway,
hook recorder) over a catalogue that also sells an ICE (category `initial_case_evaluation`,
50.00 EUR) to Iberia, billed by the Spanish company, exempt."""
import copy

import pytest
from django.contrib.auth import get_user_model

from integration.tests.snapshot_rows import (ADD_ONS, EXEMPT, IBERIA, SPAIN, SUBSCRIPTIONS, product,
                                             pull, rule)
from payments.tests.conftest import *  # noqa: F401,F403
from patients import service
from subscriptions.tests import helpers

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
