"""The region suggestion bar (epic-regions entry 5, R4 / AD-14).

A visitor of the public pages whose country (regions.geoip.request_country) the map
(regions.countries.lookup_country) gives to the other region sees a slim bar that links
to that region's site, with a "Stay here" link. The bar is computed per request, on the
server, by the `{% region_suggestion_bar %}` tag (regions/templatetags/regions_bar.py);
only the home page and the legal pages fill base.html's `region_bar` block with it, so
portal pages never show it. There is no redirect by country.

"Stay here" (and the bar's close link) is a plain GET to `stay`, which sets the
remembered cookie and returns to the page; with the cookie the bar is not shown again.

The checks run cheapest first: the stay cookie, a bot user agent (or none), an unknown
country, then the map (one query). Nothing here logs an IP or a country.
"""
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django_countries import countries

from regions.countries import lookup_country
from regions.geoip import request_country

STAY_COOKIE = 'mytp_stay_region'
STAY_COOKIE_VALUE = '1'
STAY_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year (user, 2026-11-04)

# Crawlers, link previews, headless browsers and HTTP libraries get no bar.
BOT_USER_AGENT = re.compile(
    r'bot|crawl|spider|slurp|facebookexternalhit|preview|headless|python-requests|curl|wget',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Suggestion:
    """What the bar shows: the visitor's country name (in the active language), and the
    other region's site URL (no trailing slash) and host."""
    country: str
    site_url: str
    host: str


def is_bot(request):
    """Whether the request's user agent is empty or looks like a bot."""
    user_agent = (request.META.get('HTTP_USER_AGENT') or '').strip()
    return not user_agent or bool(BOT_USER_AGENT.search(user_agent))


def suggestion_for(request):
    """The bar's Suggestion for `request`, or None when no bar is shown."""
    if STAY_COOKIE in request.COOKIES:
        return None
    if is_bot(request):
        return None
    code = request_country(request)
    if code is None:
        return None
    answer = lookup_country(code)
    if not answer.other_region:
        return None
    return Suggestion(country=str(countries.name(code)), site_url=answer.site_url,
                      host=urlsplit(answer.site_url).netloc)


@never_cache
@require_GET
def stay(request):
    """`/region/stay/?next=<path>`: remember "stay here" for a year and go back to
    `next` when it is a safe URL of this site, otherwise to `/`."""
    next_url = request.GET.get('next', '')
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        next_url = '/'
    response = HttpResponseRedirect(next_url)
    response.set_cookie(
        STAY_COOKIE, STAY_COOKIE_VALUE,
        max_age=STAY_COOKIE_MAX_AGE,
        path='/',
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite='Lax',
    )
    return response
