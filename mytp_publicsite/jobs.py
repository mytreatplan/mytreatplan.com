"""Scheduled jobs declared in deploy/jobs.conf (story 1.9).

systemd timers run them (deploy/provision.sh installs one per line); `manage.py runjob
<name>` runs one and reports Sentry cron check-ins. The schedule is a systemd OnCalendar
expression; `oncalendar_to_crontab` maps the supported subset to the crontab the Sentry
monitor is created with, so the two never drift apart.
"""
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

JOBS_FILE = Path(settings.BASE_DIR) / 'deploy' / 'jobs.conf'
# The copy deploy/provision.sh installed along with the timers. On the server the
# schedules come from it, so the Sentry monitors always match what the timers do,
# even if a release ships a jobs.conf that hasn't been provisioned yet.
INSTALLED_JOBS_FILE = Path('/srv/mytp/shared/jobs.conf')
NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]*$')

_DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
_ONCALENDAR_RE = re.compile(
    r'^(?:(?P<dow>[A-Za-z.,]+) )?'
    r'\*-\*-(?P<dom>\*|\d{1,2}) '
    r'(?P<hour>\*|\d{1,2}):(?P<minute>\d{1,2})(?::00)? UTC$')


@dataclass(frozen=True)
class Job:
    name: str
    command: str
    args: tuple
    schedule: str

    @property
    def crontab(self):
        return oncalendar_to_crontab(self.schedule)


def _cron_dow(spec):
    """'Mon', 'Mon,Wed' or 'Mon..Fri' -> cron day-of-week (0 = Sunday)."""
    def num(day):
        if day not in _DAYS:
            raise ValueError(f'unknown weekday {day!r}')
        return str((_DAYS.index(day) + 1) % 7)
    parts = []
    for item in spec.split(','):
        if '..' in item:
            first, last = item.split('..', 1)
            if last not in _DAYS or first not in _DAYS or _DAYS.index(first) >= _DAYS.index(last) \
                    or last == 'Sun':
                raise ValueError(
                    f'weekday range {item!r} must run forward within Mon..Sat; list Sunday '
                    f'separately, e.g. `Sat,Sun` or `Mon..Sat,Sun`')
            parts.append(f'{num(first)}-{num(last)}')
        else:
            parts.append(num(item))
    return ','.join(parts)


def oncalendar_to_crontab(schedule):
    """Map the supported OnCalendar subset to a 5-field crontab (UTC).

    Raises ValueError for anything else, so an unsupported schedule fails the tests
    instead of silently creating a wrong Sentry monitor.
    """
    match = _ONCALENDAR_RE.match(schedule.strip())
    if not match:
        raise ValueError(
            f'unsupported schedule {schedule!r}; use `[Mon..Fri ]*-*-<day|*> <hour|*>:<minute> UTC`')
    minute = int(match['minute'])
    hour = match['hour']
    dom = match['dom']
    if not 0 <= minute <= 59:
        raise ValueError(f'minute out of range in {schedule!r}')
    if hour != '*' and not 0 <= int(hour) <= 23:
        raise ValueError(f'hour out of range in {schedule!r}')
    if dom != '*' and not 1 <= int(dom) <= 28:
        raise ValueError(f'day of month must be 1-28 in {schedule!r}')
    dow = _cron_dow(match['dow']) if match['dow'] else '*'
    hour = hour if hour == '*' else str(int(hour))
    dom = dom if dom == '*' else str(int(dom))
    return f'{minute} {hour} {dom} * {dow}'


def parse_jobs(text):
    """Parse jobs.conf text into {name: Job}. Raises ValueError on a bad line."""
    jobs = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        fields = [f.strip() for f in line.split('|')]
        if len(fields) != 3 or not all(fields):
            raise ValueError(f'jobs.conf line {number}: expected `name | command | schedule`')
        name, command_line, schedule = fields
        if not NAME_RE.match(name):
            raise ValueError(f'jobs.conf line {number}: invalid job name {name!r}')
        if name in jobs:
            raise ValueError(f'jobs.conf line {number}: duplicate job {name!r}')
        oncalendar_to_crontab(schedule)
        command, *args = shlex.split(command_line)
        jobs[name] = Job(name, command, tuple(args), schedule)
    return jobs


def jobs_file():
    """The provisioned jobs.conf when it exists (the server), else the release's."""
    return INSTALLED_JOBS_FILE if INSTALLED_JOBS_FILE.is_file() else JOBS_FILE


def load_jobs(path=None):
    return parse_jobs(Path(path or jobs_file()).read_text())


def monitor_slug(name, environment=None):
    return f'mytp-{environment or settings.SENTRY_ENVIRONMENT}-{name}'
