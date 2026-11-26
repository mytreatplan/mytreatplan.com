"""The registered area's entry and exit: login, logout, the welcome page, and the doctor
area's home and "coming soon" pages (epic-doctor-profile entry 1) and its settings area
(the personal-data page and the sections not built yet, entry 2; the code-confirmed
mobile and contact-email changes, entry 3; the profile photo's upload page and its
confirmed delete, entry 4; the fiscal entities' list, add, edit, default and confirmed
delete pages, entry 5).

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
import uuid
from urllib.parse import unquote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME, get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
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
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from eventlog.service import client_ip
from legal.rendering import render_markdown
from signup import captcha, limits
from signup.forms import BLOCKED_MESSAGE, refuse
from signup.steps import (CODE_STATE_SESSION_KEY, SENT, code_help_context, code_state,
                          send_code)
from signup.verification import (CODE_TTL_SECONDS, EMAIL, MOBILE, PURPOSE_LOGIN, SMS,
                                 WHATSAPP)

from . import (client_sync, devices, doctor_area, fiscal, login_failures, login_guard, makers,
               photo, profile, shipping)
from .forms import (ChangeCodeForm, EmailChangeForm, FiscalEntityForm, LoginCodeForm,
                    LoginContactForm, LoginForm, MakerLoginForm, MobileChangeForm, PersonalDataForm,
                    ShippingAddressForm, mask_email, mask_phone, split_landline)

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
    profile card, and a doctor's "Pending payment" block (epic-ordering 9.9: the manual work
    orders clinical ops opened for their patients, each with "Add to cart"; nothing when
    there are none). Staff accounts see the same page, without the block."""
    user = request.user
    is_client = client_sync.is_client(user)
    return render(request, 'accounts/home.html', {
        'greeting_name': doctor_area.first_name(user),
        'profile_name': doctor_area.display_name(user),
        'profile_line': doctor_area.profile_line(user),
        'show_invoices': is_client,  # the invoices page is a doctor's
        'pending_work_orders': _pending_work_orders(user) if is_client else [],
    })


def _pending_work_orders(user):
    """The rows of the home's "Pending payment" block: each manual work order's patient
    code, product name and state: waiting for the bank transfer of its order, in the cart
    already, refused for now (`cart.service.check_work_order`'s reason, e.g. its product is
    no longer offered), or ready to add (its wizard's URL; a missing fiscal entity is the
    wizard's own block)."""
    from cart import service as cart
    from cart.common import refusal_texts
    from cart.wizard import WORK_ORDER_URL_NAME
    from catalogue import resolver
    from patients import service as patients

    if user.is_erased:
        return []
    in_cart = cart.work_order_in_cart(user)
    rows = []
    for work_order in patients.manual_work_orders(user):
        product = resolver.product(work_order.product_id) if work_order.product_id else None
        try:
            cart.check_work_order(user, work_order)
            reason = ''
        except cart.CartRefused as refused:
            reason = '' if refused.code == cart.NO_ENTITY else refusal_texts()[refused.code]
        rows.append({
            'reason': reason,
            'id': work_order.pk, 'code': work_order.patient.code,
            'product': product.public_name if product else work_order.code,
            'in_cart': work_order.pk in in_cart,
            'waiting': work_order.order_id is not None,
            'url': reverse(WORK_ORDER_URL_NAME, args=[work_order.pk, 'options'])})
    return rows


# The settings area's sections (epic-doctor-profile entry 2, Annex I), in the mockup's
# order: the URL name, the section's name (the coming-soon heading, the tile's accessible
# name), the tile's two lines (small, then bold: each with its own context, as the word
# order differs between languages) and its icon (includes/_icon.html). Only "Change
# password" is not built: it shows "coming soon" inside the area ("Change password" has no
# owning story yet: deferred-work.md).
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
SETTINGS_COMING_SOON = {'password'}


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
        **{kind: accepted for kind, (_document, accepted) in consents.items()},
    }
    form = PersonalDataForm(request.POST if request.method == 'POST' else None,
                            initial=initial, user_pk=user.pk, consent_state=consents)
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
    # The pending photo upload's outcome (entry 4): a clean one is promoted, a rejected
    # one gives the reject message once.
    photo_rejected = photo.settle(user)
    photo.forget(request)
    return render(request, 'accounts/personal_data.html', {
        **_settings_context(request, 'settings'),
        'form': form,
        'consent_rows': [(form[kind], document, render_markdown(document.body))
                         for kind, (document, _accepted) in consents.items()],
        'terms_accepted_on': accepted_on,
        'country_name': doctor_area.country_name(user),
        'mobile': user.phone,
        'contact_email': stored.alternative_email,
        'photo_rejected': photo_rejected,
        'photo_editable': photo.can_upload(user) and photo.storage_available(),
        'photo_linked': photo.has_photo(user),
    })


# --- The profile photo (epic-doctor-profile entry 4, accounts.photo) ----------------------
#
# The upload page shows the storage widget (one JPEG, PNG or WebP file, up to 5 MiB);
# once the widget has stored the file, its script posts the new object's id here, and the
# page links it as the doctor's pending photo (accounts.photo.attach; 404 for an object
# that is not theirs to attach). The delete page asks for confirmation, then unlinks and
# deletes the photo and any pending upload. Doctors only (a staff account gets 404). Only
# the object's UUID is posted; no file name or key is logged.

def _doctor_only(request):
    """404 for a staff account, and on a site without file storage (dev-ae today)."""
    if not (photo.can_upload(request.user) and photo.storage_available()):
        raise Http404


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
def settings_photo(request):
    """The profile photo's upload page (GET) and the attach of an uploaded file (POST)."""
    _doctor_only(request)
    if request.method == 'POST':
        try:
            object_id = uuid.UUID(str(request.POST.get('object_id', '')))
        except ValueError:
            raise Http404
        try:
            outcome = photo.attach(request.user, object_id)
        except photo.NotAttachable:
            raise Http404
        if outcome == photo.REJECTED:
            messages.error(request, _('The photo could not be accepted; try another file.'))
        elif outcome == photo.PROMOTED:
            messages.success(request, _('Your photo has been saved.'))
        else:
            messages.success(request, _('Your photo is being checked. It will be shown '
                                        'once the check is done.'))
        return redirect(reverse('settings'))
    return render(request, 'accounts/settings_photo.html', {
        **_settings_context(request, 'settings'),
        'purpose': photo.PURPOSE,
        'max_size': photo.MAX_SIZE,
        'allowed_types': ' '.join(photo.CONTENT_TYPES),
    })


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
def settings_photo_delete(request):
    """The profile photo's delete: the confirmation (GET), then the delete (POST)."""
    _doctor_only(request)
    if request.method == 'POST':
        if photo.remove(request.user):
            messages.success(request, _('Your photo has been deleted.'))
        return redirect(reverse('settings'))
    if not photo.has_photo(request.user):
        return redirect(reverse('settings'))
    return render(request, 'accounts/settings_photo_delete.html',
                  _settings_context(request, 'settings'))


# --- The contact changes (epic-doctor-profile entry 3) ------------------------------------
#
# Each kind (the mobile, the contact email) has a start page (the new value and the current
# password) and a code page. A valid start keeps the new value in a pending change
# (accounts.profile.start_contact_change: encrypted, 10 minutes) and sends a code to it
# (signup.steps.send_code: purpose profile.CHANGE_PURPOSE, the per-contact cooldown and
# hourly limits, the per-IP limit and the account's own hourly limit; the mobile's by
# WhatsApp, else SMS). The session holds only the pending row's id (CHANGE_SESSION_KEY)
# and the code page's state (signup.steps, page CHANGE_PAGES[kind]): never the value or a
# code. An accepted code writes the value through profile.confirm_contact_change. A wrong
# password counts on the login's password lockout (and is refused while the account is
# locked, with the lockout's own message); wrong codes never lock the account.

CHANGE_SESSION_KEY = 'contact_change'  # {kind: pending change id}
# A change just written (its account, kind and pending row id; no value), kept a minute in
# the cache: a double-clicked "Confirm" whose second request comes after the first wrote
# the change goes quietly to the personal-data page instead of "expired". In the cache, not
# the session: the second request may have read the session before the first saved it.
CHANGE_DONE_SECONDS = 60


def _done_key(user, kind, change_id):
    return f'contact-change-done:{user.pk}:{kind}:{change_id}'


def _mark_done(user, kind, change_id):
    cache.set(_done_key(user, kind, change_id), 1, timeout=CHANGE_DONE_SECONDS)


def _just_done(user, kind, change_id):
    return change_id is not None and bool(cache.get(_done_key(user, kind, change_id)))
MOBILE_CHANGE, EMAIL_CHANGE = profile.MOBILE, profile.EMAIL
# The code-state page names (and the event-log steps) of the two flows.
CHANGE_PAGES = {MOBILE_CHANGE: 'settings-mobile', EMAIL_CHANGE: 'settings-email'}
CHANGE_URLS = {MOBILE_CHANGE: ('settings_mobile', 'settings_mobile_code'),
               EMAIL_CHANGE: ('settings_email', 'settings_email_code')}
CHANGE_EXPIRED_MESSAGE = gettext_lazy('This change has expired. Please start again.')
CHANGED_MESSAGES = {
    MOBILE_CHANGE: gettext_lazy('Your mobile phone number has been changed.'),
    EMAIL_CHANGE: gettext_lazy('Your contact email has been changed.'),
}


def _end_change(request, kind):
    """Forget the session's pending change of `kind` and its code page's state."""
    ids = dict(request.session.get(CHANGE_SESSION_KEY) or {})
    if ids.pop(kind, None) is not None:
        request.session[CHANGE_SESSION_KEY] = ids
    states = request.session.get(CODE_STATE_SESSION_KEY)
    if states and CHANGE_PAGES[kind] in states:
        request.session[CODE_STATE_SESSION_KEY] = {
            page: state for page, state in states.items() if page != CHANGE_PAGES[kind]}


def _first_channel(kind):
    return WHATSAPP if kind == MOBILE_CHANGE else EMAIL


def _change_start(request, kind):
    """The start page of a change of `kind` (see above)."""
    user = request.user
    page = CHANGE_PAGES[kind]
    form_class = MobileChangeForm if kind == MOBILE_CHANGE else EmailChangeForm
    if request.method == 'POST':
        form = form_class(request.POST, user=user)
        if login_guard.account_locked(account_id=user.pk):
            # Refused before the password is checked (as the login); recorded, not counted.
            login_guard.record_blocked(request, step=page, account_id=user.pk)
            refuse(form, 'locked')
        elif form.is_valid():
            value = form.new_value()
            change = profile.start_contact_change(user, kind, value)
            _end_change(request, kind)
            request.session[CHANGE_SESSION_KEY] = {
                **(request.session.get(CHANGE_SESSION_KEY) or {}), kind: change.pk}
            send_code(request, None, page, value, profile.CHANGE_PURPOSE, _first_channel(kind),
                      account_id=user.pk)
            return redirect(CHANGE_URLS[kind][1])
        elif form.has_error('password', 'wrong_password'):
            if login_guard.record_failure(request, login_failures.PASSWORD,
                                          login_guard.WRONG_PASSWORD, step=page,
                                          account_id=user.pk):
                refuse(form, 'locked')
    else:
        initial = ({'phone_prefix': user.country.code if user.country else ''}
                   if kind == MOBILE_CHANGE else {})
        form = form_class(user=user, initial=initial)
    return render(request, 'accounts/settings_contact_change.html', {
        **_settings_context(request, 'settings'),
        'form': form,
        'kind': kind,
        'current': profile.current_contact(user, kind),
    })


def _change_code(request, kind):
    """The code page of a change of `kind`: check the code (an accepted one writes the
    change), or send a new one (`action`: `resend` over the channel used last, `sms` for
    the mobile), within the limits."""
    user = request.user
    page = CHANGE_PAGES[kind]
    start = CHANGE_URLS[kind][0]
    change_id = (request.session.get(CHANGE_SESSION_KEY) or {}).get(kind)
    change = profile.pending_change(user, kind, change_id)
    if change is None:
        _end_change(request, kind)
        if _just_done(user, kind, change_id):
            return redirect('settings')  # the first click wrote it
        if change_id is not None:
            messages.error(request, CHANGE_EXPIRED_MESSAGE)
        return redirect(start)
    contact = change.value
    action = request.POST.get('action') if request.method == 'POST' else None
    if action is not None:
        if action == 'sms' and kind == MOBILE_CHANGE:
            channel, message = SMS, _('We have sent you the code again by SMS.')
        elif action == 'resend':
            last = code_state(request, page).get('channel')
            channel = EMAIL if kind == EMAIL_CHANGE else (SMS if last == SMS else WHATSAPP)
            message = _('We have sent you the code again.')
        else:
            return redirect(CHANGE_URLS[kind][1])  # an unknown action, or SMS for an email
        outcome = send_code(request, None, page, contact, profile.CHANGE_PURPOSE, channel,
                            resend=True, account_id=user.pk)
        if outcome.status == SENT:
            messages.success(request, message)
        return redirect(CHANGE_URLS[kind][1])

    if request.method == 'POST':
        form = ChangeCodeForm(request.POST, kind=kind, contact=contact)
        if form.is_valid():
            result = profile.confirm_contact_change(user, kind, change.pk)
            _end_change(request, kind)
            if result.status == profile.CHANGED:
                _mark_done(user, kind, change.pk)
            elif _just_done(user, kind, change.pk):
                return redirect('settings')  # a parallel click wrote it
            if result.status == profile.EXPIRED:
                messages.error(request, CHANGE_EXPIRED_MESSAGE)
                return redirect(start)
            if result.status == profile.IN_USE:
                messages.error(request, MobileChangeForm.error_messages['phone_in_use'])
                return redirect(start)
            messages.success(request, CHANGED_MESSAGES[kind])
            response = redirect('settings')
            if kind == MOBILE_CHANGE:
                # This browser's mark is forgotten too (it is no longer valid anyway).
                response.delete_cookie(devices.cookie_name(user), samesite='Lax')
            return response
        elif _just_done(user, kind, change.pk):
            # The code was used by a parallel click that wrote the change meanwhile.
            _end_change(request, kind)
            return redirect('settings')
    else:
        form = ChangeCodeForm(kind=kind, contact=contact)
    code = code_help_context(request, page, form, contact, profile.CHANGE_PURPOSE,
                             change_url=reverse(start), sms=kind == MOBILE_CHANGE)
    return render(request, 'accounts/settings_contact_code.html', {
        **_settings_context(request, 'settings'),
        'form': form,
        'kind': kind,
        'contact': contact,
        'code': code,
    })


def _change_view(kind, step):
    """A signed-in page of the change of `kind`: the start, or the code (`step`)."""
    @sensitive_post_parameters('password', 'code')
    @csrf_protect
    @never_cache
    @require_http_methods(['GET', 'POST'])
    @login_required
    def view(request):
        return (_change_start if step == 'start' else _change_code)(request, kind)
    view.__name__ = f'settings_{kind}' + ('' if step == 'start' else '_code')
    return view


settings_mobile = _change_view(MOBILE_CHANGE, 'start')
settings_mobile_code = _change_view(MOBILE_CHANGE, 'code')
settings_email = _change_view(EMAIL_CHANGE, 'start')
settings_email_code = _change_view(EMAIL_CHANGE, 'code')


# --- The fiscal entities (epic-doctor-profile entry 5, accounts.fiscal) ---------------------
#
# The settings area's "Fiscal data" section: the list (URL name `settings_fiscal`), the add
# page (`settings_fiscal_add`, which honours a safe `next` for the ordering flow to come
# back to), the edit page, the "make default" POST and the confirmed delete (which archives
# an entity an order has used). Doctors only (a staff account, and another doctor's entity
# id, are 404). Every write goes through accounts.fiscal; nothing here logs a value.

def _fiscal_doctor(request):
    """404 for a staff account (a fiscal entity belongs to a doctor)."""
    if not client_sync.is_client(request.user) or request.user.is_erased:
        raise Http404


def _fiscal_entity(request, pk):
    """The doctor's active entity `pk`, or 404 (another doctor's, archived, unknown)."""
    entity = fiscal.active(request.user).filter(pk=pk).first()
    if entity is None:
        raise Http404
    return entity


def _safe_next(request):
    """The `next` of the add page when it is a path of this site, else '' (the list)."""
    target = request.POST.get('next') or request.GET.get('next') or ''
    decoded = unquote(target)
    if (target.startswith('/') and not decoded.startswith('//') and '\\' not in decoded
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded)
            and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                                require_https=request.is_secure())):
        return target
    return ''


def _fiscal_initial(entity):
    return {**{name: getattr(entity, name) for name in fiscal.FIELDS if name != 'country'},
            'country': entity.country.code if entity.country else '',
            'version': entity.version}


def _fiscal_form_page(request, form, *, entity=None, next_url=''):
    return render(request, 'accounts/settings_fiscal_form.html', {
        **_settings_context(request, 'settings_fiscal'),
        'form': form,
        'entity': entity,
        'usable': entity is None or fiscal.is_usable(entity),
        'next_url': next_url,
    })


@login_required
@never_cache
@require_safe
def settings_fiscal(request):
    """The list of the doctor's fiscal entities (cards, the default first)."""
    _fiscal_doctor(request)
    rows = list(fiscal.active(request.user))
    return render(request, 'accounts/settings_fiscal.html', {
        **_settings_context(request, 'settings_fiscal'),
        'cards': [{'entity': entity, 'usable': fiscal.is_usable(entity),
                   'check_tax_id': bool(fiscal.tax_id_problem(entity))} for entity in rows],
        'can_add': len(rows) < fiscal.MAX_ENTITIES,
        'max_entities': fiscal.MAX_ENTITIES,
    })


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
def settings_fiscal_add(request):
    """Add a fiscal entity; afterwards, to the safe `next` or the list."""
    _fiscal_doctor(request)
    user = request.user
    next_url = _safe_next(request)
    if fiscal.active(user).count() >= fiscal.MAX_ENTITIES:
        messages.error(request, FiscalEntityForm.error_messages['limit']
                       % {'max': fiscal.MAX_ENTITIES})
        return redirect(reverse('settings_fiscal'))
    if request.method == 'POST':
        form = FiscalEntityForm(request.POST)
        if form.is_valid():
            try:
                fiscal.create(user, form.entity_data())
            except fiscal.FiscalInvalid as refused:
                form.add_service_problems(refused.problems)
            else:
                messages.success(request, _('The fiscal entity has been saved.'))
                return redirect(next_url or reverse('settings_fiscal'))
    else:
        form = FiscalEntityForm(initial={
            'kind': fiscal.FiscalEntity.PERSON,
            'country': user.country.code if user.country else ''})
    return _fiscal_form_page(request, form, next_url=next_url)


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
def settings_fiscal_edit(request, pk):
    """Edit a fiscal entity in place (its version is checked: two tabs never overwrite
    each other)."""
    _fiscal_doctor(request)
    entity = _fiscal_entity(request, pk)
    if request.method == 'POST':
        form = FiscalEntityForm(request.POST)
        if form.is_valid():
            version = form.cleaned_data.get('version')
            try:
                if version is None:
                    raise fiscal.StaleVersion(str(pk))
                fiscal.update(request.user, pk, form.entity_data(), version=version)
            except fiscal.FiscalInvalid as refused:
                form.add_service_problems(refused.problems)
            except fiscal.StaleVersion:
                form.refuse_stale()
            except fiscal.FiscalEntity.DoesNotExist:
                raise Http404
            else:
                messages.success(request, _('The fiscal entity has been saved.'))
                return redirect(reverse('settings_fiscal'))
    else:
        form = FiscalEntityForm(initial=_fiscal_initial(entity))
    return _fiscal_form_page(request, form, entity=entity)


@login_required
@csrf_protect
@never_cache
@require_POST
def settings_fiscal_default(request, pk):
    """Make a fiscal entity the default."""
    _fiscal_doctor(request)
    try:
        fiscal.make_default(request.user, pk)
    except (fiscal.FiscalEntity.DoesNotExist, fiscal.FiscalInvalid):
        raise Http404
    messages.success(request, _('The default fiscal entity has been changed.'))
    return redirect(reverse('settings_fiscal'))


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
def settings_fiscal_delete(request, pk):
    """The delete of a fiscal entity: the confirmation (GET), then the delete, or the
    archive of one an order has used (POST)."""
    _fiscal_doctor(request)
    entity = _fiscal_entity(request, pk)
    if request.method == 'POST':
        try:
            fiscal.remove(request.user, pk)
        except fiscal.FiscalEntity.DoesNotExist:
            raise Http404
        messages.success(request, _('The fiscal entity has been deleted.'))
        return redirect(reverse('settings_fiscal'))
    return render(request, 'accounts/settings_fiscal_delete.html', {
        **_settings_context(request, 'settings_fiscal'), 'entity': entity})


# --- The shipping addresses (epic-doctor-profile entry 7, accounts.shipping) ----------------
#
# The settings area's "Shipping addresses" section, the same pages as the fiscal ones: the
# list (URL name `settings_shipping`), the add page (`settings_shipping_add`, which honours
# a safe `next` for the ordering flow to come back to), the edit page, the "make default"
# POST and the confirmed delete (which archives an address an order has used). Doctors only
# (a staff account, and another doctor's address id, are 404). Every write goes through
# accounts.shipping; nothing here logs a value.

def _shipping_address(request, pk):
    """The doctor's active address `pk`, or 404 (another doctor's, archived, unknown)."""
    address = shipping.active(request.user).filter(pk=pk).first()
    if address is None:
        raise Http404
    return address


def _shipping_initial(address):
    return {**{name: getattr(address, name) for name in shipping.FIELDS if name != 'country'},
            'country': address.country.code if address.country else '',
            'version': address.version}


def _shipping_form_page(request, form, *, address=None, next_url=''):
    return render(request, 'accounts/settings_shipping_form.html', {
        **_settings_context(request, 'settings_shipping'),
        'form': form,
        'address': address,
        'usable': address is None or shipping.is_usable(address),
        'next_url': next_url,
    })


@login_required
@never_cache
@require_safe
def settings_shipping(request):
    """The list of the doctor's shipping addresses (cards, the default first)."""
    _fiscal_doctor(request)
    rows = list(shipping.active(request.user))
    return render(request, 'accounts/settings_shipping.html', {
        **_settings_context(request, 'settings_shipping'),
        'cards': [{'address': address, 'usable': shipping.is_usable(address)}
                  for address in rows],
        'can_add': len(rows) < shipping.MAX_ADDRESSES,
        'max_addresses': shipping.MAX_ADDRESSES,
    })


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
def settings_shipping_add(request):
    """Add a shipping address; afterwards, to the safe `next` or the list."""
    _fiscal_doctor(request)
    user = request.user
    next_url = _safe_next(request)
    if shipping.active(user).count() >= shipping.MAX_ADDRESSES:
        messages.error(request, ShippingAddressForm.error_messages['limit']
                       % {'max': shipping.MAX_ADDRESSES})
        return redirect(reverse('settings_shipping'))
    if request.method == 'POST':
        form = ShippingAddressForm(request.POST)
        if form.is_valid():
            try:
                shipping.create(user, form.address_data())
            except shipping.ShippingInvalid as refused:
                form.add_service_problems(refused.problems)
            else:
                messages.success(request, _('The shipping address has been saved.'))
                return redirect(next_url or reverse('settings_shipping'))
    else:
        form = ShippingAddressForm(initial={
            'country': user.country.code if user.country else ''})
    return _shipping_form_page(request, form, next_url=next_url)


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
def settings_shipping_edit(request, pk):
    """Edit a shipping address in place (its version is checked: two tabs never overwrite
    each other)."""
    _fiscal_doctor(request)
    address = _shipping_address(request, pk)
    if request.method == 'POST':
        form = ShippingAddressForm(request.POST)
        if form.is_valid():
            version = form.cleaned_data.get('version')
            try:
                if version is None:
                    raise shipping.StaleVersion(str(pk))
                shipping.update(request.user, pk, form.address_data(), version=version)
            except shipping.ShippingInvalid as refused:
                form.add_service_problems(refused.problems)
            except shipping.StaleVersion:
                form.refuse_stale()
            except shipping.ShippingAddress.DoesNotExist:
                raise Http404
            else:
                messages.success(request, _('The shipping address has been saved.'))
                return redirect(reverse('settings_shipping'))
    else:
        form = ShippingAddressForm(initial=_shipping_initial(address))
    return _shipping_form_page(request, form, address=address)


@login_required
@csrf_protect
@never_cache
@require_POST
def settings_shipping_default(request, pk):
    """Make a shipping address the default."""
    _fiscal_doctor(request)
    try:
        shipping.make_default(request.user, pk)
    except (shipping.ShippingAddress.DoesNotExist, shipping.ShippingInvalid):
        raise Http404
    messages.success(request, _('The default shipping address has been changed.'))
    return redirect(reverse('settings_shipping'))


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
def settings_shipping_delete(request, pk):
    """The delete of a shipping address: the confirmation (GET), then the delete, or the
    archive of one an order has used (POST)."""
    _fiscal_doctor(request)
    address = _shipping_address(request, pk)
    if request.method == 'POST':
        try:
            shipping.remove(request.user, pk)
        except shipping.ShippingAddress.DoesNotExist:
            raise Http404
        messages.success(request, _('The shipping address has been deleted.'))
        return redirect(reverse('settings_shipping'))
    return render(request, 'accounts/settings_shipping_delete.html', {
        **_settings_context(request, 'settings_shipping'), 'address': address})


# --- The maker logins (epic-doctor-profile entry 8, accounts.makers) ------------------------
#
# The settings area's "Maker data" section: the list (URL name `settings_maker`: each named
# maker "Saved on {date}" or "Not saved", the other logins by name), the save/replace page
# of a named maker (`settings_maker_save`, which honours a safe `next`), the add, replace
# and delete pages of an "other" login, and the confirmed delete of a named one. Doctors
# only (a staff account, an unknown maker and another doctor's login id are 404). No page
# ever renders a username or a password, and the POST parameters are kept out of error
# reports. Every write goes through accounts.makers; nothing here logs a value.

def _maker_login(request, maker=None, pk=None):
    """The metadata of the doctor's login for the named `maker`, or of the `other` login
    `pk`, else 404."""
    if pk is not None:
        row = makers.login_meta(request.user, pk)
        if row is None or row['maker'] != makers.OTHER:
            raise Http404
        return row
    if maker not in makers.NAMED:
        raise Http404
    return next((row for row in makers.logins(request.user) if row['maker'] == maker), None)


def _maker_form_page(request, form, *, title, next_url='', replacing=False, other=None):
    return render(request, 'accounts/settings_maker_form.html', {
        **_settings_context(request, 'settings_maker'),
        'form': form, 'title': title, 'next_url': next_url, 'replacing': replacing,
        'other': other,
    })


@login_required
@never_cache
@require_safe
def settings_maker(request):
    """The maker logins page: the five named makers and the other logins, metadata only."""
    _fiscal_doctor(request)
    rows = makers.logins(request.user)
    by_maker = {row['maker']: row for row in rows}
    others = [row for row in rows if row['maker'] == makers.OTHER]
    return render(request, 'accounts/settings_maker.html', {
        **_settings_context(request, 'settings_maker'),
        'named': [{'code': code, 'name': name, 'login': by_maker.get(code)}
                  for code, name in makers.MAKER_NAMES.items()],
        'others': others,
        'can_add': len(others) < makers.MAX_OTHER,
        'max_other': makers.MAX_OTHER,
    })


@login_required
@csrf_protect
@never_cache
@sensitive_post_parameters('username', 'password')
@require_http_methods(['GET', 'POST'])
def settings_maker_save(request, maker):
    """Save, or replace, the login of a named maker; afterwards to the safe `next` or the
    list."""
    _fiscal_doctor(request)
    existing = _maker_login(request, maker=maker)
    next_url = _safe_next(request)
    if request.method == 'POST':
        form = MakerLoginForm(request.POST)
        if form.is_valid():
            try:
                makers.save(request.user, maker, form.login_data())
            except makers.MakerInvalid as refused:
                form.add_service_problems(refused.problems)
            else:
                messages.success(request, _('The maker login has been saved.'))
                return redirect(next_url or reverse('settings_maker'))
    else:
        form = MakerLoginForm()
    return _maker_form_page(request, form, title=makers.MAKER_NAMES[maker],
                            next_url=next_url, replacing=existing is not None)


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
def settings_maker_delete(request, maker):
    """The delete of a named maker's login: the confirmation (GET), then the delete (POST)."""
    _fiscal_doctor(request)
    row = _maker_login(request, maker=maker)
    if row is None:
        raise Http404
    if request.method == 'POST':
        try:
            makers.remove(request.user, row['id'])
        except makers.MakerLogin.DoesNotExist:
            raise Http404
        messages.success(request, _('The maker login has been deleted.'))
        return redirect(reverse('settings_maker'))
    return render(request, 'accounts/settings_maker_delete.html', {
        **_settings_context(request, 'settings_maker'), 'title': makers.MAKER_NAMES[maker],
        'login': row})


@login_required
@csrf_protect
@never_cache
@sensitive_post_parameters('username', 'password')
@require_http_methods(['GET', 'POST'])
def settings_maker_other_add(request):
    """Add a login for another maker (its name, the username and the password)."""
    _fiscal_doctor(request)
    user = request.user
    next_url = _safe_next(request)
    if len([row for row in makers.logins(user) if row['maker'] == makers.OTHER]
           ) >= makers.MAX_OTHER:
        messages.error(request, MakerLoginForm.error_messages['limit']
                       % {'max': makers.MAX_OTHER})
        return redirect(reverse('settings_maker'))
    if request.method == 'POST':
        form = MakerLoginForm(request.POST, ask_name=True)
        if form.is_valid():
            try:
                makers.add_other(user, form.maker_name(), form.login_data())
            except makers.MakerInvalid as refused:
                form.add_service_problems(refused.problems)
            else:
                messages.success(request, _('The maker login has been saved.'))
                return redirect(next_url or reverse('settings_maker'))
    else:
        form = MakerLoginForm(ask_name=True)
    return _maker_form_page(request, form, title=_('Other maker'), next_url=next_url)


@login_required
@csrf_protect
@never_cache
@sensitive_post_parameters('username', 'password')
@require_http_methods(['GET', 'POST'])
def settings_maker_other_replace(request, pk):
    """Replace the username and the password of an "other" login (its name stays)."""
    _fiscal_doctor(request)
    row = _maker_login(request, pk=pk)
    if request.method == 'POST':
        form = MakerLoginForm(request.POST)
        if form.is_valid():
            try:
                makers.save(request.user, makers.OTHER, form.login_data(),
                            other_name=row['other_name'])
            except makers.MakerInvalid as refused:
                form.add_service_problems(refused.problems)
            else:
                messages.success(request, _('The maker login has been saved.'))
                return redirect(reverse('settings_maker'))
    else:
        form = MakerLoginForm()
    return _maker_form_page(request, form, title=row['other_name'], replacing=True,
                            other=row)


@login_required
@csrf_protect
@never_cache
@require_http_methods(['GET', 'POST'])
def settings_maker_other_delete(request, pk):
    """The delete of an "other" login: the confirmation (GET), then the delete (POST)."""
    _fiscal_doctor(request)
    row = _maker_login(request, pk=pk)
    if request.method == 'POST':
        try:
            makers.remove(request.user, pk)
        except makers.MakerLogin.DoesNotExist:
            raise Http404
        messages.success(request, _('The maker login has been deleted.'))
        return redirect(reverse('settings_maker'))
    return render(request, 'accounts/settings_maker_delete.html', {
        **_settings_context(request, 'settings_maker'), 'title': row['other_name'],
        'login': row})


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
# replaces the route of the same name (accounts.urls; the VirtuaOrtho and Planning Services
# pages, built in 9.5, are in cart.urls): the URL name and the area's name,
# the one its link on the home shows (the same msgid).
COMING_SOON_AREAS = {
    'support': gettext_lazy('Support'),
    'messages': gettext_lazy('Messages'),
    'tutorials': gettext_lazy('Tutorials'),
}


@login_required
def coming_soon(request, area):
    """The "coming soon" page of an area not built yet, naming it; an unknown area is 404."""
    if area not in COMING_SOON_AREAS:
        raise Http404
    return render(request, 'accounts/coming_soon.html', {'area': COMING_SOON_AREAS[area]})
