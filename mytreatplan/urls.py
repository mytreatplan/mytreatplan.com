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

from mytp_publicsite.sitemaps import PublicPagesSitemap
from mytp_publicsite.views import healthz, robots_txt

urlpatterns = [
    path('admin/', admin.site.urls),
    # Crawler files sit at the root, outside the language prefixes.
    path('robots.txt', robots_txt, name='robots_txt'),
    path('sitemap.xml', sitemap, {'sitemaps': {'pages': PublicPagesSitemap}},
         name='django.contrib.sitemaps.views.sitemap'),
    # Uptime probe; Nginx serves it without basic auth. Not in the sitemap.
    path('healthz', healthz, name='healthz'),
]

# Portal routes (signup, login, ...). They are served, under the language prefixes, only
# when PORTAL_ENABLED is True; the nav's Sign up / Login links appear once `signup` and
# `login` resolve.
portal_patterns = [
    path('', include('signup.urls')),
    path('', include('accounts.urls')),
]


def portal_urlpatterns():
    """The portal routes when PORTAL_ENABLED is on, otherwise none."""
    return list(portal_patterns) if settings.PORTAL_ENABLED else []


# Upload spike (epic-platform entry 2; entry 11 deletes it): a staff-only page and its
# signing endpoint, served under the language prefixes only when UPLOAD_SPIKE_ENABLED is
# True (dev only). The views also answer 404 while the setting is off.
upload_spike_patterns = [
    path('upload-spike/', include('upload_spike.urls')),
]


def upload_spike_urlpatterns():
    """The upload spike's routes when UPLOAD_SPIKE_ENABLED is on, otherwise none."""
    return list(upload_spike_patterns) if settings.UPLOAD_SPIKE_ENABLED else []


# Public pages live under /en/ and /es/. A bare / is redirected by LocaleMiddleware
# to the visitor's language (Accept-Language), falling back to English.
urlpatterns += i18n_patterns(
    path('', include('mytp_publicsite.urls')),
    *portal_urlpatterns(),
    *upload_spike_urlpatterns(),
    prefix_default_language=True,
)
