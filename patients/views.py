"""The doctor's patients pages (epic-ordering 9.1, 9.2): the create form and the paginated
list of their own patients (portrait, code, name or reference, status, created), and a
patient page with the portrait and its controls. A doctor sees only their own patients
(another doctor's code is a 404); no name is put in a URL, a log line or a message. Writes
go through `patients.service` and `patients.portrait`."""
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from accounts.client_sync import is_client
from payments import service as payments_service

from . import portrait, service
from .forms import PatientForm
from .models import Patient

PAGE_SIZES = (10, 25, 50, 100)
DEFAULT_SIZE = 10


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def page_links(current, total, spread=2):
    """The page numbers to link: the first, the last and those near `current`, with None
    for a gap."""
    wanted = sorted({1, total, *range(max(1, current - spread), min(total, current + spread) + 1)})
    links, last = [], 0
    for number in wanted:
        if number - last > 1:
            links.append(None)
        links.append(number)
        last = number
    return links


def _doctor_only(request):
    if not is_client(request.user):
        raise Http404('not a doctor')


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'HEAD', 'POST'])
def patient_list(request):
    _doctor_only(request)
    if request.method == 'POST':
        form = PatientForm(request.POST)
        if form.is_valid():
            patient = service.create_patient(
                request.user, actor=payments_service.doctor_actor(request.user),
                **{name: form.cleaned_data[name]
                   for name in ('first_name', 'surname', 'reference', 'comments')})
            messages.success(request, _('The patient has been saved with the code %(code)s.')
                             % {'code': patient.code})
            return redirect('patients')
    else:
        form = PatientForm()
    size = _int(request.GET.get('size'), DEFAULT_SIZE)
    if size not in PAGE_SIZES:
        size = DEFAULT_SIZE
    paginator = Paginator(
        service.patients_of(request.user).select_related('portrait', 'portrait_pending'), size)
    number = min(max(_int(request.GET.get('page'), 1), 1), paginator.num_pages)
    page = paginator.page(number)
    with_portraits = portrait.storage_available()
    rows = [(patient, portrait.portrait_of(patient, request.user) if with_portraits else None)
            for patient in page]
    return render(request, 'patients/list.html', {
        'form': form, 'rows': rows, 'page': page, 'size': size, 'sizes': PAGE_SIZES,
        'links': page_links(page.number, paginator.num_pages),
        'with_portraits': with_portraits})


def _own_patient(request, code):
    _doctor_only(request)
    patient = (service.patients_of(request.user).filter(code=code)
               .exclude(status=Patient.ERASED)
               .select_related('portrait', 'portrait_pending').first())
    if patient is None:
        raise Http404
    return patient


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'HEAD', 'POST'])
def patient_page(request, code):
    """The patient's page (GET) and the attach of an uploaded portrait (POST)."""
    patient = _own_patient(request, code)
    available = portrait.storage_available()
    if request.method == 'POST':
        if not available:
            raise Http404
        try:
            object_id = uuid.UUID(str(request.POST.get('object_id', '')))
        except ValueError:
            raise Http404
        try:
            outcome = portrait.attach(request.user, patient.code, object_id)
        except portrait.NotAttachable:
            raise Http404
        if outcome == portrait.REJECTED:
            messages.error(request, _('The portrait could not be accepted; try another file.'))
        elif outcome == portrait.PROMOTED:
            messages.success(request, _('The portrait has been saved.'))
        else:
            messages.success(request, _('The portrait is being checked. It will be shown '
                                        'once the check is done.'))
        return redirect(reverse('patient', args=[patient.code]))
    rejected = portrait.settle(patient) if available else False
    if available:
        patient.refresh_from_db()
    shown = portrait.portrait_of(patient, request.user) if available else portrait.Portrait()
    return render(request, 'patients/patient.html', {
        'patient': patient, 'portrait': shown, 'rejected': rejected,
        'storage_available': available, 'purpose': portrait.PURPOSE,
        'max_size': portrait.MAX_SIZE, 'allowed_types': ' '.join(portrait.CONTENT_TYPES),
        'has_portrait': portrait.has_portrait(patient)})


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'HEAD', 'POST'])
def portrait_delete(request, code):
    """The portrait's delete: the confirmation (GET), then the delete (POST)."""
    patient = _own_patient(request, code)
    if not portrait.storage_available():
        raise Http404
    target = reverse('patient', args=[patient.code])
    if request.method == 'POST':
        if portrait.remove(request.user, patient.code):
            messages.success(request, _('The portrait has been deleted.'))
        return redirect(target)
    if not portrait.has_portrait(patient):
        return redirect(target)
    return render(request, 'patients/portrait_delete.html', {'patient': patient})
