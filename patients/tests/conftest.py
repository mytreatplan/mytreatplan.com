"""The patients tests' fixtures: the payments fixtures (doctor, fiscal entity, fake gateway,
hook recorder) over a catalogue that also sells an ICE (category `initial_case_evaluation`,
50.00 EUR) to Iberia, billed by the Spanish company, exempt."""
import copy

import pytest
from django.contrib.auth import get_user_model

from integration.tests.snapshot_rows import EXEMPT, IBERIA, SPAIN, SUBSCRIPTIONS, product, pull, rule
from payments.tests.conftest import *  # noqa: F401,F403
from patients import service
from subscriptions.tests import helpers

ICE = 'b0000020-0000-4000-8000-000000000020'


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
