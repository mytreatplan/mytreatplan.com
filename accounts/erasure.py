"""Account erasure: a logical deletion from the admin, then a physical one after retention.

`erase_user` (the admin's "Erase account" action, accounts.admin) deactivates the account
and hides it and its patients until `purge_after`, ACCOUNT_ERASURE_RETENTION_DAYS later.
The account's data (including the profile, the fiscal entities and the shipping addresses)
stays for the archive (below); at once it deletes the maker logins, any pending contact
change and the profile photo with its pending upload (through storage.service.delete,
by the staff member erasing it, accounts.photo), and it records the commercial
and WhatsApp contact consents as withdrawn. Its `email_index` becomes a tombstone (accounts.models.User), so the same
email can register again. `purge_erased` (the daily `purgeerased` job, `manage.py
purge_erased_accounts`) then deletes each erased account with its legal choices once
`purge_after` has passed (any stored object the account still owns is detached and
deleted first, by the system: `StoredObject.owner` is PROTECT). There is no undo. Before it is deleted, each account leaves one
activity record (kind `account`, event `account_purged`, QA A6), tied to its pk and holding
only its email, encrypted in the event log (`Event.data`), kept 5 years with the other
activity records (eventlog.retention), so they can still be tied to the person.

`erase_user` also tells the admin site (`client.erased`, accounts.client_sync, in the
same transaction), which deletes its copy; the purge sends nothing and deletes every
outbox row of the purged accounts.

Logical deletion with a regional archive (epic-ordering 9.12, user decision 2026-11-23):
`erase_user` only hides the account and its patients (patients `erased`; the fiscal
entities, shipping addresses and profile stay for the archive; the maker logins and the
photo still go at once, since neither is archived). The purge, once `purge_after` has
passed, writes one encrypted write-once archive object per account (archive.service),
verifies it and only then deletes the live rows (work orders, patients, fiscal entities,
shipping addresses, then the account). An archive failure keeps every live row of that
account for the next run. The purge refuses to run while an ARCHIVE_* setting is unset.

Nothing here logs or returns an email or a name.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from eventlog.models import Attempt
from eventlog.service import record_activity
from integration.models import OutboxEvent
from legal.models import LegalChoice, LegalDocument
from patients.models import Patient, WorkOrder

from archive import service as archive_service
from storage import s3 as storage_s3
from storage import service as storage_service
from storage.models import StoredObject

from . import client_sync, makers, photo, profile
from .models import FiscalEntity, ShippingAddress

logger = logging.getLogger(__name__)

# The admin log's change message of an erasure.
ERASED_MESSAGE = 'Erased'
# The purge record (QA A6): its event and step.
PURGED_EVENT = 'account_purged'
PURGE_STEP = 'purge'


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


def _withdraw_optional_consents(user, now):
    """Record the commercial and the WhatsApp contact consents as withdrawn (not
    accepted, on the current version, at `now`), each unless no version is published or
    its latest choice is already not accepted (accounts.profile.withdraw_consent)."""
    for kind in LegalDocument.PROFILE_CONSENT_KINDS:
        profile.withdraw_consent(user, kind, now)


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
        _withdraw_optional_consents(user, now)
        # The profile photo and its pending upload (entry 4), deleted with their traces
        # before the profile row that links them goes.
        photo.erase(user, by)
        # Only the contact change waiting for its code goes now (epic-doctor-profile entry
        # 3); the profile, the fiscal entities (entry 5) and the shipping addresses (entry
        # 7) stay until the purge, which archives them first (9.12).
        profile.clear_contact_changes(user)
        # The patients are hidden from every portal view (9.12); their rows stay.
        Patient.objects.filter(doctor_id=user.pk).update(
            status=Patient.ERASED, version=F('version') + 1, updated_at=now)
        # The maker logins (entry 8) go too.
        makers.erase(user)
        # The admin site deletes its copy (client.erased, epic-admin-interface entry 8).
        client_sync.emit(user)
        LogEntry.objects.log_actions(user_id=by.pk, queryset=[_LoggedUser(user)],
                                     action_flag=CHANGE, change_message=ERASED_MESSAGE)
    return True


def _record_purge(pk):
    """The purge record of the account `pk` (QA A6): one activity record (kind `account`,
    event PURGED_EVENT) tied to its pk, holding only its email, encrypted, so its 5-year
    activity records can still be tied to the person (the lawyer: a minimal identity).
    Returns whether it was written. An email that cannot be read (e.g. not decryptable)
    is reported without values (record_activity reports its own failures)."""
    try:
        with transaction.atomic():
            email = (get_user_model().objects.filter(pk=pk)
                     .values_list('email', flat=True).first())
    except Exception as error:  # noqa: BLE001 - one account never stops the purge
        from .login_guard import report_error

        report_error(logger,'Account purge error', 'erasure.action', 'read_email', error)
        return False
    return record_activity(Attempt.ACCOUNT, PURGED_EVENT, account_id=pk,
                           step=PURGE_STEP, data={'email': email}) is not None


def _release_stored_objects(ids):
    """The stored objects the accounts `ids` still own (StoredObject.owner is PROTECT):
    each not marked yet is deleted through storage.service.delete (by the system, after
    the batch commits; one marked earlier, by the erasure, is left to its task and the
    sweep), and every one is detached from its owner now, so the accounts can go."""
    owned = StoredObject.objects.filter(owner_id__in=ids)
    for obj in owned.filter(delete_requested_at__isnull=True):
        storage_service.delete(obj, None)
    owned.update(owner=None)


def check_archive_settings():
    """Raises ImproperlyConfigured naming the first unset ARCHIVE_* (or STORAGE_*) setting
    the archive needs; the purge refuses to run (and deletes nothing) until all are set."""
    missing = storage_s3.missing_archive_setting()
    if missing:
        raise ImproperlyConfigured(f'{missing} must be set for the account archive.')
    if settings.ARCHIVE_RETENTION_YEARS < 1:
        raise ImproperlyConfigured('ARCHIVE_RETENTION_YEARS must be at least 1.')


def _archive(pk, client, errors=None):
    """Write (or verify) the archive of the account `pk`; True when it is safely archived.
    A failure is reported (the class, and the message of an ArchiveError, which never holds
    personal data; no other exception's text) and leaves every live row. The ArchiveError
    messages are added to `errors`, when given."""
    from .login_guard import report_error

    try:
        archive_service.write(get_user_model().objects.get(pk=pk), client=client)
    except Exception as error:  # noqa: BLE001 - one account never stops the purge
        detail = str(error) if isinstance(error, archive_service.ArchiveError) else ''
        if detail and errors is not None:
            errors.add(detail)
        report_error(logger, 'Account archive error', 'erasure.action', 'archive', error,
                     detail)
        return False
    return True


def _delete_live_rows(ids):
    """The archived accounts' patients, work orders, fiscal entities and shipping
    addresses (the account itself goes with the caller's later steps)."""
    WorkOrder.objects.filter(patient__doctor_id__in=ids).delete()
    Patient.objects.filter(doctor_id__in=ids).delete()
    FiscalEntity.objects.filter(user_id__in=ids).delete()
    ShippingAddress.objects.filter(user_id__in=ids).delete()


def purge_erased(now=None, batch_size=500, stats=None):
    """Archive, then delete, each erased account whose `purge_after` has passed.

    Per account: its archive object is written and verified (archive.service.write; an
    existing row is only verified), and only then, in one transaction per batch and after
    the purge record (`_record_purge`), are its live rows deleted: work orders, patients,
    fiscal entities, shipping addresses, its legal choices (first: the user's are
    PROTECT), the stored objects it still owns (detached and deleted), its outbox rows
    and the account. An account whose archive failed, whose email cannot be read or whose
    record could not be written is kept whole for the next run. Archive rows past their
    `retain_until` are deleted. Refuses (ImproperlyConfigured) while an ARCHIVE_* setting
    is unset. Returns how many accounts were deleted; `stats`, when given, receives
    `failed` (accounts kept), `errors` (the archive failures' messages) and `expired` (archive rows deleted)."""
    check_archive_settings()
    User = get_user_model()
    now = now or timezone.now()
    client = storage_s3.make_archive_client()
    stats = stats if stats is not None else {}
    total = 0
    skipped = []
    errors = set()
    while True:
        pks = list(User.objects.filter(erased_at__isnull=False, purge_after__lte=now)
                   .exclude(pk__in=skipped).order_by('pk')
                   .values_list('pk', flat=True)[:batch_size])
        if not pks:
            break
        # The archive puts are not part of the transaction (an object cannot be rolled
        # back): each is verified and recorded before any live row goes.
        archived = []
        for pk in pks:
            (archived if _archive(pk, client, errors) else skipped).append(pk)
        with transaction.atomic():
            ids = []
            for pk in archived:
                (ids if _record_purge(pk) else skipped).append(pk)
            _delete_live_rows(ids)
            LegalChoice.objects.filter(user_id__in=ids).delete()
            _release_stored_objects(ids)
            # Every outbox row of these accounts, whatever its state (decision 2026-11-06
            # after review): nothing of them stays in the outbox. Nothing is sent.
            uids = User.objects.filter(pk__in=ids).values_list('uid', flat=True)
            OutboxEvent.objects.filter(aggregate_id__in=list(uids)).delete()
            User.objects.filter(pk__in=ids).delete()
        total += len(ids)
    stats['failed'] = len(skipped)
    stats['errors'] = sorted(errors)
    stats['expired'] = archive_service.delete_expired(now)
    return total
