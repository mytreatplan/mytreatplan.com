"""Root URLconf for tests: the real one plus the upload spike's routes, as
UPLOAD_SPIKE_ENABLED=True serves them, without reloading mytreatplan.urls."""
from django.conf.urls.i18n import i18n_patterns

from mytreatplan.urls import upload_spike_patterns
from mytreatplan.urls import urlpatterns as site_urlpatterns

urlpatterns = list(site_urlpatterns) + i18n_patterns(
    *upload_spike_patterns,
    prefix_default_language=True,
)
