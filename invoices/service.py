"""Issuing invoices (epic-payments entry 6, AD-7). The one place that numbers an invoice and
snapshots its seller and buyer.

`issue_for_order` is the receiver of the order's `paid` hook: it runs inside the payment's
transaction, so an invoice that cannot be issued (`InvoiceError`, which the hook turns into
`payments.service.HookFailed`) rolls the payment back. `issue_manual` invoices a sale that
has no order. Both take the number from a locked per-(company, instance, year) counter,
inside the transaction that creates the invoice: the number and the row commit or roll back
together, so the series has no gap.
"""
import copy
import logging
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts import fiscal
from catalogue.models import BillingCompany, BillingRule
from mytp_core.contracts.checks import PATIENT_CODE_RE
from mytp_core.money import Money
from payments.models import Order

from . import feed
from .models import EINVOICE_FIELDS, Invoice, InvoiceLine, InvoiceSeries

logger = logging.getLogger(__name__)

# The buyer fields on an invoice, by the fiscal field they copy.
_BILL = {name: ('bill_billing_email' if name == 'billing_email' else f'bill_{name}')
         for name in fiscal.FIELDS}
_ACTOR_TYPES = ('doctor', 'admin', 'system')


class InvoiceError(Exception):
    """An invoice was not issued."""


class CompanyMissing(InvoiceError):
    """The billing company is not in the configuration snapshot (or its time zone is bad)."""


class BadBuyer(InvoiceError):
    """The buyer of a manual invoice is not usable."""


class NoLines(InvoiceError):
    """A manual invoice has no lines."""


class BadLine(InvoiceError):
    """A line of a manual invoice is not usable (a 0% line needs its text)."""


class BadStatus(InvoiceError):
    """Not an e-invoice status."""


# --- Helpers ---------------------------------------------------------------------------------

def _company(code):
    """The billing company with `code` (an active one first), or CompanyMissing."""
    company = (BillingCompany.objects.filter(code=code or '').order_by('-active', 'id')
               .first())
    if company is None:
        raise CompanyMissing(f'no billing company {code!r} in the configuration')
    return company


def _zone(company):
    try:
        return ZoneInfo(company.time_zone)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise CompanyMissing(f'billing company {company.code!r} has no valid time zone') \
            from None


def _instance():
    return settings.REGION_PROFILE.invoice_prefix


def _next_number(company_code, instance, year, kind=InvoiceSeries.INVOICE):
    """The next number of the series of `kind`, `<company>-<instance>-<year>-<6 digits>`
    (a credit note's: `...-R<6 digits>`), from the series' counter row, locked until the caller's transaction ends. Two first
    invoices of a new series at once: the second create fails on the unique key once the
    first commits, and then waits for its lock."""
    with transaction.atomic():
        while True:
            series = (InvoiceSeries.objects.select_for_update()
                      .filter(company_code=company_code, instance=instance, year=year,
                              kind=kind)
                      .first())
            if series is not None:
                series.last_number += 1
                series.save(update_fields=['last_number'])
                prefix = 'R' if kind == InvoiceSeries.CREDIT_NOTE else ''
                return f'{company_code}-{instance}-{year}-{prefix}{series.last_number:06d}'
            try:
                with transaction.atomic():
                    InvoiceSeries.objects.create(company_code=company_code,
                                                 instance=instance, year=year, kind=kind)
            except IntegrityError:
                pass  # created by another transaction meanwhile: lock that one


def _seller(company, legal_name=None):
    return {'seller_legal_name': legal_name or company.legal_name, 'seller_address': company.address,
            'seller_country': company.country, 'seller_company_number': company.company_number,
            'seller_vat_number': company.vat_number, 'seller_tax_number': company.tax_number}


def _create(company, issued_at, legal_name=None, **fields):
    """The invoice row with the next number of its series, in the company's time zone."""
    local = issued_at.astimezone(_zone(company))
    instance = _instance()
    series = (InvoiceSeries.CREDIT_NOTE if fields.get('kind') == Invoice.CREDIT_NOTE
              else InvoiceSeries.INVOICE)
    number = _next_number(company.code, instance, local.year, series)
    # A credit note brings the original's seller snapshot; the others take the company's.
    seller = {name: fields.pop(name) for name in list(fields) if name.startswith('seller_')}
    return Invoice.objects.create(
        number=number, company_code=company.code, instance=instance, year=local.year,
        issued_at=issued_at, issue_date=local.date(), time_zone=company.time_zone,
        **(seller or _seller(company, legal_name)), **fields)


def _tax_category(percent, text, reverse_charge):
    if percent > 0:
        return InvoiceLine.STANDARD
    if reverse_charge:
        return InvoiceLine.REVERSE_CHARGE
    return InvoiceLine.EXEMPT if text else InvoiceLine.ZERO


# --- From an order ---------------------------------------------------------------------------

def issue_for_order(order):
    """The invoice of a paid `order`, issued once: a repeated call (the hook firing again)
    returns the existing one. Copies the order, never re-prices it. Raises `InvoiceError`
    (a missing company) when it cannot."""
    existing = Invoice.objects.filter(order=order).first()
    if existing is not None:
        return existing
    company = _company(order.company_code)
    # Reverse charge, as the pricing applies it: a valid VIES number in a country other than
    # the seller's, and the line's text is a reverse-charge text of the company's rules.
    reverse = (order.bill_vies_status == 'valid'
               and (order.bill_country.code or '') != (company.country or '').upper())
    texts = set()
    if reverse:
        texts = {text.strip() for text in BillingRule.objects.filter(
            company__code=company.code).values_list('reverse_charge_text', flat=True)
            if text.strip()}
    with transaction.atomic():
        invoice = _create(
            company, order.paid_at or timezone.now(), legal_name=order.company_legal_name,
            kind=Invoice.ORDER, order=order,
            actor_type='system', actor_uuid=None, currency=order.currency,
            net_total=order.net_total, discount_total=order.discount_total,
            ice_total=order.ice_total, voucher_taken=order.voucher_taken,
            vat_total=order.vat_total, total=order.total,
            vat_groups=copy.deepcopy(order.vat_groups),
            bill_entity_id=order.bill_entity_id,
            bill_entity_version=order.bill_entity_version,
            bill_vies_status=order.bill_vies_status,
            **{target: getattr(order, target) for target in _BILL.values()})
        InvoiceLine.objects.bulk_create([
            InvoiceLine(
                invoice=invoice, position=row.position, code=row.code, name=row.name,
                quantity=row.quantity, list_amount=row.list_amount,
                discount_percent=row.discount_percent, discount_amount=row.discount_amount,
                ice=row.ice, voucher_share=row.voucher_share, net=row.net,
                vat_percent=row.vat_percent, vat_text=row.vat_text,
                tax_category=_tax_category(row.vat_percent, row.vat_text,
                                         row.vat_text.strip() in texts),
                included_case=row.included_case, patient_code=row.patient_code)
            for row in order.lines.order_by('position')])
        feed.send_invoice(invoice)
    return invoice


# --- A credit note ---------------------------------------------------------------------------

def _negated(value):
    return -value if value else value  # keeps 0.00 as 0.00, never -0.00


def issue_credit_note(invoice, *, reason, actor):
    """The credit note of `invoice`, in full (epic-payments entry 7): its seller and buyer
    snapshots, lines and VAT groups with negated amounts, numbered in the credit-note series
    (`<company>-<instance>-<year>-R<6 digits>`, the year of the issue in the company's time
    zone) and referencing the original (`original`, so its number and issue date). One per
    invoice: a repeated call returns the existing note. `reason` is `cancellation`,
    `goodwill` or `error`; `extensions["rectification_code"]` is left empty for the tax
    advisor. Takes the number inside the caller's transaction, so a refund that rolls back
    leaves no gap. Raises `InvoiceError` for a credit note, a bad reason or a missing
    company."""
    from payments import service as payments_service
    payments_service._check_actor(actor)
    if actor.type == 'doctor':
        raise payments_service.InvalidActor('a credit note is issued by staff or the system')
    if invoice.kind == Invoice.CREDIT_NOTE:
        raise InvoiceError('a credit note has no credit note')
    if reason not in dict(Invoice.REASONS):
        raise InvoiceError(f'not a credit-note reason: {reason!r}')
    with transaction.atomic():
        # Serialise on the original, so two calls never number two notes.
        original = Invoice.objects.select_for_update().get(pk=invoice.pk)
        existing = Invoice.objects.filter(original=original,
                                          kind=Invoice.CREDIT_NOTE).first()
        if existing is not None:
            return existing
        company = _company(original.company_code)
        note = _create(
            company, timezone.now(),
            seller_legal_name=original.seller_legal_name,
            seller_address=original.seller_address, seller_country=original.seller_country,
            seller_company_number=original.seller_company_number,
            seller_vat_number=original.seller_vat_number,
            seller_tax_number=original.seller_tax_number,
            kind=Invoice.CREDIT_NOTE, order=None, original=original, reason=reason,
            actor_type=actor.type, actor_uuid=actor.uuid, currency=original.currency,
            net_total=_negated(original.net_total),
            discount_total=_negated(original.discount_total),
            ice_total=_negated(original.ice_total),
            voucher_taken=_negated(original.voucher_taken),
            vat_total=_negated(original.vat_total), total=_negated(original.total),
            vat_groups=[{**group, 'base': _negate_text(group.get('base')),
                         'vat': _negate_text(group.get('vat'))}
                        for group in copy.deepcopy(original.vat_groups)],
            bill_entity_id=original.bill_entity_id,
            bill_entity_version=original.bill_entity_version,
            bill_vies_status=original.bill_vies_status,
            extensions={'rectification_code': ''},
            **{target: getattr(original, target) for target in _BILL.values()})
        InvoiceLine.objects.bulk_create([
            InvoiceLine(
                invoice=note, position=row.position, code=row.code, name=row.name,
                quantity=row.quantity, list_amount=_negated(row.list_amount),
                discount_percent=row.discount_percent,
                discount_amount=_negated(row.discount_amount), ice=_negated(row.ice),
                voucher_share=_negated(row.voucher_share), net=_negated(row.net),
                vat_percent=row.vat_percent, vat_text=row.vat_text,
                tax_category=row.tax_category, included_case=row.included_case,
                patient_code=row.patient_code)
            for row in original.lines.order_by('position')])
        feed.send_invoice(note)
    return note


def _negate_text(value):
    """A VAT group's amount (a string such as "100.00"), negated."""
    amount = Decimal(str(value or '0'))
    return str(_negated(amount))


# --- A manual sale ---------------------------------------------------------------------------

def _check_buyer(buyer):
    if not isinstance(buyer, dict):
        raise BadBuyer('the buyer is a dict of fiscal fields')
    unknown = set(buyer) - set(fiscal.FIELDS)
    if unknown:
        raise BadBuyer(f'not buyer fields: {", ".join(sorted(unknown))}')
    data = {name: buyer.get(name, '') or '' for name in fiscal.FIELDS}
    if data['kind'] not in ('person', 'company'):
        raise BadBuyer('the buyer is a person or a company')
    if not isinstance(data['country'], str):
        data['country'] = getattr(data['country'], 'code', '')
    for name, value in data.items():
        if not isinstance(value, str):
            raise BadBuyer(f'{name} is text')
        field = Invoice._meta.get_field(_BILL[name])
        if field.max_length and len(value) > field.max_length:
            raise BadBuyer(f'{name} is too long')
    name_fields = ('legal_name',) if data['kind'] == 'company' else ('first_name', 'last_name')
    if not all(data[name].strip() for name in name_fields):
        raise BadBuyer('the buyer has no name')
    return data


def _decimal(value, what):
    if isinstance(value, float) or isinstance(value, bool):
        raise BadLine(f'{what} is a Decimal or a string, never a float')
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise BadLine(f'{what} is not a number') from None


_CROCKFORD = frozenset('0123456789ABCDEFGHJKMNPQRSTVWXYZ')  # no I, L, O, U


def _patient_code_fits(code):
    """This portal's region prefix and 8 Crockford characters (and the contract's form)."""
    prefix, _, rest = code.partition('-')
    return (bool(PATIENT_CODE_RE.fullmatch(code))
            and prefix == settings.REGION_PROFILE.patient_code_prefix
            and set(rest) <= _CROCKFORD)


def _check_line(raw, position, currency):
    """(row fields, net Money) of a manual line: `name`, `net` (for the quantity, 0 or
    more), `vat_percent` (0 to 100), `vat_text` (required at 0 %), and optionally `code`,
    `quantity` (default 1), `tax_category` and `patient_code`."""
    name = str(raw.get('name') or '').strip()
    if not name or len(name) > 150:
        raise BadLine(f'line {position} has no name or its name is too long')
    quantity = raw.get('quantity', 1)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise BadLine(f'line {position} has no valid quantity')
    percent = _decimal(raw.get('vat_percent'), f'line {position} vat_percent')
    if not 0 <= percent <= 100 or percent != percent.quantize(Decimal('0.01')):
        raise BadLine(f'line {position}: the VAT percent is 0 to 100 with two decimals')
    text = str(raw.get('vat_text') or '').strip()
    if len(text) > 500:
        raise BadLine(f'line {position}: the VAT text is too long')
    if percent == 0 and not text:
        raise BadLine(f'line {position}: a 0% line needs its exemption or reverse-charge text')
    try:
        net = Money(_decimal(raw.get('net'), f'line {position} net'), currency)
    except ValueError:
        raise BadLine(f'line {position}: not an amount') from None
    if net.amount < 0:
        raise BadLine(f'line {position}: the net is not negative')
    category = raw.get('tax_category') or _tax_category(percent, text, False)
    if category not in dict(InvoiceLine.TAX_CATEGORIES):
        raise BadLine(f'line {position}: not a tax category')
    if (category == InvoiceLine.STANDARD) != (percent > 0):
        raise BadLine(f'line {position}: the tax category does not fit the VAT percent')
    zero = Money.zero(currency)
    code = str(raw.get('code') or '')
    patient_code = str(raw.get('patient_code') or '')
    if len(code) > 64 or len(patient_code) > 32:
        raise BadLine(f'line {position}: the code or the patient code is too long')
    if patient_code and not _patient_code_fits(patient_code):
        raise BadLine(f'line {position}: the patient code is this region\'s prefix and 8 '
                      f'Crockford characters, such as EU-7K3QX9MD')
    amount = _decimal(raw.get('net'), f'line {position} net')
    if amount != amount.quantize(Decimal('0.01')):
        raise BadLine(f'line {position}: the net has more than two decimals')
    return dict(
        position=position, code=code, name=name, quantity=quantity,
        list_amount=net.amount, discount_percent=Decimal('0.00'),
        discount_amount=zero.amount, ice=zero.amount, voucher_share=zero.amount,
        net=net.amount, vat_percent=percent, vat_text=text, tax_category=category,
        included_case=False, patient_code=patient_code), net


def issue_manual(company_code, buyer, lines, currency, actor):
    """Invoice a sale with no order (and so no account needed): `buyer` is a dict of
    `accounts.fiscal.FIELDS`, `lines` a list of dicts (see `_check_line`), `currency` one of
    `Money`'s. The VAT is grouped per (percent, text) and rounded half up. `actor` is a
    `payments.service.Actor`. Returns the invoice, `kind=manual`, `order=None`."""
    from payments import service as payments_service
    payments_service._check_actor(actor)
    company = _company(company_code)
    data = _check_buyer(buyer)
    if not lines:
        raise NoLines('an invoice has at least one line')
    try:
        zero = Money.zero(currency)
    except (ValueError, TypeError):
        raise InvoiceError(f'not a currency: {currency!r}') from None
    rows, groups = [], {}
    for position, raw in enumerate(lines, start=1):
        row, net = _check_line(raw, position, currency)
        rows.append(row)
        key = (row['vat_percent'], row['vat_text'])
        groups[key] = groups.get(key, zero) + net
    vat_groups = [(percent, text, base, base * (percent / Decimal(100)))
                  for (percent, text), base in groups.items()]
    net_total = sum((Money(row['net'], currency) for row in rows), zero)
    vat_total = sum((vat for *_, vat in vat_groups), zero)
    with transaction.atomic():
        invoice = _create(
            company, timezone.now(), kind=Invoice.MANUAL, order=None,
            actor_type=actor.type, actor_uuid=actor.uuid, currency=currency,
            net_total=net_total.amount, discount_total=zero.amount, ice_total=zero.amount,
            voucher_taken=zero.amount, vat_total=vat_total.amount,
            total=(net_total + vat_total).amount,
            vat_groups=[{'percent': str(percent.quantize(Decimal('0.01'))), 'text': text, 'base': str(base.amount),
                         'vat': str(vat.amount)} for percent, text, base, vat in vat_groups],
            **{_BILL[name]: value for name, value in data.items()})
        InvoiceLine.objects.bulk_create([InvoiceLine(invoice=invoice, **row) for row in rows])
        feed.send_invoice(invoice)
    return invoice


# --- The e-invoice status ----------------------------------------------------------------------

def set_einvoice_status(invoice, status, reference=''):
    """Set the transmission status of `invoice` (and its reference, the channel's id of it):
    the only change an issued invoice takes. Returns the invoice."""
    if status not in dict(Invoice.EINVOICE_STATUSES):
        raise BadStatus(f'not an e-invoice status: {status!r}')
    with transaction.atomic():
        locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
        locked.einvoice_status = status
        locked.einvoice_reference = reference or ''
        locked.einvoice_updated_at = timezone.now()
        locked.save(update_fields=sorted(EINVOICE_FIELDS))
    invoice.einvoice_status = locked.einvoice_status
    invoice.einvoice_reference = locked.einvoice_reference
    invoice.einvoice_updated_at = locked.einvoice_updated_at
    return invoice
