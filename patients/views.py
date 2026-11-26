"""The doctor's patients pages (epic-ordering 9.1, 9.2, 9.15): the create form and the
list of their own patients (portrait, code, name or reference, status, created; all rows
rendered, searched and paged in the browser by patient-list.js), and a
patient page with the portrait and its controls. A doctor sees only their own patients
(another doctor's code is a 404); no name is put in a URL, a log line or a message. Writes
go through `patients.service` and `patients.portrait`."""
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from accounts.client_sync import is_client
from payments import service as payments_service
from storage import service as storage_service

from . import portrait, service
from .forms import PatientForm
from .models import Patient


def _doctor_only(request):
    if not is_client(request.user):
        raise Http404('not a doctor')


def create_patient_from_form(request, form):
    """Save the patient when the form is valid (with the success message); the new `Patient`,
    or None when the form is not valid. The case wizard (cart.views) reuses it."""
    if not form.is_valid():
        return None
    patient = service.create_patient(
        request.user, actor=payments_service.doctor_actor(request.user),
        **{name: form.cleaned_data[name]
           for name in ('first_name', 'surname', 'reference', 'comments')})
    messages.success(request, _('The patient has been saved with the code %(code)s.')
                     % {'code': patient.code})
    return patient


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'HEAD', 'POST'])
def patient_list(request):
    _doctor_only(request)
    open_dialog = False
    if request.method == 'POST':
        form = PatientForm(request.POST)
        if create_patient_from_form(request, form):
            return redirect('patients')
        open_dialog = True
    else:
        form = PatientForm()
    with_portraits = portrait.storage_available()
    rows = [{'patient': patient, 'search': service.search_text(patient),
             'portrait': portrait.portrait_of(patient, request.user) if with_portraits else None}
            for patient in service.patients_of(request.user)
            .select_related('portrait', 'portrait_pending')]
    return render(request, 'patients/list.html', {
        'form': form, 'rows': rows, 'with_portraits': with_portraits,
        'open_dialog': open_dialog})


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'HEAD', 'POST'])
def new_patient(request):
    """The add-patient form on its own page: the no-JS fallback of the list's dialog."""
    _doctor_only(request)
    form = PatientForm(request.POST) if request.method == 'POST' else PatientForm()
    if request.method == 'POST' and create_patient_from_form(request, form):
        return redirect('patients')
    return render(request, 'patients/new.html', {
        'form': form, 'action': reverse('patient_new'), 'back_url': reverse('patients'),
        'back_text': _('Back to patients')})


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
@never_cache
@require_http_methods(['GET', 'HEAD'])
def patient_portrait(request, code):
    """The patient's portrait for the doctor who owns it: a redirect to a fresh presigned
    inline link (no access record, as `portrait.portrait_of` explains). 404 for anything
    else: another doctor's or an erased patient, no clean portrait, no bucket."""
    patient = _own_patient(request, code)
    shown = patient.portrait
    if shown is None or not portrait.storage_available():
        raise Http404
    try:
        url = storage_service.owner_inline_url(shown, request.user)
    except (storage_service.DownloadRefused, storage_service.NotConfigured):
        raise Http404
    return redirect(url)


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
