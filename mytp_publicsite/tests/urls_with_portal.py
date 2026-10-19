"""Root URLconf for tests: the real one plus stand-in `signup`, `login`, `logout` and
`welcome` routes, as the portal epics add them. Lets the tests see the nav's portal links
appear."""
from django.conf.urls.i18n import i18n_patterns
from django.http import HttpResponse
from django.urls import path

from mytreatplan.urls import urlpatterns as site_urlpatterns


def _stub(request):
    return HttpResponse('stub')


urlpatterns = list(site_urlpatterns) + i18n_patterns(
    path('signup/', _stub, name='signup'),
    path('login/', _stub, name='login'),
    path('logout/', _stub, name='logout'),
    path('welcome/', _stub, name='welcome'),
    prefix_default_language=True,
)
