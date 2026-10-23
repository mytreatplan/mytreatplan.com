"""The contact form (story 1.5): enviar.php's anti-spam checks and limits, the stored
message and the mail to the company inbox.

A POST (mytp_publicsite.views.contact, htmx) goes through, in order:

1. The bot checks before validation (`dropped_before_validation`): the honeypot `website`
   filled in, the JS flag `cf_js` not "1" (set by static/js/contact-form.js), or the form
   sent less than MIN_FILL_SECONDS or more than MAX_FILL_SECONDS after it was rendered.
   The render time is a signed value in the hidden `cf_ts` (`make_stamp`), so the
   client's clock doesn't matter; a missing or tampered one is a bot case too.
2. The form (mytp_publicsite.forms.ContactForm): a real person's errors show next to
   their fields.
3. A URL pattern in the posted name or country fields (`has_link`, checked before the
   form, so a bot case whatever its other errors).
4. The limits (`reserve`): IP_LIMIT accepted messages per IP and GLOBAL_LIMIT overall, in
   sliding windows of WINDOW_SECONDS in the database cache, under a short lock
   (accounts.cache_mutex). The IP is kept as an HMAC with SECRET_KEY (signup.limits),
   never plain. Over a limit: the generic "try again later" error; the first block per
   IP and hour is reported (which limit only). A lock not got within the wait blocks too
   (not reported); any other cache failure lets the request pass.

Bot cases get the same thank-you as a real message, with nothing stored or sent. An
accepted message is stored (`accept`), and once its row is committed a background job
(`in_background`) mails it to CONTACT_INBOX (`build_message`: plain text, from
DEFAULT_FROM_EMAIL, Reply-To the sender) through the default mailer (epic-login entry 9),
and stores the outcome on the row (`MailStatus`). A failed send is reported to Sentry
without values; the message stays in the admin.

Nothing here logs or reports a message's values.
"""
import logging
import re
import threading
import time
from datetime import timezone as dt_timezone

import sentry_sdk
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.db import connections, transaction

from accounts.cache_mutex import CounterBusy, cache_mutex
from accounts.login_guard import report_error
from eventlog.service import client_ip
from signup.limits import _ip_hash

from .forms import clean_text
from .models import ContactMessage, MailStatus

logger = logging.getLogger(__name__)

# The hidden anti-spam fields.
HONEYPOT_FIELD = 'website'
JS_FIELD = 'cf_js'
STAMP_FIELD = 'cf_ts'
STAMP_SALT = 'mytp_publicsite.contact.rendered'
MIN_FILL_SECONDS = 3
MAX_FILL_SECONDS = 6 * 3600

# A URL where a name or a country has none: spam.
LINK_PATTERN = re.compile(r'(https?://|www\.|\[url|<a\s)', re.IGNORECASE)
LINK_CHECKED_FIELDS = ('first_name', 'middle_name', 'surname', 'country')

# The limits: accepted messages per window.
IP_LIMIT = 5
GLOBAL_LIMIT = 60
WINDOW_SECONDS = 3600
IP_BLOCKED = 'ip'
GLOBAL_BLOCKED = 'global'
GLOBAL_KEY = 'contact:messages:all'
# The lock covers only a read-then-write (as accounts.password_reset's).
MUTEX_SECONDS = 5
MUTEX_PAUSE = 0.05
MUTEX_TRIES = 20

# The mail: the field labels, in the form's order (as enviar.php's).
MAIL_LABELS = (
    ('first_name', 'First name'),
    ('middle_name', 'Middle name / initial'),
    ('surname', 'Surname'),
    ('country', 'Country'),
    ('phone', 'Phone'),
    ('email', 'Email'),
    ('aligners', 'Interested in aligners?'),
    ('club', 'Wants to know about the UAE club?'),
    ('decision', 'Makes purchasing decisions?'),
)


def _now():
    """The current time (epoch seconds); a function so tests can move it."""
    return time.time()


def _sleep(seconds):
    time.sleep(seconds)


def _report(action, error):
    """Report a failed step (the limits, the send) without any value: the action and the
    error class only."""
    report_error(logger, 'Contact form error', 'contact.action', action, error)


# --- The bot checks ---------------------------------------------------------------------

def make_stamp(at=None):
    """The signed render time for `cf_ts`."""
    return signing.dumps(_now() if at is None else at, salt=STAMP_SALT)


def stamp_age(value):
    """Seconds since the signed render time `value`, or None when it is missing or
    tampered with."""
    try:
        rendered = signing.loads(value or '', salt=STAMP_SALT)
    except signing.BadSignature:
        return None
    if isinstance(rendered, bool) or not isinstance(rendered, (int, float)):
        return None
    return _now() - rendered


def valid_stamp(value):
    """`value` if it is a render time within the fill window, else None."""
    age = stamp_age(value)
    if age is None or age < MIN_FILL_SECONDS or age > MAX_FILL_SECONDS:
        return None
    return value


def dropped_before_validation(data):
    """The honeypot, the JS flag and the fill time: True for a bot."""
    if (data.get(HONEYPOT_FIELD) or '').strip():
        return True
    if data.get(JS_FIELD) != '1':
        return True
    return valid_stamp(data.get(STAMP_FIELD)) is None


def has_link(data):
    """A URL pattern in a posted name or the country (cleaned as the form does, before
    validation, so other errors don't matter): True for a bot."""
    return any(LINK_PATTERN.search(clean_text(data.get(name) or ''))
               for name in LINK_CHECKED_FIELDS)


# --- The limits -------------------------------------------------------------------------

def _ip_key(request):
    return f'contact:messages:ip:{_ip_hash(client_ip(request))}'


def _recent(key, now):
    return [t for t in (cache.get(key) or []) if now - t < WINDOW_SECONDS]


def _mutex(key):
    return cache_mutex(key, seconds=MUTEX_SECONDS, pause=MUTEX_PAUSE, tries=MUTEX_TRIES,
                       sleep=_sleep)


def _count(request):
    """Count one accepted message for the request's IP and overall, if under both limits.
    None when counted, else the limit that blocks (IP_BLOCKED or GLOBAL_BLOCKED)."""
    ip_key = _ip_key(request)
    with _mutex(ip_key), _mutex(GLOBAL_KEY):
        now = _now()
        ip_times, all_times = _recent(ip_key, now), _recent(GLOBAL_KEY, now)
        if len(ip_times) >= IP_LIMIT:
            return IP_BLOCKED
        if len(all_times) >= GLOBAL_LIMIT:
            return GLOBAL_BLOCKED
        cache.set(ip_key, ip_times + [now], timeout=WINDOW_SECONDS + 60)
        cache.set(GLOBAL_KEY, all_times + [now], timeout=WINDOW_SECONDS + 60)
        return None


def _first_block(request, limit):
    """True for the first block of this IP and limit in this hour."""
    window = int(_now() // WINDOW_SECONDS)
    return cache.add(f'{_ip_key(request)}:blocked:{limit}:{window}', 1,
                     timeout=WINDOW_SECONDS + 60)


def _report_block(limit):
    message = f'Contact form rate limit: {limit}'
    logger.warning(message)
    with sentry_sdk.new_scope() as scope:
        scope.set_tag('contact.limit', limit)
        sentry_sdk.capture_message(message, level='warning')


def reserve(request):
    """Whether the message may be accepted under the limits (it is then counted). The
    first block per IP and hour is reported; a cache failure lets the message pass."""
    try:
        blocked = _count(request)
    except CounterBusy:
        # A lock not got within the wait is not a cache failure: blocked, as
        # signup.limits.ip_reserve, so the limits hold under load. Not reported.
        return False
    except Exception as error:  # noqa: BLE001 - the limits must never lose a message
        _report('reserve', error)
        return True
    if blocked is None:
        return True
    try:
        if _first_block(request, blocked):
            _report_block(blocked)
    except Exception as error:  # noqa: BLE001 - the answer stays the same
        _report('first_block', error)
    return False


# --- The message and its mail -----------------------------------------------------------

def build_message(message):
    """The plain-text mail of a stored message to CONTACT_INBOX."""
    name = f'{message.first_name} {message.surname}'.strip()
    lines = [f'{label}: {getattr(message, field) or "-"}' for field, label in MAIL_LABELS]
    sent = message.sent_at.astimezone(dt_timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    lines += ['', f'Sent from mytreatplan.com on {sent} UTC']
    return EmailMessage(subject=f'New contact from the website: {name}',
                        body='\n'.join(lines) + '\n',
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        to=[settings.CONTACT_INBOX], reply_to=[message.email])


def mail_job(pk):
    """The background part: build and send the mail of message `pk`, and store the
    outcome. A failure is reported without values (an SMTP error may quote an address)."""
    status = MailStatus.FAILED
    try:
        message = ContactMessage.objects.get(pk=pk)
        if not settings.CONTACT_INBOX:
            raise ValueError('CONTACT_INBOX is empty')
        build_message(message).send()
        status = MailStatus.SENT
    except Exception as error:  # noqa: BLE001 - reported, never raised in the thread
        _report('send', error)
    try:
        ContactMessage.objects.filter(pk=pk).update(mail_status=status)
    except Exception as error:  # noqa: BLE001
        _report('mail_status', error)


def _run_and_close(function):
    try:
        function()
    finally:
        connections.close_all()  # this thread's own database connections


def in_background(function):
    """Run `function` in a daemon thread (the SMTP exchange never delays the answer); a
    function so tests can run it inline (as accounts.password_reset's)."""
    thread = threading.Thread(target=_run_and_close, args=(function,), daemon=True,
                              name='contact-mail')
    thread.start()
    return thread


def accept(cleaned_data):
    """Store an accepted message; its mail goes once the row is committed."""
    message = ContactMessage.objects.create(
        **{field: cleaned_data.get(field) or '' for field, _label in MAIL_LABELS})
    pk = message.pk
    transaction.on_commit(lambda: in_background(lambda: mail_job(pk)))
    return message
