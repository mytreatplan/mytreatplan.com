"""Send accounts' current state to the admin site (epic-admin-interface entry 8): the
backfill of the accounts that existed before the client sync, or a manual re-send.

    python manage.py sync_clients --all
    python manage.py sync_clients <uid> [<uid> ...]

Each account, in its own transaction, goes through accounts.client_sync.emit: its next
version as `client.updated` (`client.erased` for an erased account, or a client made
staff); a staff or erased account never sent is skipped. The outbox delivers them (the
nudge task, or the `dispatchoutbox` job within a minute). Prints the counts; an unknown
uid, or an account whose state the contract refuses (its uid printed, then the next
account), makes the command fail once the others are sent. No email or name is printed.
"""
import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts import client_sync
from integration import outbox


class Command(BaseCommand):
    help = "Send accounts' current state to the admin site (client.updated / client.erased)."

    def add_arguments(self, parser):
        parser.add_argument('uids', nargs='*', help='Account uids.')
        parser.add_argument('--all', action='store_true', help='Every account.')

    def handle(self, *args, uids, all, **options):
        if all == bool(uids):
            raise CommandError('sync_clients: give --all or one or more uids (not both).')
        User = get_user_model()
        unknown = []
        if all:
            targets = list(User.objects.order_by('pk').values_list('uid', flat=True))
        else:
            targets = []
            for text in uids:
                try:
                    uid = uuid.UUID(text)
                except ValueError:
                    unknown.append(text)
                    continue
                targets.append(uid)
        counts = {client_sync.CLIENT_UPDATED: 0, client_sync.CLIENT_ERASED: 0, None: 0}
        failed = []
        for uid in targets:
            try:
                event_type = client_sync.resync(uid)
            except User.DoesNotExist:
                unknown.append(str(uid))
                continue
            except (ValueError, outbox.OutboxError) as exc:
                # The account's state fails the contract (the outbox's errors name the
                # fields, never values): the others go on.
                failed.append(str(uid))
                self.stderr.write(f'sync_clients: {uid} failed: {type(exc).__name__}: {exc}')
                continue
            counts[event_type] += 1
        self.stdout.write(
            f'sync_clients: {counts[client_sync.CLIENT_UPDATED]} client.updated, '
            f'{counts[client_sync.CLIENT_ERASED]} client.erased, {counts[None]} skipped '
            f'(not sent: staff or erased)')
        problems = []
        if unknown:
            problems.append(f'unknown: {", ".join(unknown)}')
        if failed:
            problems.append(f'failed: {", ".join(failed)}')
        if problems:
            raise CommandError(f'sync_clients: {"; ".join(problems)}')
