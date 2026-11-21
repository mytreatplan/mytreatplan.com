"""The doctor's invoices (epic-payments entry 10): the list of their invoices and credit
notes, and the PDF of each.

Whose an invoice is: an order invoice, the order's doctor; a manual invoice, the doctor who
owns the fiscal entity named by its buyer snapshot (`bill_entity_id`); a credit note, its
original's owner. An invoice with no owner (no order or user, no entity) is nobody's: a 404,
as is another doctor's, a staff account's or a missing one. The list shows no patient names;
the PDF does (invoices.patients), for one response only.
"""
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from accounts.client_sync import is_client
from accounts.models import FiscalEntity

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


def _doctor(request):
    if not is_client(request.user):
        raise Http404('not a doctor')
    return request.user


@login_required
@never_cache
@require_http_methods(['GET', 'HEAD'])
def invoice_list(request):
    user = _doctor(request)
    return render(request, 'invoices/list.html', {'invoices': list(invoices_of(user))})


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
