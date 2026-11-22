"""The doctor's patients page (epic-ordering 9.1): a create form and the list of their own
patients (code, name or reference, created). A doctor sees only their own patients; no name
is put in a URL, a log line or a message. Writes go through `patients.service`."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from accounts.client_sync import is_client
from payments import service as payments_service

from . import service
from .forms import PatientForm


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'HEAD', 'POST'])
def patient_list(request):
    if not is_client(request.user):
        raise Http404('not a doctor')
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
    return render(request, 'patients/list.html', {
        'form': form, 'patients': list(service.patients_of(request.user))})
