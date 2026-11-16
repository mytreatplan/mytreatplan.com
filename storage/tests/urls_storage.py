"""Root URLconf for the storage tests: the real one plus a bare widget page for the
browser test (`/en/storage/test-widget/`, staff only, the staff test purpose), which
replaces the retired staging sample page there."""
from django.conf.urls.i18n import i18n_patterns
from django.http import Http404, HttpResponse
from django.template import RequestContext, Template
from django.urls import path

from mytreatplan.urls import urlpatterns as site_urlpatterns
from storage import purposes

from .conftest import STAFF_PURPOSE

WIDGET_PAGE = Template(
    '<!doctype html><html><head><meta charset="utf-8"><title>Widget</title></head><body>'
    '{% include "storage/_upload_widget.html" with purpose=purpose max_size=max_size '
    'log_id="storage-log" %}<pre id="storage-log"></pre></body></html>')


def widget_page(request):
    if not (request.user.is_authenticated and request.user.is_staff):
        raise Http404
    context = RequestContext(request, {'purpose': STAFF_PURPOSE,
                                       'max_size': purposes.get(STAFF_PURPOSE).max_size})
    return HttpResponse(WIDGET_PAGE.render(context))


urlpatterns = list(site_urlpatterns) + i18n_patterns(
    path('storage/test-widget/', widget_page),
    prefix_default_language=True,
)
