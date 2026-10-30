"""Run one scheduled job from deploy/jobs.conf and report it to Sentry Crons.

    python manage.py runjob <name>

Started by deploy/run-job.sh (systemd mytp-<site>-job@<name>.service). When Sentry is on it
sends an `in_progress` check-in, then `ok` or `error`, to the monitor
`mytp-<environment>-<name>` (created or updated from the job's schedule), so a failed
or missed run raises a Sentry alert. A failing job also exits non-zero, which fails
the systemd unit.
"""
import time

import sentry_sdk
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from sentry_sdk.crons import capture_checkin
from sentry_sdk.crons.consts import MonitorStatus

from mytp_publicsite.jobs import load_jobs, monitor_slug

# Minutes Sentry waits past the scheduled time before a check-in counts as missed,
# and the longest a run may stay in progress before it counts as failed.
CHECKIN_MARGIN_MINUTES = 30
MAX_RUNTIME_MINUTES = 60


def monitor_config(job):
    return {
        'schedule': {'type': 'crontab', 'value': job.crontab},
        'timezone': 'UTC',
        'checkin_margin': CHECKIN_MARGIN_MINUTES,
        'max_runtime': MAX_RUNTIME_MINUTES,
        'failure_issue_threshold': 1,
        'recovery_threshold': 1,
    }


class Command(BaseCommand):
    help = 'Run a scheduled job from deploy/jobs.conf, with Sentry cron check-ins.'

    def add_arguments(self, parser):
        parser.add_argument('name', help='job name in deploy/jobs.conf')

    def handle(self, *, name, **options):
        jobs = load_jobs()
        if name not in jobs:
            raise CommandError(f'unknown job {name!r}; jobs.conf has: {", ".join(sorted(jobs))}')
        job = jobs[name]
        monitoring = sentry_sdk.get_client().is_active()
        slug = monitor_slug(job.name)
        check_in_id = None
        if monitoring:
            check_in_id = capture_checkin(
                monitor_slug=slug, status=MonitorStatus.IN_PROGRESS,
                monitor_config=monitor_config(job))

        start = time.monotonic()
        status = MonitorStatus.ERROR
        try:
            call_command(job.command, *job.args)
            status = MonitorStatus.OK
        except SystemExit as exc:
            if exc.code in (0, None):
                status = MonitorStatus.OK
            raise
        except Exception:
            if monitoring:
                sentry_sdk.capture_exception()
            raise
        finally:
            duration = time.monotonic() - start
            if monitoring:
                capture_checkin(
                    monitor_slug=slug, check_in_id=check_in_id, status=status,
                    duration=duration, monitor_config=monitor_config(job))
                sentry_sdk.flush(timeout=10)
            self.stdout.write(f'job {job.name}: {status} in {duration:.1f}s')
