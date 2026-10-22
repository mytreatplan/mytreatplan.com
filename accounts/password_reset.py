"""The password reset (epic-login entry 10, CAP-2): Django's flow, adapted.

The request page (`RequestView`, URL name `password_reset`, opened by signup's recovery
note) checks the CAPTCHA (signup.captcha, as the login), then the email, then one of the
IP's hourly reset requests (signup.limits RESET_REQUESTS, decision 1). Whatever follows,
it answers with the same "check your email" page (`SentView`): the mail goes only to an
active account with a usable password, found through the email's blind index
(`UserManager.by_email`), at most MAILS_PER_HOUR an hour per email (counted by its blind
index in the database cache, decision 1); otherwise nothing is sent. The request does the
same work for any email (the lookup and one record); the per-email count, the token, the
rendering (plain text, decision 3, in the request page's language) and the send through
the default mailer (epic-login entry 9) run in a background job (`mail_job`), so the
answer and its timing do not depend on the account; a failure there is reported to Sentry
without values.

The link is SITE_URL + `/<lang>/reset/#<uidb64>.<token>` (never the request's Host;
decision 4), with Django's token: valid PASSWORD_RESET_TIMEOUT (1 hour), and no longer once
the password has changed, so it works once. Browsers never send a fragment, so the uid and
token never reach an access log, a redirect's `next`, a page record or Sentry. The `reset/`
page (`ConfirmView` with `opening`) has a script (static/js/password-reset-link.js) that
reads the fragment, clears it from the address and POSTs it (CSRF); a valid value is kept
in the session and redirects to the new-password page (`password_reset_new`). A used,
expired or tampered value, or one whose account is no longer active, shows the one "no
longer valid" page. No URL route takes the uid or the token. The reset pages never bounce
to login (accounts.sessions RESET_PAGES).

The new password is typed twice and checked as at signup (AUTH_PASSWORD_VALIDATORS, the
breached-password check included), with signup's strength meter. A completed reset ends
every session of the account (accounts.sessions.end_all_sessions) and this browser's own,
logs no one in, forgets the remembered devices (their mark holds the session auth hash,
which the password changes), lifts the account's lock and clears both failure counts
(decision 2), then shows the login page with PASSWORD_CHANGED_MESSAGE.

Recorded in the event log (kind `login`): `password_reset_requested` (tied to the account,
or with the typed email, normalized, as entry 4's unknown emails; `reason` says what
happened), `password_reset_done` (the account), the IP's first block in a window
(`rejected`, field `ip`), a mail over the per-email limit (`rejected`, field `email`, tied
to the account, from the background job) and an unavailable CAPTCHA or breached-password check
(`check_unavailable`). Never the token, the uid or a password.
"""
import logging
import threading
import time

import sentry_sdk
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import password_validation
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import (PasswordResetConfirmView, PasswordResetDoneView,
                                       PasswordResetView)
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.db import connections
from django.forms.utils import ErrorDict
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import translation
from django.utils.decorators import method_decorator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.generic.edit import FormView

from eventlog import service as eventlog
from eventlog.models import Attempt
from eventlog.service import client_ip
from signup import captcha, limits
from signup.forms import BLOCKED_MESSAGE, BLOCKED_RATE_LIMIT, FIELD_CLASSES, StyledForm

from . import login_failures
from .crypto import email_index, normalize_email_for_index
from .password_validation import BREACHED_PASSWORD_CHECK, collect_unavailable_checks
from .sessions import end_all_sessions

logger = logging.getLogger(__name__)

# After a completed reset, on the login page.
PASSWORD_CHANGED_MESSAGE = _('Your password has been changed. Please log in.')
PASSWORD_MISMATCH_MESSAGE = _('The two passwords do not match.')

# The records: their steps, events and reasons.
REQUEST_STEP = 'password-reset'  # the request page
NEW_PASSWORD_STEP = 'password-reset-new'  # the new-password page
REQUESTED_EVENT = 'password_reset_requested'
DONE_EVENT = 'password_reset_done'
IP_BLOCKED_EVENT = 'rejected'
CHECK_UNAVAILABLE_EVENT = 'check_unavailable'
# What a request led to (`reason` of `password_reset_requested`).
MAIL_QUEUED = 'mail_queued'  # an active account: the background job counts and sends
UNKNOWN_EMAIL = 'unknown_email'  # no account (an erased account's email is unknown)
INACTIVE_ACCOUNT = 'inactive_account'  # inactive, or without a usable password
EMAIL_RATE_LIMITED = 'rate_limited_email'  # `rejected`, field `email`, from the job

# The per-email limit (decision 1): mails a window, per email blind index.
MAILS_PER_HOUR = 3
MAIL_WINDOW_SECONDS = 3600
# The per-email counter's lock covers only a read-then-write (as accounts.login_failures).
MUTEX_SECONDS = 5
MUTEX_PAUSE = 0.05
MUTEX_TRIES = 20

# The session's reset link, between the emailed URL and the new-password page.
LINK_SESSION_KEY = 'password_reset_link'

EMAIL_SUBJECT_TEMPLATE = 'accounts/email/password_reset_subject.txt'
EMAIL_BODY_TEMPLATE = 'accounts/email/password_reset.txt'


def _now():
    """The current time (epoch seconds); a function so tests can move it."""
    return time.time()


def _sleep(seconds):
    time.sleep(seconds)


def _report(action, error):
    """Report a failed step (a counter, the lookup, the send) without any personal value:
    the action and the error class only."""
    message = f'Password reset error: {action} ({type(error).__name__})'
    logger.warning(message)
    with sentry_sdk.new_scope() as scope:
        scope.set_tag('password_reset.action', action)
        sentry_sdk.capture_message(message, level='error')


# --- The per-email limit (decision 1) ---------------------------------------------------

class CounterBusy(Exception):
    """The per-email counter's lock was not got within the wait."""


def _mail_key(email):
    return f'password-reset:mails:{email_index(email)}'


def reserve_mail(email):
    """Count one mail to `email` in the last MAIL_WINDOW_SECONDS, if under MAILS_PER_HOUR.
    Returns whether the mail may go. The times are kept by the email's blind index."""
    key = _mail_key(email)
    for attempt in range(MUTEX_TRIES + 1):
        if cache.add(key + ':mutex', 1, timeout=MUTEX_SECONDS):
            break
        if attempt < MUTEX_TRIES:
            _sleep(MUTEX_PAUSE)
    else:
        raise CounterBusy(key)
    try:
        now = _now()
        times = [t for t in (cache.get(key) or []) if now - t < MAIL_WINDOW_SECONDS]
        if len(times) >= MAILS_PER_HOUR:
            return False
        cache.set(key, times + [now], timeout=MAIL_WINDOW_SECONDS + 60)
        return True
    finally:
        cache.delete(key + ':mutex')


# --- The mail -----------------------------------------------------------------------------

# The longest fragment value accepted (a uid and a token are far shorter).
MAX_LINK_LENGTH = 200


def reset_url(user, site_url):
    """The emailed link, in the active language: `site_url` (SITE_URL, never the
    request's Host), the reset page, and the uid and token in the fragment (decision 4)."""
    uid = urlsafe_base64_encode(force_bytes(get_user_model()._meta.pk.value_to_string(user)))
    token = default_token_generator.make_token(user)
    return f"{site_url}{reverse('password_reset_confirm')}#{uid}.{token}"


def parse_link(value):
    """The {'uid', 'token'} of a fragment value `<uidb64>.<token>`, or None."""
    if not isinstance(value, str) or len(value) > MAX_LINK_LENGTH:
        return None
    uid, dot, token = value.partition('.')
    if not (uid and dot and token):
        return None
    return {'uid': uid, 'token': token}


def build_message(user, language, site_url):
    """The plain-text reset mail to `user`, in `language` (decision 3)."""
    with translation.override(language):
        context = {'reset_url': reset_url(user, site_url)}
        subject = ''.join(render_to_string(EMAIL_SUBJECT_TEMPLATE, context).splitlines())
        body = render_to_string(EMAIL_BODY_TEMPLATE, context)
    return EmailMessage(subject.strip(), body, to=[user.email])


def _send(message):
    """Send through the default mailer; a failure is reported without values (the error
    class only: an SMTP error may quote the address)."""
    try:
        message.send()
    except Exception as error:  # noqa: BLE001 - the page has already answered
        _report('send', error)


def mail_job(user, email, language, site_url, request_data):
    """The background part of a request for an active account: the per-email limit
    (over it: recorded as `rejected`, field `email`, and nothing sent), the token and the
    mail, rendered in `language`, then the send. `request_data` is the request's IP and
    user agent, for the record."""
    try:
        allowed = reserve_mail(email)
    except Exception as error:  # noqa: BLE001 - the limit must never stop the mail
        _report('reserve_mail', error)
        allowed = True
    if not allowed:
        eventlog.record_activity(Attempt.LOGIN, IP_BLOCKED_EVENT, account_id=user.pk,
                                 step=REQUEST_STEP, field='email', reason=EMAIL_RATE_LIMITED,
                                 data=dict(request_data))
        return
    try:
        message = build_message(user, language, site_url)
    except Exception as error:  # noqa: BLE001 - reported, never raised in the thread
        _report('build', error)
        return
    _send(message)


def _run_and_close(function):
    try:
        function()
    finally:
        connections.close_all()  # this thread's own database connections


def in_background(function):
    """Run `function` in a daemon thread (the per-email count, the token, the rendering
    and the SMTP exchange never delay the answer); a function so tests can run it inline."""
    thread = threading.Thread(target=_run_and_close, args=(function,), daemon=True,
                              name='password-reset-mail')
    thread.start()
    return thread


# --- The request page ---------------------------------------------------------------------

class ResetRequestForm(PasswordResetForm):
    """The email, styled as signup's fields. `get_users` finds the account through the
    blind index (the email is encrypted), active and with a usable password only."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].widget.attrs.update({
            'class': FIELD_CLASSES, 'autocomplete': 'email', 'autofocus': True})

    def get_users(self, email):
        accounts = get_user_model()._default_manager.by_email(email).filter(is_active=True)
        return (user for user in accounts if user.has_usable_password())

    def refuse(self, code):
        """Only signup's generic BLOCKED_MESSAGE (the CAPTCHA failed): the email is not
        checked."""
        self._errors = ErrorDict()
        self.cleaned_data = {}
        self.add_error(None, ValidationError(BLOCKED_MESSAGE, code=code))


def _ip_blocked(request):
    """Reserve one of the IP's hourly reset requests; True when over the limit (its first
    block in the window is recorded). A limit error lets the request go on."""
    try:
        if limits.ip_reserve(request, limits.RESET_REQUESTS) is not None:
            return False
    except Exception as error:  # noqa: BLE001 - the limit must never break the page
        _report('ip_reserve', error)
        return False
    try:
        first = limits.first_block(request, limits.RESET_REQUESTS)
    except Exception as error:  # noqa: BLE001 - the record must never lift the block
        _report('first_block', error)
        first = False
    if first:
        eventlog.record_activity(Attempt.LOGIN, IP_BLOCKED_EVENT, request=request,
                                 step=REQUEST_STEP, field='ip', reason=BLOCKED_RATE_LIMIT,
                                 data={'counter': limits.RESET_REQUESTS})
    return True


def _record_request(request, reason, account_id=None, email=None):
    data = {} if account_id is not None else {'email': normalize_email_for_index(email)}
    eventlog.record_activity(Attempt.LOGIN, REQUESTED_EVENT, request=request,
                             account_id=account_id, step=REQUEST_STEP, reason=reason,
                             data=data)


def request_reset(request, form):
    """A valid request: the same work for any email (the lookup, one record); for an
    active account, the background job counts, renders and sends the mail."""
    email = form.cleaned_data['email']
    try:
        account = get_user_model()._default_manager.by_email(email).first()
        user = next(iter(form.get_users(email)), None)
    except Exception as error:  # noqa: BLE001 - the answer is the same whatever happens
        _report('lookup', error)
        return
    if user is None:
        if account is None:
            _record_request(request, UNKNOWN_EMAIL, email=email)
        else:
            _record_request(request, INACTIVE_ACCOUNT, account_id=account.pk)
        return
    _record_request(request, MAIL_QUEUED, account_id=user.pk)
    language, site_url = translation.get_language(), settings.SITE_URL
    data = eventlog.request_data(request)
    in_background(lambda: mail_job(user, email, language, site_url, data))


@method_decorator([csrf_protect, never_cache], name='dispatch')
class RequestView(PasswordResetView):
    """The request page: the email and the CAPTCHA; always the same answer."""

    form_class = ResetRequestForm
    template_name = 'accounts/password_reset_form.html'
    success_url = reverse_lazy('password_reset_done')

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), **captcha.widget_context()}

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        result = captcha.verify_turnstile(request.POST.get(captcha.TOKEN_FIELD, ''),
                                          client_ip(request))
        if result == captcha.FAILED:
            form.refuse('captcha_failed')
            return self.form_invalid(form)
        if result == captcha.UNAVAILABLE:
            # The request goes on (as the login: the limits are the guard meanwhile).
            eventlog.record_activity(Attempt.LOGIN, CHECK_UNAVAILABLE_EVENT, request=request,
                                     step=REQUEST_STEP, field='captcha',
                                     reason=captcha.TURNSTILE_CHECK)
        if not form.is_valid():
            return self.form_invalid(form)
        if not _ip_blocked(request):
            request_reset(request, form)
        return HttpResponseRedirect(self.get_success_url())


class SentView(PasswordResetDoneView):
    """The one answer to every request: "check your email"."""

    template_name = 'accounts/password_reset_sent.html'


# --- The new password ---------------------------------------------------------------------

class NewPasswordForm(StyledForm):
    """The new password, twice, checked as at signup (signup.forms.AccountForm): the
    mismatch first, then AUTH_PASSWORD_VALIDATORS for the account. The fields are named as
    signup's, so its strength meter (static/js/signup-account.js) works unchanged. An
    unavailable breached-password check is listed in `unavailable_checks`."""

    password1 = forms.CharField(
        label=_('New password'),
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'autofocus': True}),
        help_text=_('At least 12 characters.'),
    )
    password2 = forms.CharField(
        label=_('Repeat password'),
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        self.unavailable_checks = []
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            self.add_error('password2', forms.ValidationError(
                PASSWORD_MISMATCH_MESSAGE, code='password_mismatch'))
        elif password1:
            with collect_unavailable_checks() as unavailable:
                try:
                    password_validation.validate_password(password1, self.user)
                except forms.ValidationError as error:
                    self.add_error('password1', error)
            if BREACHED_PASSWORD_CHECK in unavailable:
                self.unavailable_checks.append(('password1', BREACHED_PASSWORD_CHECK))
        return cleaned_data

    def save(self):
        self.user.set_password(self.cleaned_data['password1'])
        self.user.save(update_fields=['password'])
        return self.user


def _clear_counters(user):
    """Decision 2: the account's lock and both failure counts go, so the new password logs
    in at once (the lock of its email's blind index too). Reported, never raised."""
    try:
        login_failures.reset(user.pk)
        login_failures.unlock(account_id=user.pk)
        login_failures.unlock(email=user.email)
    except Exception as error:  # noqa: BLE001 - the password is already changed
        _report('clear_counters', error)


def finish_reset(request, user):
    """After the new password is saved: this browser logged out (if it was logged in) and
    every session of the account ended; no login. The counters cleared, the reset
    recorded, and the message for the login page."""
    if request.user.is_authenticated:
        auth_logout(request)  # flushes this session, the reset link with it
    else:
        request.session.pop(LINK_SESSION_KEY, None)
    end_all_sessions(user)
    _clear_counters(user)
    eventlog.record_activity(Attempt.LOGIN, DONE_EVENT, request=request, account_id=user.pk,
                             step=NEW_PASSWORD_STEP)
    messages.success(request, PASSWORD_CHANGED_MESSAGE)


@method_decorator([sensitive_post_parameters('password1', 'password2', 'link'),
                   csrf_protect, never_cache], name='dispatch')
class ConfirmView(PasswordResetConfirmView):
    """With `opening` (the `reset/` page of the emailed link): GET is the page whose
    script posts the fragment; the POST checks it, keeps a valid one in the session and
    redirects to the new-password page, else shows the "no longer valid" page. Without
    (`password-reset/new/`): the form for the session's valid link, else that page."""

    form_class = NewPasswordForm
    template_name = 'accounts/password_reset_confirm.html'
    success_url = reverse_lazy('login')
    opening = False

    def dispatch(self, request, *args, **kwargs):
        if self.opening:
            if request.method != 'POST':
                return render(request, 'accounts/password_reset_link.html')
            link = parse_link(request.POST.get('link', ''))
            if link is not None and self._link_user(link) is not None:
                request.session[LINK_SESSION_KEY] = link
                return HttpResponseRedirect(reverse('password_reset_new'))
            request.session.pop(LINK_SESSION_KEY, None)
            self.user, self.validlink = None, False
            return self.render_to_response(self.get_context_data())
        self.user = self._link_user(request.session.get(LINK_SESSION_KEY))
        self.validlink = self.user is not None
        if not self.validlink:
            request.session.pop(LINK_SESSION_KEY, None)
            return self.render_to_response(self.get_context_data())
        # FormView's dispatch (get, post), past Django's own, which needs the URL's token.
        return FormView.dispatch(self, request, *args, **kwargs)

    def _link_user(self, link):
        """The account of a valid link: an active one, whose token still checks (within
        PASSWORD_RESET_TIMEOUT, and before the password changed); else None."""
        if not isinstance(link, dict):
            return None
        user = self.get_user(str(link.get('uid', '')))
        if (user is None or not user.is_active or not user.has_usable_password()
                or not self.token_generator.check_token(user, link.get('token'))):
            return None
        return user

    def form_valid(self, form):
        user = form.save()
        for field, check in form.unavailable_checks:
            eventlog.record_activity(Attempt.LOGIN, CHECK_UNAVAILABLE_EVENT,
                                     request=self.request, account_id=user.pk,
                                     step=NEW_PASSWORD_STEP, field=field, reason=check)
        finish_reset(self.request, user)
        return HttpResponseRedirect(self.get_success_url())
