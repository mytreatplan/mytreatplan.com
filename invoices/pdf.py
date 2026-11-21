"""An invoice or credit note as a PDF (epic-payments entry 10, AD-7).

`render(invoice, user)` builds the PDF from the invoice's own snapshot (the `seller_*` and
`bill_*` fields, the totals, `vat_groups` and each line's `vat_text`), never from the live
billing company or fiscal entity, in the doctor's profile language (the fallback of
`payments.transfers.build_message`: a language this portal does not offer, such as any
other than English on the ae portal, gives the default one). Each patient's current name
comes from `invoices.patients.names_for` and goes next to the code for this render only: it
is not stored, logged or cached. Rendering is synchronous; WeasyPrint needs Pango.
"""
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.utils import formats, translation
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


def context(invoice, user):
    """What the template shows, formatted in the active language."""
    rows = list(invoice.lines.order_by('position'))
    names = patients.names_for(user, [row.patient_code for row in rows])
    currency = invoice.currency
    credit = invoice.kind == invoice.CREDIT_NOTE
    original = invoice.original if credit else None
    lines = []
    for row in rows:
        name = names.get(row.patient_code)
        lines.append({
            'name': row.name, 'code': row.code, 'quantity': row.quantity,
            'patient': (f'{name} ({row.patient_code})' if name else row.patient_code),
            'net': money(row.net, currency), 'vat_percent': _percent(row.vat_percent),
            'vat_text': row.vat_text,
            'discount': money(row.discount_amount, currency) if row.discount_amount else ''})
    totals = [(pgettext('invoice PDF', 'Net total'), money(invoice.net_total, currency))]
    for label, value in ((pgettext('invoice PDF', 'Discount'), invoice.discount_total),
                         (pgettext('invoice PDF', 'Special consumption tax'), invoice.ice_total),
                         (pgettext('invoice PDF', 'Voucher'), invoice.voucher_taken)):
        if value:
            totals.append((label, money(value, currency)))
    return {
        'invoice': invoice, 'credit': credit, 'original': original,
        'reason': _reason(invoice.reason) if credit else '',
        'title': pgettext('invoice PDF', 'Credit note') if credit else pgettext('invoice PDF', 'Invoice'),
        'seller': _seller(invoice), 'buyer': _buyer(invoice), 'lines': lines,
        'issue_date': formats.date_format(invoice.issue_date, 'DATE_FORMAT'),
        'original_date': (formats.date_format(original.issue_date, 'DATE_FORMAT')
                          if original else ''),
        'totals': totals,
        'groups': [{'percent': _percent(group.get('percent')), 'text': group.get('text', ''),
                    'base': money(group.get('base'), currency),
                    'vat': money(group.get('vat'), currency)}
                   for group in invoice.vat_groups],
        'total': money(invoice.total, currency), 'vat_total': money(invoice.vat_total, currency),
        'fonts': {name: Path(finders.find(path)).as_uri() for name, path in FONTS.items()
                  if finders.find(path)},
    }


def render_html(invoice, user):
    """The document's HTML, in the doctor's language."""
    language = language_of(user)
    with translation.override(language):
        return render_to_string(TEMPLATE, {**context(invoice, user), 'language': language})


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


def render(invoice, user):
    """The PDF's bytes. Only the fonts (`font_urls`) can be loaded by the renderer."""
    # Imported here: WeasyPrint loads Pango at import, and only a download needs it.
    from weasyprint import HTML
    html = render_html(invoice, user)
    return HTML(string=html, url_fetcher=_fonts_only()).write_pdf()
