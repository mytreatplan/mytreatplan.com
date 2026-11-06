"""Root URLconf for tests: the real one plus the sample page's routes, as
STORAGE_SAMPLE_UPLOADS=True serves them, without reloading mytreatplan.urls."""
from django.conf.urls.i18n import i18n_patterns
from django.urls import include, path

from mytreatplan.urls import urlpatterns as site_urlpatterns
from storage.urls import sample_urlpatterns

urlpatterns = list(site_urlpatterns) + i18n_patterns(
    path('storage/sample/', include(sample_urlpatterns)),
    prefix_default_language=True,
)
