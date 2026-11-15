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
"""
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from legal.models import LegalChoice, LegalDocument

from . import client_sync
from .models import Profile

# The account fields the page edits (all travel in `client.updated`).
USER_FIELDS = ('title', 'first_name', 'middle_name', 'last_name')
# The profile fields the page edits (all travel too).
PROFILE_FIELDS = Profile.SYNCED_FIELDS
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
