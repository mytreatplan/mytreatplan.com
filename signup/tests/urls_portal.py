"""Root URLconf for tests: the real one plus the real portal routes, as PORTAL_ENABLED=True
serves them, without reloading mytreatplan.urls."""
from django.conf.urls.i18n import i18n_patterns

from mytreatplan.urls import portal_patterns
from mytreatplan.urls import urlpatterns as site_urlpatterns

urlpatterns = list(site_urlpatterns) + i18n_patterns(
    *portal_patterns,
    prefix_default_language=True,
)
