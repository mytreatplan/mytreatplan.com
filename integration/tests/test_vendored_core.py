"""The vendored mytp-core wheel is the release this site expects (epic-catalogue entry 1;
the configuration snapshot, epic-admin-interface entry 4; the client record events,
entry 8; the snapshot's catalogue section, epic-catalogue entry 2)."""

from decimal import Decimal

import mytp_core
from mytp_core.contracts import SNAPSHOT_READER, ConfigSnapshot
from mytp_core.money import Money
from mytp_core.snapshots import sign_snapshot, verify_snapshot


def test_vendored_mytp_core_has_money():
    assert mytp_core.__version__ == '0.12.0'
    assert Money('10.005', 'EUR') == Money(Decimal('10.01'), 'EUR')
    assert Money('10', 'EUR') != Money('10', 'AED')


def test_vendored_mytp_core_has_the_config_snapshot():
    assert SNAPSHOT_READER == 1
    assert ConfigSnapshot.model_fields.keys() >= {'version', 'min_reader', 'generated_at',
                                                  'admin_directory'}
    assert callable(sign_snapshot) and callable(verify_snapshot)


def test_vendored_mytp_core_has_the_client_events_and_no_sample():
    from mytp_core import contracts
    assert contracts.registered_types() == ['client.erased', 'client.updated']
    assert not hasattr(contracts, 'SAMPLE_RECORDED')


def test_vendored_mytp_core_has_the_catalogue_section():
    from mytp_core.contracts import CatalogueSection
    assert ConfigSnapshot.model_fields['catalogue'].is_required() is False
    assert CatalogueSection.model_fields.keys() == {'country_groups', 'products',
                                                    'subscriptions'}
