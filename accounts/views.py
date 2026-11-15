"""The registered area's entry and exit: login, logout, the welcome page, and the doctor
area's home and "coming soon" pages (epic-doctor-profile entry 1) and its settings area
(the personal-data page and the sections not built yet, entry 2).

Login checks the CAPTCHA (signup.captcha) first, then the credentials (accounts.forms).
A right password logs no one in: it starts a pending login (`start_pending_login`), and
the second factor (epic-login entry 2, CAP-7) follows on two pages: `login_verify`, the
choice of the account's email or mobile, and `login_code`, the code sent there through
signup's verification provider and sending helper (signup.steps.send_code: WhatsApp,
else SMS; the per-contact cooldown and hourly limits, purpose `login`). Only an approved
code completes the login, through `finish_login` and `complete_login`, the one place a
login is finished; with "Remember me" ticked it also gives the browser the account's
remembered-device mark (accounts.devices, entry 3), and a later right password with that
valid mark completes the login through `finish_login` at once, without the code. Logout
is a POST only (and leaves the mark).

The login records (entry 4, CAP-3), the lockout and the per-IP limit (entry 5) are the
login guard's (accounts.login_guard). After the CAPTCHA, a password submission first
reserves one of the IP's hourly login attempts, then the lock is checked, and only then
the credentials. A locked account, or an IP over its limit, gets signup's BLOCKED_MESSAGE,
which tells nothing (not which check, how many attempts are left, or whether the account
exists); a right password, and a remembered device, are refused while the lock lasts. A
locked account's pending login ends on the code pages, back on the login page with that
message.

The pending login grants nothing: the session holds the user's pk, their session auth
hash, its start time and the safe `next`, plus the contact chosen. It lives
CODE_TTL_SECONDS; it ends, back on the login page, when it expires or when the account
changes meanwhile (deactivated, its password or its mobile changed).
"""
import functools
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME, get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import NON_FIELD_ERRORS
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect, render, resolve_url
from django.urls import reverse
from django.utils import translation
from django.utils.crypto import constant_time_compare
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, pgettext_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from eventlog.service import client_ip
from legal.rendering import render_markdown
from signup import captcha, limits
from signup.forms import BLOCKED_MESSAGE, refuse
from signup.steps import (CODE_STATE_SESSION_KEY, SENT, code_help_context, code_state,
                          send_code)
from signup.verification import (CODE_TTL_SECONDS, EMAIL, MOBILE, PURPOSE_LOGIN, SMS,
                                 WHATSAPP)

from . import devices, doctor_area, login_guard, profile
from .forms import (LoginCodeForm, LoginContactForm, LoginForm, PersonalDataForm, mask_email,
                    mask_phone, split_landline)

# Where a login lands without a safe `next`: the doctor area's home (signup still ends on
# the upgrade screen or the welcome page).
LOGIN_REDIRECT = 'home'


# The session's pending login (see the module docstring). Never a contact or a code.
PENDING_LOGIN_SESSION_KEY = 'login_pending'
# The login code page's name in signup.steps' code state (and its event-log step).
CODE_PAGE = 'login'
# When a pending login has expired or its account changed: back to the login page.
LOGIN_EXPIRED_MESSAGE = gettext_lazy('Your login has expired. Please log in again.')


def _now():
    """The current time (epoch seconds); a function so tests can move it."""
    return time.time()


def safe_next(request, url):
    """`url` when it is a safe `next` (this host, and HTTPS when the request is, as
    Django's LoginView checks it), else ''."""
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return url
    return ''


def requested_next(request):
    """The `next` of the POST or the query string when it is safe, else ''."""
    url = request.POST.get(REDIRECT_FIELD_NAME, request.GET.get(REDIRECT_FIELD_NAME, ''))
    return safe_next(request, url)


def redirect_after_login(request):
    """To the safe `next` as given (a literal URL, never reversed as a view name, as
    Django's LoginView does), else to the home page."""
    return HttpResponseRedirect(requested_next(request) or resolve_url(LOGIN_REDIRECT))


def complete_login(request, user, next_url=None):
    """Finish the login of `user`: log them in and send them to `next_url` (already
    checked; by default the request's safe `next`), or the home page. Called only once
    the second factor has passed (`finish_login`)."""
    auth_login(request, user)
    if next_url is None:
        return redirect_after_login(request)
    return HttpResponseRedirect(safe_next(request, next_url) or resolve_url(LOGIN_REDIRECT))


# --- The pending login ------------------------------------------------------------------

def start_pending_login(request, user, next_url):
    """After a right password: remember who is logging in, nothing more. Replaces any
    pending login of this session."""
    request.session[PENDING_LOGIN_SESSION_KEY] = {
        'user': str(user.pk),
        'hash': user.get_session_auth_hash(),
        'started': _now(),
        'next': next_url,
        # The contacts as they were at the password (blind indexes, never the values).
        'email_index': user.email_index,
        'phone_index': user.phone_index,
        'contact': None,
        # Whether a code was sent to the chosen contact during this pending login.
        'sent': False,
    }
    _clear_code_state(request)


def end_pending_login(request):
    """Forget the session's pending login and its code page's state."""
    request.session.pop(PENDING_LOGIN_SESSION_KEY, None)
    _clear_code_state(request)


def _clear_code_state(request):
    states = request.session.get(CODE_STATE_SESSION_KEY)
    if states and CODE_PAGE in states:
        request.session[CODE_STATE_SESSION_KEY] = {
            page: state for page, state in states.items() if page != CODE_PAGE}


def _account_unchanged(user, pending):
    """The account can still log in as it could at the password: active, the same session
    auth hash (so the same password), a mobile (decision 2), and the same email and
    mobile (so a code goes to, and is checked against, the contacts of that moment)."""
    return (user is not None and user.is_active and bool(user.phone)
            and constant_time_compare(user.get_session_auth_hash(), pending.get('hash', ''))
            and constant_time_compare(user.email_index, pending.get('email_index', ''))
            and constant_time_compare(user.phone_index, pending.get('phone_index', '')))


def _pending_user(pending):
    return get_user_model()._default_manager.filter(pk=pending.get('user')).first()


def _login_again(request):
    """End the pending login: back to the login page with LOGIN_EXPIRED_MESSAGE. Says
    nothing of why (expired, or the account changed)."""
    end_pending_login(request)
    messages.info(request, LOGIN_EXPIRED_MESSAGE)
    return redirect('login')


def _pending_login_view(view):
    """The second-factor pages: only with a valid pending login, else back to the login
    page (with a message when it expired or its account changed). Calls
    `view(request, user, pending)`."""
    @functools.wraps(view)
    def wrapper(request):
        if request.user.is_authenticated:
            end_pending_login(request)
            return redirect_after_login(request)
        pending = request.session.get(PENDING_LOGIN_SESSION_KEY)
        if not isinstance(pending, dict):
            return redirect('login')
        if _now() - pending.get('started', 0) >= CODE_TTL_SECONDS:
            return _login_again(request)
        user = _pending_user(pending)
        if not _account_unchanged(user, pending):
            return _login_again(request)
        if login_guard.account_locked(account_id=user.pk):
            if request.method == 'POST':
                # A submission refused during a lock: recorded (QA A5), not counted.
                login_guard.record_blocked(request, step=step, account_id=user.pk)
            return _locked_out(request)
        return view(request, user, dict(pending))
    # The event-log step of the page (the contact choice, or the code).
    step = (login_guard.VERIFY_STEP if view.__name__ == 'login_verify'
            else login_guard.CODE_STEP)
    return wrapper


def _locked_out(request):
    """A locked account on the code pages: the pending login ends, back on the login page
    with BLOCKED_MESSAGE (nothing tells why)."""
    end_pending_login(request)
    messages.error(request, BLOCKED_MESSAGE)
    return redirect('login')


def finish_login(request, user, pending, remembered=False):
    """The one place a login completes after the second factor (3.3's remembered device
    skips the code by calling it right after the password, with `remembered`). The
    account must still be as it was at the password; the pending login is removed before
    `auth_login`. The completed login is recorded (entry 4)."""
    user.refresh_from_db()
    if not _account_unchanged(user, pending):
        return _login_again(request)
    end_pending_login(request)
    response = complete_login(request, user, pending.get('next') or '')
    login_guard.record_login(request, user, remembered)
    return response


@sensitive_post_parameters('password')
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
def login(request):
    if request.user.is_authenticated:
        return redirect_after_login(request)
    # Opening or posting the login page ends any earlier pending login, whatever the
    # outcome: only a right password now starts a new one.
    end_pending_login(request)
    if request.method != 'POST':
        form = LoginForm(request)
    else:
        form = LoginForm(request, data=request.POST)
        result = captcha.verify_turnstile(request.POST.get(captcha.TOKEN_FIELD, ''),
                                          client_ip(request))
        if result == captcha.FAILED:
            refuse(form, 'captcha_failed')
        else:
            if result == captcha.UNAVAILABLE:
                # The login goes on (decision 1: the login lockout and IP limits, entry 5,
                # are the guard during an outage). Recorded in the event log, with no
                # account (the credentials are not checked yet).
                login_guard.record_unavailable_captcha(request, login_guard.LOGIN_STEP)
            blocked, reservation = login_guard.reserve_login_attempt(request)
            if blocked:
                # Over the IP's limit: the credentials are not checked.
                refuse(form, 'rate_limited_ip')
            elif login_guard.login_locked(request.POST.get('email', '')):
                # A locked account (or email): refused before the password is checked,
                # so even a right one (or a remembered device) does not log in. Recorded
                # (`blocked: locked`, QA A5), not counted as a failure; it keeps its IP
                # attempt, so the IP's limit bounds these records too.
                login_guard.record_blocked(request, step=login_guard.LOGIN_STEP,
                                           email=request.POST.get('email', ''))
                refuse(form, 'locked')
            elif form.is_valid():
                user = form.get_user()
                if login_guard.account_locked(account_id=user.pk):
                    # Locked meanwhile (a parallel 5th failure, after the check above):
                    # still refused, no pending login and no remembered-device skip.
                    login_guard.record_blocked(request, step=login_guard.LOGIN_STEP,
                                               account_id=user.pk)
                    refuse(form, 'locked')
                elif not user.phone:
                    # No mobile, no portal login (decision 2): the generic error, no code.
                    form.refuse_login()
                else:
                    start_pending_login(request, user, requested_next(request))
                    if (result == captcha.PASSED
                            and devices.is_remembered(request, user, _now())):
                        # A remembered device (entry 3), only with a passed CAPTCHA: the
                        # same pending login, ended by the one completion, without the code.
                        return finish_login(request, user,
                                            dict(request.session[PENDING_LOGIN_SESSION_KEY]),
                                            remembered=True)
                    return redirect('login_verify')
            elif form.has_error(NON_FIELD_ERRORS, 'invalid_login'):
                if login_guard.password_rejected(request,
                                                 form.cleaned_data.get('email', ''),
                                                 form.cleaned_data.get('password', '')):
                    refuse(form, 'locked')
            else:
                # An empty email or password: the credentials were not checked.
                login_guard.refund_login_attempt(request, reservation)
    return render(request, 'accounts/login.html', {
        'form': form,
        'next': requested_next(request),
        **captcha.widget_context(),
    })


def _contact(user, kind):
    return user.email if kind == EMAIL else user.phone


@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
@_pending_login_view
def login_verify(request, user, pending):
    """The choice of where the login code goes: the email or the mobile, both masked
    (decision 3). Choosing sends the code (the mobile's by WhatsApp, else SMS) unless
    one went to that same contact within the cooldown (it is still valid)."""
    form = LoginContactForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        kind = form.cleaned_data['contact']
        contact = _contact(user, kind)
        since = limits.seconds_since_last_send(contact, PURPOSE_LOGIN)
        if not (pending.get('contact') == kind and since is not None
                and since < limits.COOLDOWN_SECONDS):
            _clear_code_state(request)
            outcome = send_code(request, None, CODE_PAGE, contact, PURPOSE_LOGIN,
                                EMAIL if kind == EMAIL else WHATSAPP,
                                login_record=(user.pk, login_guard.VERIFY_STEP))
            request.session[PENDING_LOGIN_SESSION_KEY] = {
                **pending, 'contact': kind, 'sent': outcome.status == SENT}
        return redirect('login_code')
    return render(request, 'accounts/login_choose.html', {
        'email': mask_email(user.email),
        'phone': mask_phone(user.phone),
    })


@sensitive_post_parameters('code')
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
@_pending_login_view
def login_code(request, user, pending):
    """The login code: check it (an approved one completes the login), or send a new one
    (`action`: `resend` over the channel used last, `sms` for the mobile), within the
    limits, as signup's code pages."""
    kind = pending.get('contact')
    if kind not in (EMAIL, MOBILE):
        return redirect('login_verify')
    contact = _contact(user, kind)
    action = request.POST.get('action') if request.method == 'POST' else None
    if action is not None and action != 'resend' and not (action == 'sms' and kind == MOBILE):
        return redirect('login_code')  # an unknown action, or SMS on the email page
    if action is not None:
        if action == 'sms':
            channel, message = SMS, _('We have sent you the code again by SMS.')
        else:
            last = code_state(request, CODE_PAGE).get('channel')
            channel = EMAIL if kind == EMAIL else (SMS if last == SMS else WHATSAPP)
            message = _('We have sent you the code again.')
        outcome = send_code(request, None, CODE_PAGE, contact, PURPOSE_LOGIN, channel,
                            resend=True, login_record=(user.pk, login_guard.CODE_STEP))
        if outcome.status == SENT:
            request.session[PENDING_LOGIN_SESSION_KEY] = {**pending, 'sent': True}
            messages.success(request, message)
        return redirect('login_code')

    if request.method == 'POST':
        form = LoginCodeForm(request.POST, kind=kind, contact=contact)
        if form.is_valid():
            response = finish_login(request, user, pending)
            if form.cleaned_data['remember'] and request.user.is_authenticated:
                devices.remember(response, user, _now())
            return response
        if form.has_error('code', 'invalid_code') or form.has_error('code', 'code_expired'):
            if login_guard.login_code_rejected(request, user, form):
                return _locked_out(request)
    else:
        form = LoginCodeForm(kind=kind, contact=contact)
    code = code_help_context(request, CODE_PAGE, form, contact, PURPOSE_LOGIN,
                             sms=kind == MOBILE)
    # "Sent" only for a send that succeeded during this pending login (not, e.g., a code
    # of an earlier login, already used, while this one's send met the cooldown).
    code['sent_recently'] = code['sent_recently'] and bool(pending.get('sent'))
    return render(request, 'accounts/login_code.html', {
        'form': form,
        'kind': kind,
        'contact': mask_email(user.email) if kind == EMAIL else mask_phone(user.phone),
        'code': code,
    })


@require_POST
@csrf_protect
@never_cache
def logout(request):
    auth_logout(request)
    messages.success(request, _('You have logged out.'))
    return redirect('login')


@login_required
def welcome(request):
    """The page after signup (and after the upgrade screen), with a button to the home.
    Its content comes with the onboarding spec."""
    return render(request, 'accounts/welcome.html')


@login_required
def home(request):
    """The doctor area's home (Annex I mockup): the greeting, the quick links and the
    profile card. Staff accounts see the same page."""
    user = request.user
    return render(request, 'accounts/home.html', {
        'greeting_name': doctor_area.first_name(user),
        'profile_name': doctor_area.display_name(user),
        'profile_line': doctor_area.profile_line(user),
    })


# The settings area's sections (epic-doctor-profile entry 2, Annex I), in the mockup's
# order: the URL name, the section's name (the coming-soon heading, the tile's accessible
# name), the tile's two lines (small, then bold: each with its own context, as the word
# order differs between languages) and its icon (includes/_icon.html). Only the personal
# data is built; the others show "coming soon" inside the area until their stories
# ("Change password" has no owning story yet: deferred-work.md).
SETTINGS_SECTIONS = [
    ('settings', gettext_lazy('My details'),
     pgettext_lazy('settings tile: my details', 'My'),
     pgettext_lazy('settings tile: my details', 'Details'), 'user'),
    ('settings_maker', gettext_lazy('Maker data'),
     pgettext_lazy('settings tile: maker data', 'Maker'),
     pgettext_lazy('settings tile: maker data', 'Data'), 'tag'),
    ('settings_password', pgettext_lazy('settings section', 'Change password'),
     pgettext_lazy('settings tile: change password', 'Change'),
     pgettext_lazy('settings tile: change password', 'Password'), 'key'),
    ('settings_fiscal', gettext_lazy('Fiscal data'),
     pgettext_lazy('settings tile: fiscal data', 'Fiscal'),
     pgettext_lazy('settings tile: fiscal data', 'Data'), 'dollar'),
    ('settings_shipping', gettext_lazy('Shipping addresses'),
     pgettext_lazy('settings tile: shipping addresses', 'Shipping'),
     pgettext_lazy('settings tile: shipping addresses', 'Addresses'), 'truck'),
]
SETTINGS_COMING_SOON = {'maker', 'password', 'fiscal', 'shipping'}


def _settings_context(request, current):
    """The settings area's header: the profile card and the tiles (`current` is the URL
    name of the page shown)."""
    user = request.user
    return {
        'profile_name': doctor_area.display_name(user),
        'profile_line': doctor_area.profile_line(user),
        'sections': [{'url_name': url_name, 'name': name, 'line1': line1, 'line2': line2,
                      'icon': icon, 'current': url_name == current}
                     for url_name, name, line1, line2, icon in SETTINGS_SECTIONS],
    }


@login_required
def personal_data(request):
    """The settings area's personal-data page (URL name `settings`; Annex I): the form of
    accounts.forms.PersonalDataForm, saved through accounts.profile.update_personal_data
    (the only writer). A save that changes the language lands on the page in it."""
    user = request.user
    stored = profile.profile_of(user)
    consents = profile.consents_of(user)
    page_language = translation.get_language()
    default_prefix = user.country.code if user.country else ''
    prefix, national = split_landline(stored.landline, default_prefix)
    initial = {
        'language': stored.language or page_language,
        'city': stored.city,
        'time_zone': stored.time_zone or settings.TIME_ZONE,
        'title': user.title,
        'profession': stored.profession,
        'first_name': user.first_name,
        'middle_name': user.middle_name,
        'last_name': user.last_name,
        'landline_prefix': prefix,
        'landline': national,
        'alternative_email': stored.alternative_email,
        **{kind: accepted for kind, (_document, accepted) in consents.items()},
    }
    form = PersonalDataForm(request.POST if request.method == 'POST' else None,
                            initial=initial, login_email=user.email, user_pk=user.pk,
                            consent_state=consents)
    if request.method == 'POST' and form.is_valid():
        changes = form.changes()
        profile.update_personal_data(user, changes, form.consents())
        # The page lands in the preferred language the form showed (changed or not; the
        # page language on a one-language site). Only this redirect: landing in it after
        # login comes later (deferred-work.md).
        language = form.cleaned_data.get('language') or page_language
        with translation.override(language):
            messages.success(request, _('Your details have been saved.'))
            return redirect(reverse('settings'))
    accepted_on = profile.terms_accepted_on(user)
    return render(request, 'accounts/personal_data.html', {
        **_settings_context(request, 'settings'),
        'form': form,
        'consent_rows': [(form[kind], document, render_markdown(document.body))
                         for kind, (document, _accepted) in consents.items()],
        'terms_accepted_on': accepted_on,
        'country_name': doctor_area.country_name(user),
        'mobile': user.phone,
    })


@login_required
def settings_coming_soon(request, section):
    """A settings section not built yet: "coming soon" inside the settings area, its tile
    current; an unknown section is 404."""
    if section not in SETTINGS_COMING_SOON:
        raise Http404
    url_name = f'settings_{section}'
    title = next(name for name_of_url, name, *_rest in SETTINGS_SECTIONS
                 if name_of_url == url_name)
    return render(request, 'accounts/settings_coming_soon.html', {
        **_settings_context(request, url_name), 'section_title': title})


# The doctor-area addresses not built yet, each served by `coming_soon` until its epic
# replaces the route of the same name (accounts.urls): the URL name and the area's name,
# the one its link on the home shows (the same msgid).
COMING_SOON_AREAS = {
    'patients': gettext_lazy('Patients'),
    'support': gettext_lazy('Support'),
    'messages': gettext_lazy('Messages'),
    'cart': gettext_lazy('Cart'),
    'tutorials': gettext_lazy('Tutorials'),
    'virtuaortho': 'Virtua ORTHO',
    'planning_services': gettext_lazy('Planning Services'),
    'start_a_case': gettext_lazy('Request New Planning'),
}


@login_required
def coming_soon(request, area):
    """The "coming soon" page of an area not built yet, naming it; an unknown area is 404."""
    if area not in COMING_SOON_AREAS:
        raise Http404
    return render(request, 'accounts/coming_soon.html', {'area': COMING_SOON_AREAS[area]})
