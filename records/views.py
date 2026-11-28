"""The records section of the patient file (epic-case-lifecycle 10.5): the case-actions slot
entry (`slot_context`) and its two POSTs, attach (the widget posts the stored object's id,
as the portrait does) and Records complete. Both redirect back to the patient file with a
message; another doctor's or an erased patient is a 404. No patient name or file name goes
into a URL, a message or a log line (AD-23)."""
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from patients import service as patients_service

from . import service


def slot_context(request, patient):
    """The context of the slot entry (templates/records/_case_records.html): the upload
    section while the live work order waits for records, and the status of every service
    that has a file."""
    work_order = service.waiting_work_order(patient)
    available = service.storage_available()
    rows = service.statuses(patient)
    return {
        'upload': work_order is not None and available,
        'version': work_order.version if work_order is not None else 0,
        'purpose': service.PURPOSE, 'max_size': service.MAX_SIZE,
        'statuses': rows,
        'CHECKING': service.CHECKING, 'WAITING': service.WAITING,
        'PROCESSED': service.PROCESSED, 'REFUSED': service.REFUSED}


def _target(code):
    return reverse('patient', args=[code])


@login_required
@csrf_protect
@never_cache
@require_http_methods(['POST'])
def attach(request, code):
    """Link the uploaded object (its id is the only thing posted) as the case's records."""
    try:
        object_id = uuid.UUID(str(request.POST.get('object_id', '')))
    except ValueError:
        raise Http404
    try:
        service.attach(request.user, code, object_id)
    except service.NotAttachable:
        raise Http404
    except service.NotUploadable:
        messages.error(request, _('This case is not waiting for records any more.'))
        return redirect(_target(code))
    messages.success(request, _('Your file is being checked. Records complete is '
                                'available once the check is done.'))
    return redirect(_target(code))


@login_required
@csrf_protect
@never_cache
@require_http_methods(['POST'])
def complete(request, code):
    """Records complete: the doctor's move to Processing prescription."""
    try:
        version = int(request.POST.get('version', ''))
    except ValueError:
        messages.error(request, _('The change could not be saved. Reload the page and try '
                                  'again.'))
        return redirect(_target(code))
    try:
        service.complete(request.user, code, version)
    except service.NotAttachable:
        raise Http404
    except service.NotUploadable:
        messages.error(request, _('This case is not waiting for records any more.'))
    except service.NotReady:
        messages.error(request, _('Upload your records first. Records complete is available '
                                  'once your file has passed the check.'))
    except patients_service.StaleVersion:
        messages.error(request, _('This case changed meanwhile. Reload the page and try '
                                  'again.'))
    except patients_service.MoveRefused:
        messages.error(request, _('The records could not be completed. Reload the page and '
                                  'try again.'))
    else:
        messages.success(request, _('Thank you. Your records are complete and the case is '
                                    'now being processed.'))
    return redirect(_target(code))
