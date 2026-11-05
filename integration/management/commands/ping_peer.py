"""Call a peer site's signed ping (epic-admin-interface entry 1).

    python manage.py ping_peer admindev

Signs GET <peer>/api/v1/ping with this site's key (INTERFACE_SIGNING_KMS_KEY) for the
peer's site name and prints its JSON answer. The peer's address is its INTERFACE_PEERS
entry. Any failure (no signing key, an unknown peer, no answer, a refusal with its code)
prints one line and exits non-zero, without a traceback.
"""
import json
import logging

from django.core.management.base import BaseCommand, CommandError

from integration.peers import peer_url
from mytp_core import interface

logger = logging.getLogger('integration.calls')
PING_PATH = '/api/v1/ping'


class Command(BaseCommand):
    help = "Call a peer site's signed /api/v1/ping and print its answer."

    def add_arguments(self, parser):
        parser.add_argument('site', help='The peer site name, e.g. admindev.')

    def handle(self, *args, site, **options):
        try:
            url = peer_url(site) + PING_PATH
            answer = interface.signed_get(url, audience=site)
        except interface.InterfaceError as exc:
            raise CommandError(f'ping_peer {site}: {exc}') from None
        logger.info('ping_peer %s: HTTP %s', site, answer.status)
        self.stdout.write(json.dumps(answer.data, sort_keys=True))
