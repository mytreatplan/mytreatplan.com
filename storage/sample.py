"""The staging-only `sample` purpose and its page (decision 2026-11-06), which replace the
upload spike's until the first real purpose arrives (then both go).

Only active staff, only with STORAGE_SAMPLE_UPLOADS=True (the check storage.E002 refuses
it on a public site). The page (`/<lang>/storage/sample/`, routed only with the setting
on) uploads with the widget, lists the sample objects and downloads or deletes them;
every view answers 404 to anyone else. Sample objects may be downloaded from
`quarantine/` (no scan before entry 12). English only, not in the translation catalogue.
"""
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import purposes, s3, service
from .models import StoredObject

PURPOSE = 'sample'
DOWNLOAD_REASON = 'sample_page'


def sample_allowed(user):
    return bool(settings.STORAGE_SAMPLE_UPLOADS and user.is_authenticated and user.is_active
                and user.is_staff)


def register():
    purposes.register(PURPOSE, sample_allowed, lambda user, obj: sample_allowed(user),
                      download_quarantine=True)


def sample_staff_only(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not sample_allowed(request.user):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapper


def _sample_object(pk):
    return get_object_or_404(StoredObject, pk=pk, purpose=PURPOSE)


@never_cache
@sample_staff_only
@require_GET
def page(request):
    return render(request, 'storage/sample.html', {
        'missing_setting': s3.missing_setting(),
        'bucket': settings.STORAGE_BUCKET,
        'endpoint': settings.STORAGE_ENDPOINT,
        'purpose': PURPOSE,
        'max_size': purposes.get(PURPOSE).max_size,
        'objects': StoredObject.objects.filter(purpose=PURPOSE,
                                               delete_requested_at__isnull=True)[:100],
    })


@never_cache
@sample_staff_only
@require_POST
def download(request, pk):
    obj = _sample_object(pk)
    try:
        url = service.download_url(obj, request.user, DOWNLOAD_REASON, request=request)
    except service.DownloadRefused:
        return HttpResponse('This download is not allowed.', status=403,
                            content_type='text/plain')
    except service.AccessRecordFailed:
        return HttpResponse('The access could not be recorded, so there is no download. '
                            'Try again.', status=503, content_type='text/plain')
    except service.NotConfigured as exc:
        return HttpResponse(f'{exc}.', status=503, content_type='text/plain')
    return HttpResponseRedirect(url)


@never_cache
@sample_staff_only
@require_POST
def delete(request, pk):
    obj = _sample_object(pk)
    service.delete(obj, request.user)
    messages.success(request, f'Object {obj.pk} will be deleted by the worker.')
    return redirect('storage_sample:page')
