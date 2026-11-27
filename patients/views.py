"""The doctor's patients pages (epic-ordering 9.1, 9.2, 9.15): the create form and the
list of their own patients (portrait, code, name or reference, status, created; all rows
rendered, searched and paged in the browser by patient-list.js, with the stage of the
patient's current case), and a patient page with the portrait and its controls, the
Services history (every work order with its dated stage changes), the editable comments
(`patient_comments`), the active/inactive control (`patient_status`) and the two slots
other apps fill (`patients.slots`). A doctor sees only their own patients
(another doctor's code is a 404); no name is put in a URL, a log line or a message. Writes
go through `patients.service` (comments and status: `update_comments`, `set_active`) and
`patients.portrait`."""
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

from catalogue import resolver

from . import portrait, service, slots
from .stages import stage_label
from .forms import CommentsForm, PatientForm, StatusForm
from .models import Patient, WorkOrder


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
    patients = list(service.patients_of(request.user)
                    .select_related('portrait', 'portrait_pending'))
    current = service.current_work_orders(patients)
    rows = [{'patient': patient, 'search': service.search_text(patient),
             'portrait': portrait.portrait_of(patient, request.user) if with_portraits else None,
             'stage': current[patient.pk].stage if patient.pk in current else '',
             'stage_label': (stage_label(current[patient.pk].stage)
                             if patient.pk in current else '')}
            for patient in patients]
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


def _case_of(patient):
    """The service and translated stage of the patient's current case, or None."""
    work_order = service.current_work_order(patient)
    if work_order is None:
        return None
    product = resolver.product(work_order.product_id) if work_order.product_id else None
    return {'service': product.public_name if product is not None else work_order.code,
            'stage': work_order.stage, 'stage_label': stage_label(work_order.stage)}


def _services_of(patient):
    """Every service (work order) of the patient, newest first, each with its product name
    (or code), current stage and dated stage changes (oldest first). `recorded` is False
    when the stage changes of a service from before they were recorded are missing: no row,
    or a first row that is not its creation."""
    work_orders = (WorkOrder.objects.filter(patient_id=patient.pk)
                   .prefetch_related('stage_changes').order_by('-created_at', '-id'))
    services = []
    for work_order in work_orders:
        product = resolver.product(work_order.product_id) if work_order.product_id else None
        changes = list(work_order.stage_changes.all())
        services.append({
            'service': product.public_name if product is not None else work_order.code,
            'stage': work_order.stage, 'stage_label': stage_label(work_order.stage),
            'created_at': work_order.created_at,
            'changes': [{'at': change.at, 'stage': change.to_stage,
                         'label': stage_label(change.to_stage)} for change in changes],
            'recorded': bool(changes) and changes[0].from_stage == ''})
    return services


def _file_context(request, patient, comments_form=None):
    """The patient file's context: the portrait part, the case, the comments form, the
    services history and the slot entries."""
    available = portrait.storage_available()
    rejected = portrait.settle(patient) if available else False
    if available:
        patient.refresh_from_db()
    shown = portrait.portrait_of(patient, request.user) if available else portrait.Portrait()
    return {
        'patient': patient, 'portrait': shown, 'rejected': rejected,
        'storage_available': available, 'purpose': portrait.PURPOSE,
        'max_size': portrait.MAX_SIZE, 'allowed_types': ' '.join(portrait.CONTENT_TYPES),
        'has_portrait': portrait.has_portrait(patient), 'case': _case_of(patient),
        'comments_form': comments_form or CommentsForm(
            initial={'comments': patient.comments, 'version': patient.version}),
        'services': _services_of(patient),
        'case_actions': slots.render_context(slots.CASE_ACTIONS, request, patient),
        'appointments': slots.render_context(slots.APPOINTMENTS, request, patient)}


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
    return render(request, 'patients/patient.html', _file_context(request, patient))


def _stale():
    return _('This patient changed meanwhile. Reload the page and try again.')


@login_required
@csrf_protect
@never_cache
@require_http_methods(['POST'])
def patient_comments(request, code):
    """Save the patient's comments (epic-case-lifecycle 10.4). The text is never logged."""
    patient = _own_patient(request, code)
    form = CommentsForm(request.POST)
    target = reverse('patient', args=[patient.code])
    if not form.is_valid():
        return render(request, 'patients/patient.html',
                      _file_context(request, patient, comments_form=form))
    try:
        service.update_comments(
            patient, form.cleaned_data['comments'], form.cleaned_data['version'],
            payments_service.doctor_actor(request.user))
    except service.StaleVersion:
        messages.error(request, _stale())
        return redirect(target)
    except service.PatientInvalid:
        raise Http404
    messages.success(request, _('The comments have been saved.'))
    return redirect(target)


@login_required
@csrf_protect
@never_cache
@require_http_methods(['POST'])
def patient_status(request, code):
    """Set the patient active or inactive (epic-case-lifecycle 10.4)."""
    patient = _own_patient(request, code)
    form = StatusForm(request.POST)
    target = reverse('patient', args=[patient.code])
    if not form.is_valid():
        messages.error(request, _('The change could not be saved. Reload the page and try '
                                  'again.'))
        return redirect(target)
    active = form.cleaned_data['active']
    try:
        service.set_active(patient, active, form.cleaned_data['version'],
                           payments_service.doctor_actor(request.user))
    except service.StaleVersion:
        messages.error(request, _stale())
        return redirect(target)
    except service.PatientInvalid:
        raise Http404
    messages.success(request, _('The patient is now active.') if active
                     else _('The patient is now inactive.'))
    return redirect(target)


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
