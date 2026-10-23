"""Page-request records (epic-login entry 7, CAP-4): every full page of the registered area
shown to a logged-in user is an event-log record of kind `page`
(eventlog.service.record_activity), tied to the account's pk.

Recorded: a GET by an authenticated user of a portal page (`is_portal_path`: the routes of
signup.urls and accounts.urls, under a language prefix, and the admin, QA A4) answered
with a `text/html` page, status 200 or an error status (4xx/5xx, decision 3), admin pages
included (decision 1). The public site (mytp_publicsite) is never recorded; a path that
resolves to no view (a 404) is recorded only under a portal prefix. htmx full-page loads
(boosted navigation, history restores) count as pages (decision 4). Not recorded: htmx
partial updates (`HX-Request` without `HX-Boosted` or `HX-History-Restore-Request`),
static files, redirects and other statuses, other methods, streamed or non-HTML
responses, anonymous visitors, and requests whose session the session limit
(accounts.sessions) ended.

A record is its event `page_view`: the URL name in `step` (with its namespace, e.g.
`admin:index`; empty when nothing resolves), the status in `reason`, and, encrypted in its
data only, the full path with its query string (decision 2), the client IP and the user
agent. Never the request body.

The middleware wraps every other one but RobotsNoindexMiddleware, SessionLimitMiddleware
included, so it sees the final response (a 404 that LocaleMiddleware or CommonMiddleware
turns into a redirect is not recorded) and the user as the session limit left it: logged
out (anonymous) when it ended the session, before the view or during it. Recording never
changes the response nor breaks the page: a failure is reported as the event log does,
without values.
"""
from django.conf import settings
from django.urls import Resolver404, resolve

from eventlog.models import Attempt
from eventlog.service import record_activity, report_failure

PAGE_VIEW = 'page_view'
STEP_MAX_LENGTH = 50

# The portal's paths (QA A4, decision 1), after the language prefix: the first segments of
# signup.urls and accounts.urls (a new portal route needs its prefix here; a test checks
# they match). The admin sits at the root, outside the language prefixes.
PORTAL_PREFIXES = ('signup/', 'login', 'logout/', 'welcome/', 'password-reset/', 'reset/')
ADMIN_PREFIX = '/admin/'


def _is_static(path):
    """Whether `path` is a static or media file's URL. A prefix of `/` alone is no file
    prefix (an unset MEDIA_URL reads as the script prefix, `/`)."""
    for prefix in (settings.STATIC_URL, settings.MEDIA_URL):
        if prefix and prefix.startswith('/') and prefix != '/' and path.startswith(prefix):
            return True
    return False


def _is_html(response):
    content_type = response.get('Content-Type', '')
    return content_type.split(';', 1)[0].strip().lower() == 'text/html'


def _is_partial(request):
    """An htmx partial update: `HX-Request`, but not a boosted page nor a history restore,
    which replace the whole page (decision 4)."""
    headers = request.headers
    return bool(headers.get('HX-Request')
                and not headers.get('HX-Boosted')
                and not headers.get('HX-History-Restore-Request'))


def is_portal_path(path):
    """Whether `path` (a request's `path_info`) is the portal's: the admin, or one of the
    PORTAL_PREFIXES after a language prefix. Resolved or not (a 404 under a portal prefix
    counts); the public site's pages never."""
    if path.startswith(ADMIN_PREFIX):
        return True
    language, _, rest = path[1:].partition('/')
    if not path.startswith('/') or language not in dict(settings.LANGUAGES):
        return False
    return rest.startswith(PORTAL_PREFIXES)


def is_recorded(request, response):
    """Whether this request and its response are a full portal page shown to a logged-in
    user."""
    status = response.status_code
    user = getattr(request, 'user', None)  # None when an outer middleware answered
    return (request.method == 'GET'
            and user is not None and user.is_authenticated
            and not _is_partial(request)
            and not _is_static(request.path)
            and is_portal_path(request.path_info)
            and (status == 200 or status >= 400)
            and not getattr(response, 'streaming', False)
            and _is_html(response))


def url_name(request):
    """The page's URL name, with its namespaces (not personal); '' when none resolves."""
    match = getattr(request, 'resolver_match', None)
    if match is None:
        try:
            match = resolve(request.path_info)
        except Resolver404:
            return ''
    if not match.url_name:
        return ''
    return ':'.join([*match.namespaces, match.url_name])[:STEP_MAX_LENGTH]


def record_page(request, response):
    """The `page` record of this page view (record_activity never raises)."""
    return record_activity(
        Attempt.PAGE, PAGE_VIEW, request=request, account_id=request.user.pk,
        step=url_name(request), reason=str(response.status_code),
        data={'path': request.get_full_path()})


class PageRecordMiddleware:
    """Records each full page shown to a logged-in user (see the module docstring). Second
    in MIDDLEWARE, so it runs after all the others on the response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            if is_recorded(request, response):
                record_page(request, response)
        except Exception as error:  # noqa: BLE001 - recording never breaks the page
            report_failure('record_page', error, PAGE_VIEW)
        return response
