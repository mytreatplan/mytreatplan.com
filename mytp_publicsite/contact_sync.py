"""The contact message sync (epic-admin-interface entry 9, AD-18, interface I9): the admin
site keeps a copy of every message sent through the contact form, fed by the outbox.

`emit(message)` runs inside the caller's `transaction.atomic()`, after the caller saved
the message. It locks the message's row, records `contact_message.updated` (its full
state, mytp-core's `ContactMessageUpdated`) in the outbox (integration.outbox.record,
aggregate id = the message's `uid`, aggregate version = its `sync_version` + 1) and bumps
`sync_version`.

`delete(messages)` is the only other write (the Django admin's delete, the one change
path of a stored message): in one transaction, for each message, its `.updated` rows not
sent yet are deleted, `contact_message.deleted` (an empty payload) is recorded at the
next version when the message was sent before (`sync_version` > 0; a message never sent
records nothing), and the row is deleted.

Called from the contact form (`mytp_publicsite.contact.accept`, in the transaction that
creates the message; the inbox mail still goes once that commits, and its status is not
sent), the Django admin's delete (`ContactMessageAdmin.delete_model`, `delete_queryset`),
the re-sync the admin site asks for (`resync`, POST /api/v1/contact-messages/resync) and
the backfill (`manage.py sync_contact_messages`). If the caller's transaction rolls back,
neither the version nor the event stays. Nothing of a message is logged here.
"""
from django.db import transaction

from integration import outbox
from integration.models import OutboxEvent
from mytp_core.contracts import CONTACT_MESSAGE_DELETED, CONTACT_MESSAGE_UPDATED

from .models import ContactMessage


class ContactSyncError(Exception):
    """`emit` or `delete` was called outside a transaction."""


def _require_atomic(name):
    if not transaction.get_connection().in_atomic_block:
        raise ContactSyncError(f'contact_sync.{name} must run inside transaction.atomic()')


def payload_of(message):
    """The `contact_message.updated` payload of a message (its state, in clear: the signed
    channel carries it; the admin site encrypts it). The mail status is not sent."""
    return {
        'first_name': message.first_name or '',
        'middle_name': message.middle_name or '',
        'surname': message.surname or '',
        'email': message.email,
        'phone': message.phone or '',
        'country': message.country or '',
        'aligners': message.aligners or '',
        'club': message.club or '',
        'decision': message.decision or '',
        'sent_at': message.sent_at,
    }


def emit(message):
    """Record the message's `contact_message.updated` in the caller's transaction (see the
    module docstring); returns the event type.
    Raises ContactSyncError outside `transaction.atomic()`, and the outbox's errors (the
    payload refused by the contract: ValueError naming fields, never values; nothing is
    then written)."""
    _require_atomic('emit')
    locked = ContactMessage.objects.select_for_update().get(pk=message.pk)
    version = locked.sync_version + 1
    # The outbox checks the payload before it writes anything: a refusal leaves the
    # version as it was.
    outbox.record(CONTACT_MESSAGE_UPDATED, locked.uid, version, payload_of(locked))
    ContactMessage.objects.filter(pk=locked.pk).update(sync_version=version)
    message.sync_version = version
    return CONTACT_MESSAGE_UPDATED


def delete(messages):
    """Delete `messages` (ContactMessage instances or a queryset) in the caller's
    transaction, telling the admin site (see the module docstring); returns the number
    of `contact_message.deleted` events recorded. Raises ContactSyncError outside
    `transaction.atomic()`."""
    _require_atomic('delete')
    pks = [message.pk for message in messages]
    recorded = 0
    for locked in ContactMessage.objects.select_for_update().filter(pk__in=pks) \
            .order_by('pk'):
        if locked.sync_version > 0:
            # Its update not sent yet goes: the admin site gets the tombstone only.
            OutboxEvent.objects.filter(aggregate_id=locked.uid,
                                       type=CONTACT_MESSAGE_UPDATED,
                                       state=OutboxEvent.PENDING).delete()
            outbox.record(CONTACT_MESSAGE_DELETED, locked.uid, locked.sync_version + 1, {})
            recorded += 1
        locked.delete()
    return recorded


def resync(uid):
    """Emit the current state of the message `uid` again (the admin site's re-sync, the
    backfill), in its own transaction; returns the event type, or raises
    ContactMessage.DoesNotExist."""
    with transaction.atomic():
        message = ContactMessage.objects.get(uid=uid)
        return emit(message)
