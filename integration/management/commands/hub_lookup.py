"""Run a hub lookup against the admin site and print its answer (epic-admin-interface
entry 7).

    python manage.py hub_lookup sample

`sample` is the staging-only sample availability (GET /api/v1/hub/sample-availability,
served by the admin site only with its INTERFACE_SAMPLE_LOOKUPS on). The answer is printed
as JSON and stored nowhere. A failure prints one line, ``hub_lookup sample: unavailable
(network)`` or ``refused (http_404_not_found)``, and exits non-zero, without a traceback.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from integration.hub import HubLookupFailed, lookup
from mytp_core.contracts import SampleAvailability
from mytp_core.contracts.hub import SAMPLE_AVAILABILITY_PATH

# The lookups this command runs: name -> (path, reader).
LOOKUPS = {'sample': (SAMPLE_AVAILABILITY_PATH, SampleAvailability)}


class Command(BaseCommand):
    help = 'Run a hub lookup against the admin site and print its answer.'

    def add_arguments(self, parser):
        parser.add_argument('name', choices=sorted(LOOKUPS), help='The lookup to run.')

    def handle(self, *args, name, **options):
        path, reader = LOOKUPS[name]
        try:
            answer = lookup(path, reader)
        except HubLookupFailed as exc:
            raise CommandError(f'hub_lookup {name}: {exc}') from None
        self.stdout.write(json.dumps(answer.model_dump(mode='json'), sort_keys=True))
