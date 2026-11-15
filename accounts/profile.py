"""The doctor's personal data (epic-doctor-profile entry 2, AD-15): what the settings
area's personal-data page reads and the one function that writes it.

`update_personal_data(user, changes, consents)` is the only doctor-side writer of the
account's editable fields (title and names), its profile (accounts.models.Profile) and
its optional consent choices (legal.models.LegalChoice). In one transaction it:

- writes the changed account fields and profile fields (the profile row is created on
  the first change); any other field (country, mobile, login email, ...) is refused with
  ValueError, so the server never changes them whatever a page posts;
- records a consent choice only when it differs from the latest one of its kind (or
  there is none and it is ticked): append-only, on the current published document of
  this site's region, at `now`; a kind without a published document is skipped;
- calls `client_sync.emit` once when an account or profile field changed and the
  account is a client (a consent-only save, or a staff account's, emits nothing).

The consents never travel to the admin site. Nothing here logs a value.

The mobile and the contact email (the profile's `alternative_email`) are not written by
`update_personal_data`: they change only after a code sent to the new value
(epic-doctor-profile entry 3). `start_contact_change` keeps the new value in a
PendingContactChange row (encrypted, one per account and kind, PENDING_CHANGE_SECONDS of
life); the views send the code (purpose CHANGE_PURPOSE) and keep only the row's id in the
session. Once the code is accepted, `confirm_contact_change` is the one writer: in one
transaction it locks the account's row, checks the row is still there and alive, checks
again that no other account has the mobile (the database rule is the last guard), writes
the value, forgets the remembered devices for a mobile (`User.devices_valid_after`),
deletes the row and calls `client_sync.emit` once (for a client). After the transaction
it sends the notice mail to the login email, in the background (`notify_change`), naming
what changed with the new value masked.
"""
import logging
import zoneinfo
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone, translation

from legal.models import LegalChoice, LegalDocument
from signup.verification import CODE_TTL_SECONDS

from . import client_sync
from .models import PendingContactChange, Profile

logger = logging.getLogger(__name__)

# The account fields the page edits (all travel in `client.updated`).
USER_FIELDS = ('title', 'first_name', 'middle_name', 'last_name')
# The profile fields the page edits (all travel too). The alternative email (the contact
# email) is not one of them: it changes by `confirm_contact_change` only.
PROFILE_FIELDS = tuple(name for name in Profile.SYNCED_FIELDS if name != 'alternative_email')
EDITABLE_FIELDS = USER_FIELDS + PROFILE_FIELDS
CONSENT_KINDS = LegalDocument.PROFILE_CONSENT_KINDS


@dataclass(frozen=True)
class SaveResult:
    changed_fields: tuple
    consent_kinds: tuple
    emitted: bool


def profile_of(user):
    """The account's profile row, or an unsaved blank one (a missing row reads as blank)."""
    if user.pk is None:
        return Profile()
    return Profile.objects.filter(user_id=user.pk).first() or Profile(user_id=user.pk)


def latest_choice(user, kind):
    """The user's latest choice on a document of `kind` (the latest wins), or None."""
    return (LegalChoice.objects.filter(user=user, document__kind=kind)
            .order_by('-decided_at', '-id').first())


def consents_of(user, now=None):
    """{kind: (current document, accepted)} for each optional consent of the page whose
    kind has a published document in this region; `accepted` is the latest choice's."""
    state = {}
    for kind in CONSENT_KINDS:
        document = LegalDocument.objects.current(kind, now)
        if document is None:
            continue
        latest = latest_choice(user, kind)
        state[kind] = (document, bool(latest is not None and latest.accepted))
    return state


def terms_accepted_on(user):
    """When the account accepted the Terms and the Conditions: the time of its latest
    accepted choice on either (None without one, e.g. a staff account)."""
    choice = (LegalChoice.objects.filter(user=user, accepted=True,
                                         document__kind__in=LegalDocument.REQUIRED_KINDS)
              .order_by('-decided_at', '-id').first())
    return choice.decided_at if choice is not None else None


def withdraw_consent(user, kind, now):
    """Record `kind` as withdrawn (not accepted, on the current document, at `now`),
    unless no document is published or the latest choice is already not accepted."""
    document = LegalDocument.objects.current(kind, now)
    if document is None:
        return False
    latest = latest_choice(user, kind)
    if latest is not None and not latest.accepted:
        return False
    LegalChoice.objects.create(user=user, document=document, accepted=False, decided_at=now)
    return True


def update_personal_data(user, changes, consents=None, *, now=None):
    """Write `changes` ({field: value}, fields of EDITABLE_FIELDS only) and `consents`
    ({kind: ticked}, kinds of CONSENT_KINDS only) for `user` (see the module docstring).
    Returns a SaveResult. Raises ValueError for any other field or kind."""
    consents = consents or {}
    refused = set(changes) - set(EDITABLE_FIELDS)
    if refused:
        raise ValueError(f'Not editable here: {", ".join(sorted(refused))}')
    unknown = set(consents) - set(CONSENT_KINDS)
    if unknown:
        raise ValueError(f'Not a consent of this page: {", ".join(sorted(unknown))}')
    now = now or timezone.now()
    with transaction.atomic():
        locked = get_user_model().objects.select_for_update().get(pk=user.pk)
        user_changed = [name for name in USER_FIELDS
                        if name in changes and (getattr(locked, name) or '') != changes[name]]
        for name in user_changed:
            setattr(locked, name, changes[name])
        if user_changed:
            locked.save(update_fields=user_changed)
        profile = (Profile.objects.select_for_update().filter(user=locked).first()
                   or Profile(user=locked))
        profile_changed = [name for name in PROFILE_FIELDS
                           if name in changes
                           and (getattr(profile, name) or '') != changes[name]]
        for name in profile_changed:
            setattr(profile, name, changes[name])
        if profile_changed:
            profile.save()
        recorded = []
        for kind, ticked in consents.items():
            document = LegalDocument.objects.current(kind, now)
            if document is None:
                continue
            latest = latest_choice(locked, kind)
            if bool(ticked) == bool(latest is not None and latest.accepted):
                continue
            LegalChoice.objects.create(user=locked, document=document, accepted=bool(ticked),
                                       decided_at=now)
            recorded.append(kind)
        emitted = False
        if (user_changed or profile_changed) and client_sync.is_client(locked):
            client_sync.emit(locked)
            emitted = True
    for name in user_changed:
        setattr(user, name, changes[name])
    user.sync_version = locked.sync_version
    return SaveResult(tuple(user_changed + profile_changed), tuple(recorded), emitted)


def clear_profile(user):
    """Erasure: delete the account's profile row (its contact data goes with it)."""
    Profile.objects.filter(user_id=user.pk).delete()


def clear_contact_changes(user):
    """Erasure: delete the account's pending contact changes (their new values)."""
    PendingContactChange.objects.filter(user_id=user.pk).delete()


# --- Contact changes (epic-doctor-profile entry 3) ---------------------------------------

MOBILE = PendingContactChange.MOBILE
EMAIL = PendingContactChange.EMAIL
# The code purpose of a contact change (mytp-core codes: a code for it fails any other).
CHANGE_PURPOSE = 'change'
# A pending change lives as long as its code.
PENDING_CHANGE_SECONDS = CODE_TTL_SECONDS
# What `confirm_contact_change` did.
CHANGED, EXPIRED, IN_USE = 'changed', 'expired', 'in_use'

NOTICE_SUBJECT_TEMPLATE = 'accounts/email/contact_changed_subject.txt'
NOTICE_BODY_TEMPLATE = 'accounts/email/contact_changed.txt'


def current_contact(user, kind):
    """The account's current mobile, or its contact email ('' when none)."""
    if kind == MOBILE:
        return user.phone or ''
    return profile_of(user).alternative_email or ''


def mobile_in_use(phone, user):
    """Whether another account has the mobile `phone` (E.164), through its blind index."""
    return get_user_model().objects.by_phone(phone).exclude(pk=user.pk).exists()


def _is_expired(change, now):
    return now - change.created_at >= timedelta(seconds=PENDING_CHANGE_SECONDS)


def _clear_expired(now):
    """Delete every pending change past its life, of any account: run whenever any change
    starts, so expired rows never pile up (there is no scheduled job for them)."""
    PendingContactChange.objects.filter(
        created_at__lte=now - timedelta(seconds=PENDING_CHANGE_SECONDS)).delete()


def start_contact_change(user, kind, value, *, now=None):
    """Keep `value` (an E.164 mobile or a normalized email) as the account's pending
    change of `kind`, replacing an earlier one of that kind. Returns the row (the caller
    keeps only its id). The account's row is locked first, so two starts of one account
    (a double-submitted form) run one after the other: the second replaces the first's
    row instead of hitting the one-row-per-kind rule."""
    if kind not in (MOBILE, EMAIL):
        raise ValueError(f'Not a contact kind: {kind}')
    now = now or timezone.now()
    with transaction.atomic():
        get_user_model().objects.select_for_update().only('pk').get(pk=user.pk)
        _clear_expired(now)
        PendingContactChange.objects.filter(user_id=user.pk, kind=kind).delete()
        return PendingContactChange.objects.create(user_id=user.pk, kind=kind, value=value,
                                                   created_at=now)


def pending_change(user, kind, change_id, *, now=None):
    """The account's live pending change of `kind` with the id `change_id`, or None (gone,
    another account's or kind's, or expired: an expired row is deleted)."""
    if not change_id:
        return None
    now = now or timezone.now()
    change = PendingContactChange.objects.filter(pk=change_id, user_id=user.pk,
                                                 kind=kind).first()
    if change is not None and _is_expired(change, now):
        change.delete()
        return None
    return change


def cancel_contact_change(user, kind):
    """Forget the account's pending change of `kind` (e.g. refused as in use)."""
    PendingContactChange.objects.filter(user_id=user.pk, kind=kind).delete()


@dataclass(frozen=True)
class ChangeResult:
    status: str
    emitted: bool = False


def confirm_contact_change(user, kind, change_id, *, now=None, notify=True):
    """Write the pending change `change_id` of `kind` once its code was accepted (see the
    module docstring). Returns a ChangeResult: CHANGED; EXPIRED (no live row: nothing
    written); or IN_USE (another account has the mobile, found now or by the database
    rule: nothing written, the row deleted)."""
    now = now or timezone.now()
    User = get_user_model()
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        change = (PendingContactChange.objects.select_for_update()
                  .filter(pk=change_id, user=locked, kind=kind).first())
        if change is None or _is_expired(change, now):
            if change is not None:
                change.delete()
            return ChangeResult(EXPIRED)
        value = change.value
        if kind == MOBILE:
            if mobile_in_use(value, locked):
                change.delete()
                return ChangeResult(IN_USE)
            try:
                with transaction.atomic():
                    locked.phone = value
                    locked.devices_valid_after = now
                    locked.save(update_fields=['phone', 'devices_valid_after'])
            except IntegrityError:
                # Taken between the check and the write: the database rule.
                change.delete()
                return ChangeResult(IN_USE)
        else:
            stored = (Profile.objects.select_for_update().filter(user=locked).first()
                      or Profile(user=locked))
            stored.alternative_email = value
            stored.save()
        change.delete()
        emitted = False
        if client_sync.is_client(locked):
            client_sync.emit(locked)
            emitted = True
    if kind == MOBILE:
        user.phone = value
        user.phone_index = locked.phone_index
        user.devices_valid_after = now
    user.sync_version = locked.sync_version
    if notify:
        notify_change(user, kind, value, translation.get_language(), now)
    return ChangeResult(CHANGED, emitted)


def mask(kind, value):
    """The new value as the notice and the pages show it: never in full."""
    from .forms import mask_email, mask_phone

    return mask_phone(value) if kind == MOBILE else mask_email(value)


def notice_time(user, now):
    """When the change was made, for the notice: in the doctor's time zone (the profile's
    `time_zone`, else the site's), with the zone's name, e.g. `2026-11-15 14:05 (Europe/Madrid)`."""
    name = profile_of(user).time_zone or settings.TIME_ZONE
    try:
        zone = zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        name = settings.TIME_ZONE
        zone = zoneinfo.ZoneInfo(name)
    return f'{now.astimezone(zone):%Y-%m-%d %H:%M} ({name})'


def build_notice(user, kind, value, language, now):
    """The plain-text notice of a change to the account's login email, in `language`."""
    with translation.override(language):
        context = {'kind': kind, 'masked': mask(kind, value),
                   'changed_on': notice_time(user, now),
                   'support_email': settings.SIGNUP_SUPPORT_EMAIL}
        subject = ''.join(render_to_string(NOTICE_SUBJECT_TEMPLATE, context).splitlines())
        body = render_to_string(NOTICE_BODY_TEMPLATE, context)
    return EmailMessage(subject.strip(), body, to=[user.email])


def _notice_job(user, kind, value, language, now):
    from .login_guard import report_error

    try:
        message = build_notice(user, kind, value, language, now)
        message.send()
    except Exception as error:  # noqa: BLE001 - the change is already made
        # The error class only: an SMTP error may quote the address.
        report_error(logger, 'Contact change notice error', 'contact_change.action',
                     'notice', error)


def notify_change(user, kind, value, language, now):
    """Send the notice mail in the background (accounts.password_reset.in_background)."""
    from .password_reset import in_background

    in_background(lambda: _notice_job(user, kind, value, language, now))
