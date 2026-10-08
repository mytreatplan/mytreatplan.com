"""Root URLconf for tests: the real one plus stand-in `signup` and `login` routes, as the
portal epics will add them. Lets the tests see the nav's Sign up / Login links appear."""
from django.conf.urls.i18n import i18n_patterns
from django.http import HttpResponse
from django.urls import path

from mytreatplan.urls import urlpatterns as site_urlpatterns


def _stub(request):
    return HttpResponse('stub')


urlpatterns = list(site_urlpatterns) + i18n_patterns(
    path('signup/', _stub, name='signup'),
    path('login/', _stub, name='login'),
    prefix_default_language=True,
)
