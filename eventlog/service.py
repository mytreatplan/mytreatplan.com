"""The event log API: `start_attempt`, `record` and `end_attempt` (plus `get_attempt`), and
`record_activity`, one self-contained record tied to an account (login and password reset
records, epic-login entries 4 and 10; page records, entry 7).

Every signup step and every login record logs through it. Logging never breaks the flow it
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


def report_failure(action, error, event=''):
    """Report a failed log write (or a failed step around one, such as a page record's
    checks) without any personal value: the action, the event type and the error class."""
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
    deploy/nginx/site.conf.template), else REMOTE_ADDR."""
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
        report_failure('get_attempt', error)
        return None


def start_attempt(kind, *, account_id=None):
    """A new open attempt of `kind` (tied to the account `account_id`, a user pk, if
    given), or None if it could not be written."""
    try:
        with transaction.atomic():
            return Attempt.objects.create(kind=kind, account_id=account_id)
    except Exception as error:  # noqa: BLE001
        report_failure('start_attempt', error, kind)
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
        report_failure('record', error, event)
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
        report_failure('end_attempt', error, outcome)
        return
    if isinstance(attempt, Attempt):
        attempt.ended_at, attempt.outcome = now, outcome


def record_activity(kind, event, *, request=None, account_id=None, step='', field='',
                    reason='', data=None):
    """One record of `kind` on its own: an attempt (tied to `account_id`, a user pk, or to
    no account), its one `event` (as `record`: `data` encrypted, plus the client IP and
    user agent with `request`), ended at once with `event` as its outcome. Returns the
    Event, or None; never raises. A failed event write leaves nothing: the attempt is
    removed again, so no record without its event remains."""
    attempt = start_attempt(kind, account_id=account_id)
    if attempt is None:
        return None
    entry = record(attempt, event, request=request, step=step, field=field, reason=reason,
                   data=data)
    if entry is None:
        try:
            with transaction.atomic():
                Attempt.objects.filter(pk=attempt.pk).delete()
        except Exception as error:  # noqa: BLE001
            report_failure('record_activity', error, event)
        return None
    end_attempt(attempt, event)
    return entry
