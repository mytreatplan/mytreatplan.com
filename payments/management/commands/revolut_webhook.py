"""Manage the Revolut webhook of this site (epic-payments entry 4), run over SSH by the user.

    python manage.py revolut_webhook register   # SITE_URL/payments/webhook/revolut/
    python manage.py revolut_webhook list
    python manage.py revolut_webhook delete <webhook id>

`register` registers the site's webhook URL for ORDER_COMPLETED, ORDER_FAILED and
ORDER_CANCELLED and prints the signing secret once, in this terminal only, to be typed into
settings.env as REVOLUT_WEBHOOK_SECRET (then restart the site). The secret is never written to
a file or a log. Needs REVOLUT_SECRET_KEY (and REVOLUT_ENVIRONMENT) in settings.env.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from payments import gateways, revolut


class Command(BaseCommand):
    help = 'Register, list or delete the Revolut webhook of this site.'

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='action', required=True)
        sub.add_parser('register')
        sub.add_parser('list')
        delete = sub.add_parser('delete')
        delete.add_argument('webhook_id')

    def handle(self, *args, **options):
        if not revolut.enabled():
            raise CommandError('REVOLUT_SECRET_KEY is not set')
        try:
            getattr(self, f'_{options["action"]}')(options)
        except gateways.GatewayError as error:
            raise CommandError(str(error)) from None

    def _register(self, options):
        url = settings.SITE_URL.rstrip('/') + reverse('payments_webhook', args=[revolut.NAME])
        answer = revolut.request('POST', '/api/webhooks', {
            'url': url, 'events': list(revolut.WEBHOOK_PATH_EVENTS)})
        if not isinstance(answer, dict) or not answer.get('signing_secret'):
            raise CommandError('Revolut gave no signing secret')
        self.stdout.write(f'registered {answer.get("id", "-")}: {url} '
                          f'({revolut.environment()})')
        self.stdout.write('Signing secret, shown once. Type it into settings.env as '
                          'REVOLUT_WEBHOOK_SECRET and restart the site:')
        self.stdout.write(str(answer['signing_secret']))

    def _list(self, options):
        answer = revolut.request('GET', '/api/webhooks')
        rows = answer if isinstance(answer, list) else (answer or {}).get('webhooks', [])
        if not rows:
            self.stdout.write('no webhooks registered')
        for row in rows:
            if isinstance(row, dict):
                self.stdout.write(f'{row.get("id", "-")}  {row.get("url", "-")}  '
                                  f'{",".join(row.get("events") or [])}')

    def _delete(self, options):
        webhook_id = options['webhook_id']
        if not webhook_id.replace('-', '').isalnum():
            raise CommandError('not a webhook id')
        revolut.request('DELETE', f'/api/webhooks/{webhook_id}', ok=(200, 204))
        self.stdout.write(f'deleted {webhook_id}')
