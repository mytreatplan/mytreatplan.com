"""The client record sync (epic-admin-interface entry 8, AD-2, AD-15, AD-18, interface I8):
the admin site keeps a copy of every doctor (client) registered here, fed by the outbox.

`emit(user)` runs inside the caller's `transaction.atomic()`, after the caller saved the
account. It locks the account's row, bumps its `sync_version` and records one event in
the outbox (integration.outbox.record, aggregate id = the account's `uid`, aggregate
version = the new `sync_version`):

- `client.erased` (an empty payload) for an erased account (accounts.erasure), once it
  was sent (`sync_version` > 0; an account never sent records nothing). Its pending
  `client.updated` rows are deleted first, in the same transaction;
- `client.updated` (its full current state, mytp-core's `ClientUpdated`) otherwise.

Staff accounts (`is_staff` or `is_superuser`) are not clients: nothing is recorded for
them, except `client.erased` (as above) when the account was sent as a client before
(`sync_version` > 0), so the admin site drops it.

Called from signup (`signup.views._create_account`), the Django admin's user save
(`accounts.admin.UserAdmin.save_model`, add and change) and `accounts.erasure.erase_user`;
also by the re-sync the admin site asks for (`resync`, POST /api/v1/clients/resync) and the
backfill (`manage.py sync_clients`). Not for password, session or re-encryption writes;
the purge of erased accounts sends nothing. If the caller's transaction rolls back,
neither the version nor the event stays. The payload is never logged here.
"""
from django.contrib.auth import get_user_model
from django.db import transaction

from integration import outbox
from integration.models import OutboxEvent
from mytp_core.contracts import CLIENT_ERASED, CLIENT_UPDATED

from .models import instance_region


class ClientSyncError(Exception):
    """`emit` was called outside a transaction."""


def is_client(user):
    """Whether the account is a client (a doctor): not staff, not a superuser."""
    return not (user.is_staff or user.is_superuser)


def payload_of(user):
    """The `client.updated` payload of an account (its current state, in clear: the
    signed channel carries it; the admin site encrypts it). The free text of "how found:
    others" is not sent."""
    return {
        'title': user.title or '',
        'first_name': user.first_name or '',
        'middle_name': user.middle_name or '',
        'last_name': user.last_name or '',
        'email': user.email,
        'phone': user.phone or '',
        'country': user.country.code if user.country else '',
        # '' only for a row the previous release inserted during a deploy (accounts 0013):
        # this instance's region, as that migration fills it.
        'home_region': user.home_region or instance_region(),
        'active': bool(user.is_active),
        'joined_at': user.date_joined,
        'how_found': user.how_found or '',
    }


def emit(user):
    """Record the account's client event in the caller's transaction (see the module
    docstring); returns the event type recorded, or None for a staff or erased account
    never sent.
    Raises ClientSyncError outside `transaction.atomic()`, and the outbox's errors (the
    caller's transaction then rolls back)."""
    if not transaction.get_connection().in_atomic_block:
        raise ClientSyncError('client_sync.emit must run inside transaction.atomic()')
    User = get_user_model()
    locked = User.objects.select_for_update().get(pk=user.pk)
    if locked.is_erased or not is_client(locked):
        if locked.sync_version == 0:
            return None  # never sent: the admin site has nothing to drop
        # Its client.updated rows not sent yet go (decision 2026-11-06 after review): the
        # admin site gets the tombstone only.
        OutboxEvent.objects.filter(aggregate_id=locked.uid, type=CLIENT_UPDATED,
                                   state=OutboxEvent.PENDING).delete()
        event_type, payload = CLIENT_ERASED, {}
    else:
        event_type, payload = CLIENT_UPDATED, payload_of(locked)
    version = locked.sync_version + 1
    User.objects.filter(pk=locked.pk).update(sync_version=version)
    outbox.record(event_type, locked.uid, version, payload)
    user.sync_version = version
    return event_type


def resync(uid):
    """Emit the current state of the account `uid` again (the admin site's re-sync), in its
    own transaction; returns the event type, None for an account never sent that is
    staff or erased, or
    raises User.DoesNotExist."""
    User = get_user_model()
    with transaction.atomic():
        user = User.objects.get(uid=uid)
        return emit(user)
