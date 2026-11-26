"""The doctor's invoices (epic-payments entry 10): the list of their invoices and credit
notes, and the PDF of each.

Whose an invoice is: an order invoice, the order's doctor; a manual invoice, the doctor who
owns the fiscal entity named by its buyer snapshot (`bill_entity_id`); a credit note, its
original's owner. An invoice with no owner (no order or user, no entity) is nobody's: a 404,
as is another doctor's, a staff account's or a missing one. The list shows no patient names;
the PDF does (invoices.patients), for one response only.

Pending-payment documents (epic-ordering 9.19): each of the doctor's orders waiting for a
bank transfer (`integration.transfers.pending`: `pending_payment` with an open
`bank_transfer` attempt) is listed among the invoices by date, marked "Pending payment" and
never as an invoice, with its PDF (`invoice_pending_pdf`). Paid, it leaves the list (its
invoice takes its place); cancelled or switched to another method, it leaves the list and
its PDF is a 404, as is another doctor's order.
"""
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from accounts.client_sync import is_client
from accounts.models import FiscalEntity
from integration import transfers

from . import pdf
from .models import Invoice


def invoices_of(user):
    """The invoices and credit notes that are `user`'s, newest first."""
    entities = FiscalEntity.objects.filter(user_id=user.pk).values('pk')
    return Invoice.objects.filter(
        Q(kind=Invoice.ORDER, order__user_id=user.pk)
        | Q(kind=Invoice.MANUAL, bill_entity_id__in=entities)
        | Q(kind=Invoice.CREDIT_NOTE, original__kind=Invoice.ORDER,
            original__order__user_id=user.pk)
        | Q(kind=Invoice.CREDIT_NOTE, original__kind=Invoice.MANUAL,
            original__bill_entity_id__in=entities)
    ).select_related('original').order_by('-issued_at', '-number')


def pending_documents_of(user):
    """The orders of `user` waiting for a bank transfer (their pending-payment documents)."""
    return transfers.pending().filter(user_id=user.pk)


def _rows(user):
    """The list's rows, newest first: each invoice (`invoice`) or pending-payment document
    (`pending`), with its `date`. An invoice is dated by its issue, a pending document by
    the day its order was frozen (`pdf.pending_date`)."""
    rows = [{'invoice': invoice, 'when': invoice.issued_at, 'date': invoice.issue_date}
            for invoice in invoices_of(user)]
    companies = {}
    for order in pending_documents_of(user):
        if order.company_code not in companies:
            companies[order.company_code] = pdf.company_of(order)
        rows.append({'pending': order, 'when': order.pending_at or order.created_at,
                     'date': pdf.pending_date(order, companies[order.company_code])})
    # Stable: invoices issued at the same time keep their order (by number).
    return sorted(rows, key=lambda row: row['when'], reverse=True)


def _doctor(request):
    if not is_client(request.user):
        raise Http404('not a doctor')
    return request.user


@login_required
@never_cache
@require_http_methods(['GET', 'HEAD'])
def invoice_list(request):
    user = _doctor(request)
    return render(request, 'invoices/list.html', {'rows': _rows(user)})


@login_required
@never_cache
@require_http_methods(['GET', 'HEAD'])
def invoice_pdf(request, pk):
    user = _doctor(request)
    invoice = invoices_of(user).filter(pk=pk).first()
    if invoice is None:
        raise Http404('not your invoice')
    response = HttpResponse(pdf.render(invoice, user), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{invoice.number}.pdf"'
    return response


@login_required
@never_cache
@require_http_methods(['GET', 'HEAD'])
def invoice_pending_pdf(request, order_pk):
    user = _doctor(request)
    order = pending_documents_of(user).filter(pk=order_pk).first()
    if order is None:
        raise Http404('not your pending order')
    response = HttpResponse(pdf.render_pending(order, user), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{order.reference}-pending.pdf"'
    return response
