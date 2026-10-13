"""The event log API: `start_attempt`, `record` and `end_attempt` (plus `get_attempt`).

Every signup step logs through it (and, later, login). Logging never breaks the flow it
logs: any failure is caught, reported to Sentry by event type and error class only (no
personal values, no exception text, which may quote them), and the caller carries on.
"""
import logging

import sentry_sdk
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Attempt, Event

logger = logging.getLogger(__name__)


def _report_failure(action, error, event=''):
    """Report a failed log write without any personal value."""
    what = f'{action} {event}'.strip()
    message = f'Event log write failed: {what} ({type(error).__name__})'
    logger.warning(message)
    with sentry_sdk.new_scope() as scope:
        scope.set_tag('eventlog.action', action)
        if event:
            scope.set_tag('eventlog.event', event)
        sentry_sdk.capture_message(message, level='error')


def client_ip(request):
    """The client IP: the last X-Forwarded-For entry (the one our Nginx appended,
    deploy/nginx/mytp.conf.template), else REMOTE_ADDR."""
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    entries = [entry.strip() for entry in forwarded.split(',') if entry.strip()]
    if entries:
        return entries[-1]
    return request.META.get('REMOTE_ADDR', '')


def request_data(request):
    """What every event recorded with a request stores: client IP and user agent."""
    return {'ip': client_ip(request), 'user_agent': request.META.get('HTTP_USER_AGENT', '')}


def get_attempt(pk):
    """The attempt with this id, or None (no id, malformed, gone, or a database error)."""
    if not pk:
        return None
    try:
        return Attempt.objects.filter(pk=pk).first()
    except ValidationError:
        return None
    except Exception as error:  # noqa: BLE001 - logging must never break the flow
        _report_failure('get_attempt', error)
        return None


def start_attempt(kind):
    """A new open attempt of `kind`, or None if it could not be written."""
    try:
        with transaction.atomic():
            return Attempt.objects.create(kind=kind)
    except Exception as error:  # noqa: BLE001
        _report_failure('start_attempt', error, kind)
        return None


def record(attempt, event, *, request=None, step='', field='', reason='', data=None):
    """Append an event to `attempt` (an Attempt or its id; None does nothing).

    `data` holds the personal values (encrypted). With `request`, the client IP and the
    user agent are added to it. `field` and `reason` are names and codes, never values.
    Returns the Event, or None.
    """
    attempt_id = getattr(attempt, 'pk', attempt)
    if not attempt_id:
        return None
    values = dict(data or {})
    if request is not None:
        values.update(request_data(request))
    try:
        with transaction.atomic():
            return Event.objects.create(
                attempt_id=attempt_id, event=event, step=step, field=field or '',
                reason=reason or '', data=values or None)
    except Exception as error:  # noqa: BLE001
        _report_failure('record', error, event)
        return None


def end_attempt(attempt, outcome):
    """Close `attempt` (an Attempt or its id) with `outcome`; the end time is now."""
    attempt_id = getattr(attempt, 'pk', attempt)
    if not attempt_id:
        return
    now = timezone.now()
    try:
        with transaction.atomic():
            Attempt.objects.filter(pk=attempt_id).update(ended_at=now, outcome=outcome)
    except Exception as error:  # noqa: BLE001
        _report_failure('end_attempt', error, outcome)
        return
    if isinstance(attempt, Attempt):
        attempt.ended_at, attempt.outcome = now, outcome
