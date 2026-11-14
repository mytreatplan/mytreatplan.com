"""
URL configuration for mytreatplan project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from mytp_core.views import healthz
from mytp_publicsite.sitemaps import PublicPagesSitemap
from mytp_publicsite.views import robots_txt
from regions.suggestion import stay as region_stay

urlpatterns = [
    path('admin/', admin.site.urls),
    # Crawler files sit at the root, outside the language prefixes.
    path('robots.txt', robots_txt, name='robots_txt'),
    path('sitemap.xml', sitemap, {'sitemaps': {'pages': PublicPagesSitemap}},
         name='django.contrib.sitemaps.views.sitemap'),
    # Uptime probe (mytp-core: `ok`, `site: <SITE>` and `region: <REGION>`, or
    # `unavailable`); Nginx serves it without basic auth. Not in the sitemap.
    path('healthz', healthz, name='healthz'),
    # The region suggestion bar's "Stay here" (regions/suggestion.py): sets the remembered
    # cookie and returns to the page. Outside the language prefixes.
    path('region/stay/', region_stay, name='region_stay'),
    # The interface with the admin site (integration, AD-4): signed calls only, outside the
    # language prefixes, CSRF and basic auth (Nginx's /api/ block).
    path('api/v1/', include('integration.urls')),
    # The upload widget's endpoints (storage.views): logged-in users, CSRF; outside the
    # language prefixes.
    path('storage/', include('storage.urls')),
]

# Portal routes (signup, login, ...). They are served, under the language prefixes, only
# when PORTAL_ENABLED is True; the nav's Sign up / Login links appear once `signup` and
# `login` resolve.
portal_patterns = [
    path('', include('signup.urls')),
    path('', include('accounts.urls')),
    # The upgrade screen (epic-subscriptions entry 2).
    path('', include('subscriptions.urls')),
]


def portal_urlpatterns():
    """The portal routes when PORTAL_ENABLED is on, otherwise none."""
    return list(portal_patterns) if settings.PORTAL_ENABLED else []


# The staging sample uploads page (storage.sample; removed with the sample purpose):
# served under the language prefixes only when STORAGE_SAMPLE_UPLOADS is True. The views
# also answer 404 while the setting is off.
def storage_sample_urlpatterns():
    """The sample uploads page's routes when STORAGE_SAMPLE_UPLOADS is on, otherwise none."""
    from storage.urls import sample_urlpatterns
    if not settings.STORAGE_SAMPLE_UPLOADS:
        return []
    return [path('storage/sample/', include(sample_urlpatterns))]


# Public pages live under /en/ and /es/. A bare / is redirected by LocaleMiddleware
# to the visitor's language (Accept-Language), falling back to English.
urlpatterns += i18n_patterns(
    path('', include('mytp_publicsite.urls')),
    *portal_urlpatterns(),
    *storage_sample_urlpatterns(),
    prefix_default_language=True,
)
