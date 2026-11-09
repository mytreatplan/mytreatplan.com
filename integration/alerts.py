"""The interface's health check (epic-admin-interface entry 10, interface I4 and I5): the
`checkinterface` job every 5 minutes (`manage.py check_interface`).

Two kinds, each with its threshold in minutes (settings, checked by integration.E008 and
integration.E009):

- `outbox`: the oldest pending outbox event was recorded more than OUTBOX_ALERT_MINUTES
  (15) ago: the admin site is not taking the events, or the dispatcher has stopped;
- `config`: the last good configuration pull is more than CONFIG_ALERT_MINUTES (60) old
  (or, before the first good one, the first attempt): the admin site is not answering,
  or the `pullconfig` job has stopped. A site that never tried a pull (no ConfigState
  row) is `not_checked` at first: the first such run records its time in its own
  InterfaceAlert row (`since`; ConfigState is never written here), and once that is
  older than CONFIG_ALERT_MINUTES it warns once like a stale pull.
  When the pull has already sent its own outage warning (ConfigState.warned_at set), the
  check sends none (`still_failing`), so one outage does not warn twice.

One Sentry warning per outage per kind, marked in InterfaceAlert.warned_at (saved before
it is sent, after the commit), reset by the first run that finds that kind healthy again,
so the next outage warns again. A warning carries counts and ages only: never a payload,
an event or aggregate id, a URL or an error text. The check only reads the outbox and the
pull's state: it never sends, retries or changes an event, and never pulls. The outbox's
own warning (one per event failing 24 hours) and the pull's (no good pull for an hour,
with its error code) stay as they are.
"""
import logging
from dataclasses import dataclass
from functools import partial

import sentry_sdk
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Min
from django.utils import timezone

from .models import ConfigState, InterfaceAlert, OutboxEvent

logger = logging.getLogger('integration.alerts')

OUTBOX = InterfaceAlert.OUTBOX
CONFIG = InterfaceAlert.CONFIG
DEFAULT_MINUTES = {OUTBOX: 15, CONFIG: 60}
SETTING_NAMES = {OUTBOX: 'OUTBOX_ALERT_MINUTES', CONFIG: 'CONFIG_ALERT_MINUTES'}

OK = 'ok'
WARNED = 'warned'
STILL_FAILING = 'still_failing'
RECOVERED = 'recovered'
NOT_CHECKED = 'not_checked'


@dataclass
class Finding:
    kind: str
    unhealthy: bool | None  # None: never pulled (config only), decided in _settle
    summary: str            # counts and ages only
    outcome: str = OK
    # config: the pull's own outage warning was already sent (ConfigState.warned_at).
    already_warned: bool = False


def threshold(kind):
    """The kind's threshold in minutes (its setting; validated by the system check)."""
    return getattr(settings, SETTING_NAMES[kind], DEFAULT_MINUTES[kind])


def _minutes(delta):
    return max(int(delta.total_seconds() // 60), 0)


def outbox_finding(now):
    pending = OutboxEvent.objects.filter(state=OutboxEvent.PENDING).aggregate(
        count=Count('id'), oldest=Min('created_at'))
    count = pending['count']
    if not count:
        return Finding(OUTBOX, False, '0 events pending')
    age = _minutes(now - pending['oldest'])
    return Finding(OUTBOX, age >= threshold(OUTBOX),
                   f'{count} event(s) pending, the oldest for {age} minutes')


def config_finding(now):
    row = ConfigState.objects.filter(pk=ConfigState.SINGLETON).first()
    if row is None:
        return Finding(CONFIG, None, 'no pull tried yet')
    if row.last_success_at is not None:
        age = _minutes(now - row.last_success_at)
        summary = f'last good pull {age} minutes ago'
    else:
        age = _minutes(now - row.created_at)
        summary = f'no good pull yet, first attempt {age} minutes ago'
    return Finding(CONFIG, age >= threshold(CONFIG), summary,
                   already_warned=row.warned_at is not None)


def _message(finding):
    if finding.kind == OUTBOX:
        return (f'Interface outbox not delivering for over {threshold(OUTBOX)} minutes: '
                f'{finding.summary}')
    return (f'Configuration snapshot not pulled for over {threshold(CONFIG)} minutes: '
            f'{finding.summary}')


def _send_warning(kind, message):
    logger.warning(message)
    with sentry_sdk.new_scope() as scope:
        scope.set_tag('interface.alert', kind)
        # One Sentry issue per kind, whatever the counts and ages in the message.
        scope.fingerprint = ['interface-alert', kind]
        sentry_sdk.capture_message(message, level='warning')


def _find_locked(kind):
    return InterfaceAlert.objects.select_for_update().filter(kind=kind).first()


def _locked_alert(kind, create):
    """The kind's state row, locked for this transaction (created when `create`)."""
    row = _find_locked(kind)
    if row is not None or not create:
        return row
    try:
        with transaction.atomic():
            InterfaceAlert.objects.create(kind=kind)
    except IntegrityError:
        pass  # created by another run meanwhile: lock that one
    return InterfaceAlert.objects.select_for_update().get(kind=kind)


def _never_pulled(finding, row, now):
    """No pull tried yet: start the clock in the check's own row; once it is older than
    the threshold the finding is unhealthy. Returns whether it is."""
    if row.since is None:
        row.since = now
        row.save(update_fields=['since'])
    age = _minutes(now - row.since)
    finding.summary = f'no pull tried yet, first seen {age} minutes ago'
    finding.unhealthy = age >= threshold(CONFIG)
    return finding.unhealthy


def _settle(finding, now):
    """Warn once per outage, reset when healthy; returns the outcome."""
    with transaction.atomic():
        never_pulled = finding.unhealthy is None
        row = _locked_alert(finding.kind, create=never_pulled or finding.unhealthy)
        if never_pulled and not _never_pulled(finding, row, now):
            return NOT_CHECKED
        if finding.unhealthy:
            if row.warned_at is not None:
                return STILL_FAILING
            row.warned_at = now
            row.save(update_fields=['warned_at'])
            if finding.already_warned:
                # The pull has sent its own warning for this outage: none from here.
                return STILL_FAILING
            transaction.on_commit(partial(_send_warning, finding.kind, _message(finding)))
            return WARNED
        if row is None or (row.warned_at is None and row.since is None):
            return OK
        was_warned = row.warned_at is not None
        row.warned_at = None
        row.since = None
        row.save(update_fields=['warned_at', 'since'])
        if not was_warned:
            return OK
    logger.info('interface alert cleared: %s (%s)', finding.kind, finding.summary)
    return RECOVERED


def check(now=None):
    """Check both kinds; returns their Findings (outcome set), outbox first."""
    now = now or timezone.now()
    findings = [outbox_finding(now), config_finding(now)]
    for finding in findings:
        finding.outcome = _settle(finding, now)
    return findings
