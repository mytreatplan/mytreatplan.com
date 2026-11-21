from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class PublicPagesSitemap(Sitemap):
    """The public pages in every language, each listing its hreflang alternates.

    URLs are built from settings.SITE_URL rather than the request host, so a sitemap
    fetched through another hostname still points at the public site.
    """

    i18n = True
    alternates = True

    pages = {
        'publicsite:home': ('monthly', 1.0),
        'publicsite:privacy': ('yearly', 0.3),
        'publicsite:terms': ('yearly', 0.3),
    }

    # Only in regions with country sellers (the legal notice and conditions pages).
    sales_pages = {
        'publicsite:legal_notice': ('yearly', 0.3),
        'publicsite:conditions': ('yearly', 0.3),
    }

    def _all_pages(self):
        if settings.REGION_PROFILE.country_sellers:
            return {**self.pages, **self.sales_pages}
        return self.pages

    def items(self):
        return list(self._all_pages())

    def location(self, item):
        return reverse(item)

    def changefreq(self, item):
        return self._all_pages()[item][0]

    def priority(self, item):
        return self._all_pages()[item][1]

    def get_urls(self, page=1, site=None, protocol=None):
        site_url = urlsplit(settings.SITE_URL)
        return self._urls(page, site_url.scheme, site_url.netloc)
