"""The vendored mytp-core wheel is the release this site expects (epic-catalogue entry 1;
the configuration snapshot, epic-admin-interface entry 4; the client record events,
entry 8; the snapshot's catalogue section, epic-catalogue entry 2; remote reads, entry 5;
the contact message events, entry 9; the snapshot's country map section,
epic-catalogue entry 3; remote writes, entry 6)."""

from decimal import Decimal

import mytp_core
from mytp_core.contracts import SNAPSHOT_READER, ConfigSnapshot
from mytp_core.money import Money
from mytp_core.snapshots import sign_snapshot, verify_snapshot


def test_vendored_mytp_core_has_money():
    assert mytp_core.__version__ == '0.16.0'
    assert Money('10.005', 'EUR') == Money(Decimal('10.01'), 'EUR')
    assert Money('10', 'EUR') != Money('10', 'AED')


def test_vendored_mytp_core_has_the_config_snapshot():
    assert SNAPSHOT_READER == 1
    assert ConfigSnapshot.model_fields.keys() >= {'version', 'min_reader', 'generated_at',
                                                  'admin_directory'}
    assert callable(sign_snapshot) and callable(verify_snapshot)


def test_vendored_mytp_core_has_the_client_events_and_no_sample():
    from mytp_core import contracts
    assert contracts.registered_types() == ['client.erased', 'client.updated',
                                            'contact_message.deleted',
                                            'contact_message.updated']
    assert not hasattr(contracts, 'SAMPLE_RECORDED')


def test_vendored_mytp_core_has_the_catalogue_section():
    from mytp_core.contracts import CatalogueSection
    assert ConfigSnapshot.model_fields['catalogue'].is_required() is False
    assert CatalogueSection.model_fields.keys() == {'country_groups', 'products',
                                                    'subscriptions'}


def test_vendored_mytp_core_has_remote_reads():
    from mytp_core import access, interface
    from mytp_core.contracts import errors, remote_record
    assert access.rule_for('sample_record', 'clinical_manager').fields == {
        'uuid', 'patient_name', 'assignee_uuid', 'note'}
    assert callable(interface.remote_actor) and callable(remote_record)
    assert errors.remote_read_error('access_log_unavailable')[1] == 503


def test_vendored_mytp_core_has_the_contact_message_events():
    from mytp_core import contracts
    assert contracts.CONTACT_MESSAGE_READERS == {
        'contact_message.updated': contracts.ContactMessageUpdatedReader,
        'contact_message.deleted': contracts.ContactMessageDeletedReader}
    assert contracts.payload_model_for('contact_message.updated') is (
        contracts.ContactMessageUpdated)


def test_vendored_mytp_core_has_the_country_map_section():
    from mytp_core.contracts import CountryMapEntry, CountryMapSection
    assert ConfigSnapshot.model_fields['country_map'].is_required() is False
    assert CountryMapSection.model_fields.keys() == {'countries'}
    assert CountryMapEntry.model_fields.keys() == {'code', 'state'}


def test_vendored_mytp_core_has_remote_writes():
    from mytp_core import access
    from mytp_core.contracts import RESOURCE_WRITERS, errors, stale_version_body
    assert access.writable('sample_record', 'clinical_manager') == {'note'}
    assert set(RESOURCE_WRITERS) == {'sample_record'}
    assert errors.REMOTE_WRITE_STATUS['stale_version'] == 409
    assert errors.REMOTE_WRITE_STATUS['idempotency_key_reused'] == 422
    assert stale_version_body(2)['current_version'] == 2
