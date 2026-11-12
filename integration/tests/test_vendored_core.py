"""The vendored mytp-core wheel is the release this site expects (epic-catalogue entry 1;
the configuration snapshot, epic-admin-interface entry 4; the client record events,
entry 8; the snapshot's catalogue section, epic-catalogue entry 2; remote reads, entry 5;
the contact message events, entry 9; the snapshot's country map section,
epic-catalogue entry 3; remote writes, entry 6; hub lookups, entry 7; the snapshot's
provider costs section, epic-catalogue entry 4; the products' texts, code and validity and
the in-house TPS discount, epic-catalogue entry 5; add-ons, sizes, per-unit quantities,
calls and cost links, entry 6; subscriptions in full, entry 8; billing companies and
billing rules, entry 9; voucher definitions, entry 10)."""

from decimal import Decimal

import mytp_core
from mytp_core.contracts import SNAPSHOT_READER, ConfigSnapshot
from mytp_core.money import Money
from mytp_core.snapshots import sign_snapshot, verify_snapshot


def test_vendored_mytp_core_has_money():
    assert mytp_core.__version__ == '0.24.0'
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
    assert CatalogueSection.model_fields.keys() == {
        'country_groups', 'products', 'subscriptions', 'add_ons', 'manual_subscriptions',
        'billing_companies', 'billing_rules'}


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


def test_vendored_mytp_core_has_hub_lookups():
    from mytp_core.contracts import LOOKUP_READERS, MAX_LOOKUP_BYTES, SampleAvailability, errors
    from mytp_core.contracts.hub import SAMPLE_AVAILABILITY_PATH
    assert LOOKUP_READERS == {'sample_availability': SampleAvailability}
    assert SAMPLE_AVAILABILITY_PATH == '/api/v1/hub/sample-availability'
    assert MAX_LOOKUP_BYTES == 64 * 1024
    assert errors.HUB_LOOKUP_STATUS == {'invalid_request': 400, 'region_mismatch': 403,
                                        'not_found': 404}


def test_vendored_mytp_core_has_the_provider_costs_section():
    from datetime import date

    from mytp_core.contracts import ProviderCost, ProviderCostProduct, ProviderCostsSection
    assert ConfigSnapshot.model_fields['provider_costs'].is_required() is False
    assert ProviderCostsSection.model_fields.keys() == {'products'}
    assert ProviderCostProduct.model_fields.keys() == {'id', 'code', 'currency', 'active',
                                                       'costs'}
    assert ProviderCost.model_fields.keys() == {'valid_from', 'valid_to', 'face',
                                                'discounted'}
    product = ProviderCostProduct.model_validate({
        'id': '6f1c2a4e-8b3d-4c5e-9f7a-1b2c3d4e5f60', 'code': 'DM', 'currency': 'EUR',
        'active': True, 'costs': [{'valid_from': '2026-07-01', 'valid_to': None,
                                   'face': '100.00', 'discounted': '90.00'}]})
    assert product.cost_at(date(2026, 7, 1)) == Decimal('90.00')


def test_vendored_mytp_core_has_the_products_core_fields():
    from mytp_core.contracts import CatalogueProduct, CatalogueSubscription, ProductText
    assert CatalogueProduct.model_fields.keys() >= {
        'id', 'name', 'category', 'net_price', 'active', 'texts', 'code', 'valid_from',
        'valid_to'}
    assert ProductText.model_fields.keys() == {'name', 'description'}
    field = CatalogueSubscription.model_fields['tps_in_house_discount_percent']
    assert field.is_required() is False
    assert callable(CatalogueProduct.name_in) and callable(CatalogueProduct.offered_on)


def test_vendored_mytp_core_has_add_ons_quantities_calls_and_costs():
    from mytp_core.contracts import (CatalogueAddOn, CatalogueProduct, CatalogueSection,
                                     CostLink, PerUnit, ProductCalls, line_price, sum_costs)
    from mytp_core.money import Money
    assert CatalogueSection.model_fields['add_ons'].is_required() is False
    assert CatalogueProduct.model_fields.keys() == {
        'id', 'name', 'category', 'net_price', 'active', 'texts', 'code', 'valid_from',
        'valid_to', 'size', 'per_unit', 'calls', 'cost_links'}
    assert CatalogueAddOn.model_fields.keys() == {
        'id', 'code', 'texts', 'amount', 'percent', 'categories', 'sizes', 'active'}
    assert PerUnit.model_fields.keys() == {'min_quantity', 'max_quantity'}
    assert ProductCalls.model_fields.keys() == {'count', 'minutes'}
    assert CostLink.model_fields.keys() == {'provider_product', 'quantity', 'monthly'}
    assert line_price(Money('100.00', 'EUR'), percents=[Decimal('-25.00')],
                      discount_percent=Decimal('10.00')) == Money('67.50', 'EUR')
    cost = sum_costs([('A', 1, False, Money('10.00', 'EUR')), ('B', 2, False, None)], 'EUR')
    assert cost.total == Money('10.00', 'EUR') and not cost.complete
    assert cost.missing == ('B',)


def test_vendored_mytp_core_has_subscriptions_in_full():
    from mytp_core.contracts import (CallExtras, CatalogueSection, CatalogueSubscription,
                                     SubscriptionPrice, calls_under, subscription_discount,
                                     subscription_offers)
    assert CatalogueSection.model_fields['manual_subscriptions'].is_required() is False
    assert CatalogueSubscription.model_fields.keys() == {
        'id', 'tier', 'country_group', 'tps_discount_percent',
        'tps_in_house_discount_percent', 'active', 'code', 'texts', 'products',
        'discounts', 'price', 'call_extras'}
    assert SubscriptionPrice.model_fields.keys() == {'currency', 'monthly', 'upfront'}
    assert CallExtras.model_fields.keys() == {'extra_calls', 'minutes'}
    assert subscription_discount('odb', discounts={'odb': Decimal('12.00')}) == Decimal('12.00')
    assert subscription_offers('a', None) and not subscription_offers('a', [])
    assert calls_under(3, 25, 1, 35) == (4, 35)
