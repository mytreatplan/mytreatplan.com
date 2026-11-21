"""The accounting feed (epic-payments entry 8, AD-18, AD-23): what the portal tells the admin
site about its invoices, payments and refunds, for the accounting copy there.

Each function builds the payload of one closed mytp-core 0.31.0 contract and records it on
the outbox (`integration.outbox.record`), so it must run inside the transaction that creates
the record: a payload the contract refuses rolls that transaction back. A record is sent once,
when it is first created (aggregate version 1); the callers do not send on an idempotent
repeat.

- `send_invoice(invoice)`: `invoice.issued` (kind `order`, `manual` or `credit_note`), the
  aggregate being the invoice's UUID.
- `send_payment(payment)`: `payment.confirmed`, the aggregate being the payment's UUID.
- `send_refund(refund)`: `refund.completed`, the aggregate being the refund's
  UUID. The staff note is never sent.

Patients appear as `patient_code` only (the key is left out when a line has none). The doctor
is referenced by `client_uid` (the order's user), left out when there is no account (a manual
sale, an order whose user is gone). No backlog: records issued before the feed existed are
not sent.
"""
from decimal import Decimal

from mytp_core.contracts import INVOICE_ISSUED, PAYMENT_CONFIRMED, REFUND_COMPLETED

from integration import outbox

from .models import Invoice

CENT = Decimal('0.01')


def _amount(value, currency):
    value = Decimal(str(value)).quantize(CENT)
    if not value:
        value = Decimal('0.00')  # never -0.00
    return {'amount': value, 'currency': currency}


def _percent(value):
    return str(Decimal(str(value)).quantize(CENT))


def _code(value):
    return getattr(value, 'code', value) or ''


def _client_uid(order):
    user = getattr(order, 'user', None) if order is not None else None
    return getattr(user, 'uid', None)


def _seller(invoice):
    return {'legal_name': invoice.seller_legal_name, 'address': invoice.seller_address,
            'country': invoice.seller_country, 'company_number': invoice.seller_company_number,
            'vat_number': invoice.seller_vat_number, 'tax_number': invoice.seller_tax_number}


def _buyer(invoice):
    return {
        'kind': invoice.bill_kind, 'first_name': invoice.bill_first_name,
        'last_name': invoice.bill_last_name, 'legal_name': invoice.bill_legal_name,
        'tax_id': invoice.bill_tax_id, 'vat_id': invoice.bill_vat_id,
        'address_line1': invoice.bill_address_line1,
        'address_line2': invoice.bill_address_line2, 'city': invoice.bill_city,
        'postal_code': invoice.bill_postal_code, 'province': invoice.bill_province,
        'country': _code(invoice.bill_country),
        'billing_email': invoice.bill_billing_email,
        'entity_id': invoice.bill_entity_id,
        'entity_version': invoice.bill_entity_version or None,
        'vies_status': invoice.bill_vies_status}


def _line(row, currency):
    line = {
        'position': row.position, 'code': row.code, 'name': row.name,
        'quantity': row.quantity, 'list_amount': _amount(row.list_amount, currency),
        'discount_percent': _percent(row.discount_percent),
        'discount_amount': _amount(row.discount_amount, currency),
        'ice': _amount(row.ice, currency),
        'voucher_share': _amount(row.voucher_share, currency),
        'net': _amount(row.net, currency), 'vat_percent': _percent(row.vat_percent),
        'vat_text': row.vat_text, 'tax_category': row.tax_category,
        'included_case': row.included_case}
    if row.patient_code:
        line['patient_code'] = row.patient_code
    return line


def invoice_payload(invoice):
    """The `invoice.issued` payload of `invoice` (a stored invoice, with its lines)."""
    currency = invoice.currency
    order = invoice.order
    payload = {
        'invoice_id': invoice.pk, 'number': invoice.number, 'kind': invoice.kind,
        'company_code': invoice.company_code, 'instance': invoice.instance,
        'issued_at': invoice.issued_at, 'issue_date': invoice.issue_date,
        'time_zone': invoice.time_zone, 'seller': _seller(invoice),
        'buyer': _buyer(invoice),
        'lines': [_line(row, currency) for row in invoice.lines.order_by('position')],
        'vat_groups': [
            {'percent': _percent(group['percent']), 'text': group.get('text', ''),
             'base': _amount(group['base'], currency), 'vat': _amount(group['vat'], currency)}
            for group in invoice.vat_groups],
        'net_total': _amount(invoice.net_total, currency),
        'discount_total': _amount(invoice.discount_total, currency),
        'ice_total': _amount(invoice.ice_total, currency),
        'voucher_taken': _amount(invoice.voucher_taken, currency),
        'vat_total': _amount(invoice.vat_total, currency),
        'total': _amount(invoice.total, currency)}
    if invoice.kind == Invoice.CREDIT_NOTE:
        payload['original_id'] = invoice.original_id
        payload['reason'] = invoice.reason
        order = invoice.original.order  # None once the order is gone
    if order is not None:
        payload['order_id'] = order.pk
        payload['order_reference'] = order.reference
        uid = _client_uid(order)
        if uid is not None:
            payload['client_uid'] = uid
    return payload


def send_invoice(invoice):
    """Record `invoice.issued` for a newly created invoice or credit note (inside its
    transaction)."""
    return outbox.record(INVOICE_ISSUED, invoice.pk, 1, invoice_payload(invoice),
                         occurred_at=invoice.issued_at)


def send_payment(payment):
    """Record `payment.confirmed` for a payment just confirmed (inside its transaction)."""
    order = payment.order
    invoice_id = (Invoice.objects.filter(order_id=order.pk).values_list('pk', flat=True)
                  .first())
    payload = {
        'payment_id': payment.pk, 'order_id': order.pk, 'order_reference': order.reference,
        'gateway': payment.gateway, 'gateway_payment_id': payment.gateway_payment_id,
        'amount': _amount(payment.amount, payment.currency),
        'confirmed_at': payment.confirmed_at}
    if invoice_id is not None:
        payload['invoice_id'] = invoice_id
    uid = _client_uid(order)
    if uid is not None:
        payload['client_uid'] = uid
    return outbox.record(PAYMENT_CONFIRMED, payment.pk, 1, payload,
                         occurred_at=payment.confirmed_at)


def send_refund(refund):
    """Record `refund.completed` for a refund just settled, its credit note issued (inside
    its transaction). The staff note is not sent."""
    note = refund.credit_note
    order = refund.order
    payload = {
        'refund_id': refund.pk, 'order_id': order.pk, 'order_reference': order.reference,
        'invoice_id': note.original_id, 'credit_note_id': note.pk,
        'amount': _amount(refund.amount, refund.currency), 'manual': refund.manual,
        'reason': refund.reason, 'gateway_refund_id': refund.gateway_refund_id,
        'completed_at': refund.completed_at}
    if refund.payment_id:
        payload['payment_id'] = refund.payment_id
    uid = _client_uid(order)
    if uid is not None:
        payload['client_uid'] = uid
    return outbox.record(REFUND_COMPLETED, refund.pk, 1, payload,
                         occurred_at=refund.completed_at)
