"""An invoice or credit note as a PDF (epic-payments entry 10, AD-7).

`render(invoice, user)` builds the PDF from the invoice's own snapshot (the `seller_*` and
`bill_*` fields, the totals, `vat_groups` and each line's `vat_text`), never from the live
billing company or fiscal entity, in the doctor's profile language (the fallback of
`payments.transfers.build_message`: a language this portal does not offer, such as any
other than English on the ae portal, gives the default one). Each patient's current name
comes from `invoices.patients.names_for` and goes next to the code for this render only: it
is not stored, logged or cached. Rendering is synchronous; WeasyPrint needs Pango.

`render_pending(order, user)` (epic-ordering 9.19) renders an order waiting for its bank
transfer in the same layout as a "Pending payment document" that says it is not an
invoice: no number, the order's reference, the bank details, and the seller from the live
billing company (user decision 2026-11-26; nothing is snapshotted or stored).
"""
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.utils import formats, timezone, translation
from django.utils.translation import pgettext

from accounts.profile import profile_of

from . import patients

TEMPLATE = 'invoices/pdf.html'
FONTS = {'latin': 'fonts/inter-latin-wght-normal.woff2',
         'latin_ext': 'fonts/inter-latin-ext-wght-normal.woff2'}


def language_of(user):
    """The doctor's profile language, else the site's default (as `build_message`)."""
    language = profile_of(user).language
    if language not in dict(settings.LANGUAGES):
        language = settings.LANGUAGE_CODE
    return language


def money(value, currency):
    """`1,234.50 EUR`: two decimals, the active language's separators, the currency code."""
    try:
        amount = Decimal(str(value or '0'))
    except InvalidOperation:
        amount = Decimal('0')
    return f'{formats.number_format(amount, 2, force_grouping=True)} {currency}'


def _percent(value):
    """`21`, `7.5`: the VAT percent without trailing zeros."""
    return f'{Decimal(str(value or "0")).normalize():f}'


def _buyer(invoice):
    lines = [invoice.bill_display_name]
    if invoice.bill_kind == 'company' and (invoice.bill_first_name or invoice.bill_last_name):
        lines.append(f'{invoice.bill_first_name} {invoice.bill_last_name}'.strip())
    lines += [invoice.bill_address_line1, invoice.bill_address_line2,
              f'{invoice.bill_postal_code} {invoice.bill_city}'.strip(), invoice.bill_province,
              invoice.bill_country.name if invoice.bill_country else '']
    ids = []
    if invoice.bill_tax_id:
        ids.append((pgettext('invoice PDF', 'Tax ID'), invoice.bill_tax_id))
    if invoice.bill_vat_id:
        ids.append((pgettext('invoice PDF', 'VAT ID'), invoice.bill_vat_id))
    return {'lines': [line for line in lines if line], 'ids': ids}


def _seller(invoice):
    ids = [(label, value) for label, value in (
        (pgettext('invoice PDF', 'Company number'), invoice.seller_company_number),
        (pgettext('invoice PDF', 'VAT ID'), invoice.seller_vat_number),
        (pgettext('invoice PDF', 'Tax ID'), invoice.seller_tax_number)) if value]
    return {'name': invoice.seller_legal_name,
            'lines': [line for line in invoice.seller_address.splitlines() if line.strip()],
            'ids': ids}


def _reason(code):
    return {'cancellation': pgettext('invoice PDF', 'Cancellation'),
            'goodwill': pgettext('invoice PDF', 'Goodwill'),
            'error': pgettext('invoice PDF', 'Error')}.get(code, '')


def _lines(rows, user, currency):
    """The lines as the template shows them: each patient's current name next to the
    code (invoices.patients), for this render only."""
    names = patients.names_for(user, [row.patient_code for row in rows])
    lines = []
    for row in rows:
        name = names.get(row.patient_code)
        lines.append({
            'name': row.name, 'code': row.code, 'quantity': row.quantity,
            'patient': (f'{name} ({row.patient_code})' if name else row.patient_code),
            'net': money(row.net, currency), 'vat_percent': _percent(row.vat_percent),
            'vat_text': row.vat_text,
            'discount': money(row.discount_amount, currency) if row.discount_amount else ''})
    return lines


def _amounts(document):
    """The totals, VAT groups, VAT total and total of an invoice or an order (the same
    field names on both)."""
    currency = document.currency
    totals = [(pgettext('invoice PDF', 'Net total'), money(document.net_total, currency))]
    for label, value in ((pgettext('invoice PDF', 'Discount'), document.discount_total),
                         (pgettext('invoice PDF', 'Special consumption tax'), document.ice_total),
                         (pgettext('invoice PDF', 'Voucher'), document.voucher_taken)):
        if value:
            totals.append((label, money(value, currency)))
    return {
        'totals': totals,
        'groups': [{'percent': _percent(group.get('percent')), 'text': group.get('text', ''),
                    'base': money(group.get('base'), currency),
                    'vat': money(group.get('vat'), currency)}
                   for group in document.vat_groups],
        'total': money(document.total, currency),
        'vat_total': money(document.vat_total, currency),
    }


def _fonts():
    return {name: Path(finders.find(path)).as_uri() for name, path in FONTS.items()
            if finders.find(path)}


def context(invoice, user):
    """What the template shows, formatted in the active language."""
    credit = invoice.kind == invoice.CREDIT_NOTE
    original = invoice.original if credit else None
    return {
        'invoice': invoice, 'pending': False, 'credit': credit, 'original': original,
        'reason': _reason(invoice.reason) if credit else '',
        'title': pgettext('invoice PDF', 'Credit note') if credit else pgettext('invoice PDF', 'Invoice'),
        'seller': _seller(invoice), 'buyer': _buyer(invoice),
        'lines': _lines(list(invoice.lines.order_by('position')), user, invoice.currency),
        'issue_date': formats.date_format(invoice.issue_date, 'DATE_FORMAT'),
        'original_date': (formats.date_format(original.issue_date, 'DATE_FORMAT')
                          if original else ''),
        **_amounts(invoice),
        'fonts': _fonts(),
    }


# --- A pending-payment document (epic-ordering 9.19) ----------------------------------------
# An order waiting for a bank transfer, shown in the invoice's layout but never as an
# invoice: no number (its reference is the order's), the "not an invoice" notice, and the
# bank details to pay. Built from the order and the live billing company: nothing is stored.

def company_of(order):
    """The order's billing company (an active one first), or None: the same row as its
    bank details (`payments.bank_transfer.company_of`)."""
    from payments import bank_transfer
    return bank_transfer.company_of(order)


def pending_date(order, company=None):
    """The document's date: the day the order was frozen (`pending_at`), in the billing
    company's time zone (as an invoice's issue date), else the site's."""
    when = order.pending_at or order.created_at
    company = company if company is not None else company_of(order)
    try:
        zone = ZoneInfo(company.time_zone) if company is not None else None
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        zone = None
    return timezone.localtime(when, zone).date()


def _live_seller(order, company):
    """The seller from the live billing company (user decision 2026-11-26), named as the
    order names it (as `invoices.service.issue_for_order`)."""
    if company is None:
        return {'name': order.company_legal_name, 'lines': [], 'ids': []}
    ids = [(label, value) for label, value in (
        (pgettext('invoice PDF', 'Company number'), company.company_number),
        (pgettext('invoice PDF', 'VAT ID'), company.vat_number),
        (pgettext('invoice PDF', 'Tax ID'), company.tax_number)) if value]
    return {'name': order.company_legal_name or company.legal_name,
            'lines': [line for line in company.address.splitlines() if line.strip()],
            'ids': ids}


def pending_context(order, user):
    """What the template shows for a pending-payment document, in the active language."""
    from payments import bank_transfer
    from payments.models import Payment
    company = company_of(order)
    payment = order.payments.filter(gateway=bank_transfer.NAME, state=Payment.OPEN).first()
    details = bank_transfer.details(order, payment)
    if details is not None:
        details = {**details, 'amount': money(details['amount'], details['currency'])}
    return {
        'invoice': None, 'pending': True, 'credit': False, 'original': None, 'reason': '',
        'title': pgettext('invoice PDF', 'Pending payment document'),
        'reference': order.reference, 'bank': details,
        'seller': _live_seller(order, company), 'buyer': _buyer(order),
        'lines': _lines(list(order.lines.order_by('position')), user, order.currency),
        'issue_date': formats.date_format(pending_date(order, company), 'DATE_FORMAT'),
        'original_date': '',
        **_amounts(order),
        'fonts': _fonts(),
    }


def render_html(invoice, user, build=context):
    """The document's HTML, in the doctor's language (`build`: `context`, or
    `pending_context` for an order waiting for its transfer)."""
    language = language_of(user)
    with translation.override(language):
        return render_to_string(TEMPLATE, {**build(invoice, user), 'language': language})


def render_pending_html(order, user):
    return render_html(order, user, build=pending_context)


def font_urls():
    """The file URIs of the fonts: all the renderer may load."""
    return {Path(finders.find(path)).as_uri() for path in FONTS.values() if finders.find(path)}


def _fonts_only():
    from weasyprint.urls import URLFetcher

    class FontsOnly(URLFetcher):
        def fetch(self, url, headers=None):
            if url not in font_urls():
                raise ValueError('only the invoice fonts can be loaded')
            return super().fetch(url, headers)
    return FontsOnly()


def render(invoice, user, build=context):
    """The PDF's bytes. Only the fonts (`font_urls`) can be loaded by the renderer."""
    # Imported here: WeasyPrint loads Pango at import, and only a download needs it.
    from weasyprint import HTML
    html = render_html(invoice, user, build)
    return HTML(string=html, url_fetcher=_fonts_only()).write_pdf()


def render_pending(order, user):
    """The pending-payment document of `order` as a PDF."""
    return render(order, user, build=pending_context)
