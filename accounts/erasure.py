"""Account erasure: a logical deletion from the admin, then a physical one after retention.

`erase_user` (the admin's "Erase account" action, accounts.admin) deactivates the account
and keeps only what the evidence needs (email, title, names, country, phone, date joined,
and the legal choices) until `purge_after`, ACCOUNT_ERASURE_RETENTION_DAYS later; it
clears the rest. Its `email_index` becomes a tombstone (accounts.models.User), so the same
email can register again. `purge_erased` (the daily `purgeerased` job, `manage.py
purge_erased_accounts`) then deletes each erased account with its legal choices once
`purge_after` has passed. There is no undo.

Nothing here logs or returns an email or a name.
"""
from datetime import timedelta

from django.conf import settings
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from legal.models import LegalChoice, LegalDocument

# The admin log's change message of an erasure.
ERASED_MESSAGE = 'Erased'


def retention_days():
    """ACCOUNT_ERASURE_RETENTION_DAYS. Raises ImproperlyConfigured while it is unset or
    below 1 (0 would let the next purge delete the evidence at once): the period is the
    lawyer's, never guessed in code."""
    days = getattr(settings, 'ACCOUNT_ERASURE_RETENTION_DAYS', None)
    if days is None or days < 1:
        raise ImproperlyConfigured(
            'ACCOUNT_ERASURE_RETENTION_DAYS must be set to a positive number of days.')
    return days


def purge_date(now=None):
    """When an account erased at `now` is deleted for good."""
    return (now or timezone.now()) + timedelta(days=retention_days())


def _withdraw_commercial_consent(user, now):
    """Record the commercial consent as withdrawn (not accepted, on the current version, at
    `now`), unless no version is published or the latest choice is already not accepted."""
    kind = LegalDocument.Kind.COMMERCIAL_CONSENT
    document = LegalDocument.objects.current(kind, now)
    if document is None:
        return
    latest = (LegalChoice.objects.filter(user=user, document__kind=kind)
              .order_by('-decided_at', '-id').first())
    if latest is not None and not latest.accepted:
        return
    LegalChoice.objects.create(user=user, document=document, accepted=False, decided_at=now)


def erase_user(user, *, by, now=None):
    """Erase `user`, by the staff member `by`, in one transaction. Returns False (and
    changes nothing) when the account is already erased. Refuses `by`'s own account
    (ValueError) and an unset retention (ImproperlyConfigured)."""
    from .admin import _LoggedUser

    if user.pk == by.pk:
        raise ValueError('A staff member cannot erase their own account.')
    now = now or timezone.now()
    purge_after = purge_date(now)
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().get(pk=user.pk)
        if user.is_erased:
            return False
        user.erased_at = now
        user.purge_after = purge_after
        user.is_active = user.is_staff = user.is_superuser = False
        # Also ends their sessions: the session hash derives from the password.
        user.set_unusable_password()
        user.how_found = ''
        user.how_found_other = ''
        user.last_login = None
        user.save()
        user.groups.clear()
        user.user_permissions.clear()
        _withdraw_commercial_consent(user, now)
        LogEntry.objects.log_actions(user_id=by.pk, queryset=[_LoggedUser(user)],
                                     action_flag=CHANGE, change_message=ERASED_MESSAGE)
    return True


def purge_erased(now=None, batch_size=500):
    """Delete each erased account whose `purge_after` has passed, with its legal choices
    (first: the user's are PROTECT), one transaction per batch. Returns how many."""
    User = get_user_model()
    now = now or timezone.now()
    total = 0
    while True:
        with transaction.atomic():
            ids = list(User.objects.filter(erased_at__isnull=False, purge_after__lte=now)
                       .order_by('pk').values_list('pk', flat=True)[:batch_size])
            if not ids:
                return total
            LegalChoice.objects.filter(user_id__in=ids).delete()
            User.objects.filter(pk__in=ids).delete()
        total += len(ids)
