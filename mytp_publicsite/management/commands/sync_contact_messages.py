"""Send contact messages' current state to the admin site (epic-admin-interface entry 9):
the backfill of the messages stored before the sync, or a manual re-send.

    python manage.py sync_contact_messages --all
    python manage.py sync_contact_messages <uid> [<uid> ...]

Each message, in its own transaction, goes through mytp_publicsite.contact_sync.emit: its
next version as `contact_message.updated`. The outbox delivers them (the nudge task, or
the `dispatchoutbox` job within a minute). Prints the count; an unknown uid, or a message
whose state the contract refuses (its uid printed, then the next message), makes the
command fail once the others are sent. Nothing of a message is printed.
"""
import uuid

from django.core.management.base import BaseCommand, CommandError

from integration import outbox
from mytp_publicsite import contact_sync
from mytp_publicsite.models import ContactMessage


class Command(BaseCommand):
    help = "Send contact messages' current state to the admin site (contact_message.updated)."

    def add_arguments(self, parser):
        parser.add_argument('uids', nargs='*', help='Contact message uids.')
        parser.add_argument('--all', action='store_true', help='Every contact message.')

    def handle(self, *args, uids, all, **options):
        if all == bool(uids):
            raise CommandError('sync_contact_messages: give --all or one or more uids '
                               '(not both).')
        unknown = []
        if all:
            targets = list(ContactMessage.objects.order_by('pk')
                           .values_list('uid', flat=True))
        else:
            targets = []
            for text in uids:
                try:
                    targets.append(uuid.UUID(text))
                except ValueError:
                    unknown.append(text)
        sent = 0
        failed = []
        for uid in targets:
            try:
                contact_sync.resync(uid)
            except ContactMessage.DoesNotExist:
                unknown.append(str(uid))
                continue
            except (ValueError, outbox.OutboxError) as exc:
                # The message's state fails the contract (the outbox's errors name the
                # fields, never values): the others go on.
                failed.append(str(uid))
                self.stderr.write(f'sync_contact_messages: {uid} failed: '
                                  f'{type(exc).__name__}: {exc}')
                continue
            sent += 1
        self.stdout.write(f'sync_contact_messages: {sent} contact_message.updated')
        problems = []
        if unknown:
            problems.append(f'unknown: {", ".join(unknown)}')
        if failed:
            problems.append(f'failed: {", ".join(failed)}')
        if problems:
            raise CommandError(f'sync_contact_messages: {"; ".join(problems)}')
