"""Pull the configuration snapshot from the admin site (epic-admin-interface entry 4).

    python manage.py pull_config

Run every 5 minutes by the `pullconfig` job in deploy/jobs.conf (and, as a task, after
each nudge from the admin site). Verifies the signed snapshot and applies it only when
its version is higher than the applied one (integration.config). Prints the outcome and
version only, never the contents. A refusal or failure (the admin site unreachable, a bad
signature, an older version, ...) keeps the applied snapshot, is recorded as
`last_error` (`show_config`) and prints one line with its code, exiting 0: an outage
gives one Sentry warning (integration.config), not a failed job every 5 minutes. Only a
missing configuration (`not_configured`) fails the job, so its Sentry monitor shows it.
"""
from django.core.management.base import BaseCommand, CommandError

from integration import config


class Command(BaseCommand):
    help = 'Pull, verify and apply the configuration snapshot from the admin site.'

    def handle(self, **options):
        try:
            outcome, version = config.pull()
        except config.PullFailed as exc:
            if exc.code == config.NOT_CONFIGURED:
                raise CommandError(f'pull_config: refused ({exc.code}); the applied '
                                   f'snapshot is kept') from None
            self.stdout.write(f'pull_config: refused ({exc.code}); the applied snapshot '
                              f'is kept')
            return
        self.stdout.write(f'pull_config: v{version} {outcome}')
