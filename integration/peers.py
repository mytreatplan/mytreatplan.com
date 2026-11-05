"""The sites this portal calls (INTERFACE_PEERS = `<site>=<base URL>,...`)."""
from urllib.parse import urlsplit

from django.conf import settings

from mytp_core import interface


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
