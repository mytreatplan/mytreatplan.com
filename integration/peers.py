"""The sites this portal calls (INTERFACE_PEERS = `<site>=<base URL>,...`), the admin
site among them (`admin_site_url`), and how a failed call to one is told (`error_code`,
`unavailable`)."""
from urllib.parse import urlsplit

from django.conf import settings

from mytp_core import interface

# The error codes of retried failures that say the admin site is unreachable or
# unavailable (they count towards the outbox's MAX_FAILURES_IN_A_ROW).
NETWORK = 'network'
PEER_URL = 'peer_url'


def parse_peers(value):
    """{site: base URL without a trailing slash}; InterfaceConfigurationError naming the
    entry when one is malformed or empty. A URL must be https://, except with DEBUG on
    (local development), as the signed token would otherwise travel in clear text."""
    peers = {}
    if not (value or '').strip():
        return peers
    schemes = ('https', 'http') if settings.DEBUG else ('https',)
    for position, entry in enumerate(value.split(','), start=1):
        entry = entry.strip()
        if not entry:
            raise interface.InterfaceConfigurationError(
                f'INTERFACE_PEERS: entry {position} is empty.')
        site, sep, url = entry.partition('=')
        site, url = site.strip(), url.strip().rstrip('/')
        parts = urlsplit(url)
        if not sep or parts.scheme not in schemes or not parts.hostname \
                or parts.query or parts.fragment:
            raise interface.InterfaceConfigurationError(
                f'INTERFACE_PEERS: entry {position} is not <site>=https://<host> (http:// '
                f'only with DEBUG on).')
        interface.check_site(site, f'INTERFACE_PEERS: the site of entry {position}')
        peers[site] = url
    return peers


def peer_url(site):
    """The base URL of `site`; InterfaceConfigurationError when it is not listed."""
    peers = parse_peers(getattr(settings, 'INTERFACE_PEERS', '') or '')
    if site not in peers:
        known = ', '.join(sorted(peers)) or 'none'
        raise interface.InterfaceConfigurationError(
            f'{site!r} is not in INTERFACE_PEERS (known: {known}).')
    return peers[site]


def admin_site_url():
    """The admin site's name and base URL (INTERFACE_ADMIN_SITE, or the only
    INTERFACE_PEERS entry); InterfaceConfigurationError when it cannot be told."""
    site = (getattr(settings, 'INTERFACE_ADMIN_SITE', '') or '').strip()
    peers = parse_peers(getattr(settings, 'INTERFACE_PEERS', '') or '')
    if not site:
        if len(peers) != 1:
            raise interface.InterfaceConfigurationError(
                'Set INTERFACE_ADMIN_SITE: INTERFACE_PEERS does not list exactly one site.')
        site = next(iter(peers))
    if site not in peers:
        raise interface.InterfaceConfigurationError(
            f'INTERFACE_ADMIN_SITE {site!r} is not in INTERFACE_PEERS.')
    return site, peers[site]


def error_code(exc):
    """The code of a call failure: `network` only for no answer at all, `peer_url` for a
    peer URL refused before the call, `http_<status>[_<peer code>]` for an HTTP error,
    `bad_answer` for a 2xx answer that is not a JSON object."""
    if exc.status is None:
        return NETWORK if str(exc).startswith('no answer') else PEER_URL
    if 200 <= exc.status < 300:
        return 'bad_answer'
    return f'http_{exc.status}' + (f'_{exc.code}' if exc.code else '')


def unavailable(exc):
    """Whether a call failure says the admin site is unreachable or unavailable (rather
    than refusing this one event)."""
    return exc.status is None or exc.status >= 500 or exc.status == 401
