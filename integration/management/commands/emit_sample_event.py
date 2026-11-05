"""Record a staging-only sample event (epic-admin-interface entry 3): proves the outbox
path to the admin site. Removed when entry 8 adds the first real event type.

    python manage.py emit_sample_event <aggregate uuid> <aggregate version> [--label TEXT]
    python manage.py emit_sample_event <aggregate uuid> <version> --region ae   # a wrong region

Refused unless INTERFACE_SAMPLE_EVENTS=True (which the system check integration.E004
refuses on a public site). Records a `sample.recorded` event (payload {"label": ...})
in the outbox and prints its event id; the dispatcher sends it (the nudge task, or the
`dispatchoutbox` job within a minute). `--region` overrides the envelope's region, so
the admin site's region check can be seen to reject it.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from integration import outbox
from mytp_core.contracts import SAMPLE_RECORDED


class Command(BaseCommand):
    help = 'Record a sample.recorded event in the outbox (staging only).'

    def add_arguments(self, parser):
        parser.add_argument('aggregate', help='The aggregate UUID.')
        parser.add_argument('version', type=int, help='The aggregate version (>= 1).')
        parser.add_argument('--label', default='sample', help='The payload label.')
        parser.add_argument('--region', default=None,
                            help='The envelope region (default: this site\'s REGION).')

    def handle(self, *args, aggregate, version, label, region, **options):
        if not getattr(settings, 'INTERFACE_SAMPLE_EVENTS', False):
            raise CommandError('emit_sample_event: INTERFACE_SAMPLE_EVENTS is off.')
        try:
            with transaction.atomic():
                event = outbox.record(SAMPLE_RECORDED, aggregate, version, {'label': label},
                                      region=region)
        except (outbox.OutboxError, ValueError) as exc:
            raise CommandError(f'emit_sample_event: {exc}') from None
        self.stdout.write(f'emit_sample_event: {event.event_id} recorded (aggregate '
                          f'{event.aggregate_id} v{event.aggregate_version} '
                          f'#{event.sequence})')
