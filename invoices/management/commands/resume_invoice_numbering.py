"""Restore-safe invoice numbering (AD-7). After a database restore the series counters may be
behind the numbers already issued, which the admin site's accounting copy holds.

    python manage.py resume_invoice_numbering --hold-only   # right after the restore
    python manage.py resume_invoice_numbering               # once the admin site is reachable

`--hold-only` puts numbering on hold and stops: no invoice or credit note is numbered (and
so no payment is confirmed) until the second command succeeds. Without the flag the command
sets the hold, asks the admin site for the highest number of each series of this region
(the hub lookup `invoice-series-highest`), raises every counter that is behind, creates a
series the admin knows and this site lacks, and lifts the hold. It prints each series. It is
idempotent: counters only go up. Once the hold is lifted it re-checks every order still
pending payment that has an open gateway attempt, whatever its session time (the gateway's webhook retries give up after
about 2 h 42 min, so a payment made during a long hold would stay unconfirmed): each is
re-read at the gateway and confirmed when paid; one line per order confirmed (the order
reference only) and a count. A failure on one order is reported and does not stop the
others or put the hold back. When the admin site cannot answer the hold stays on and the
command exits non-zero; run it again later.
"""
from django.core.management.base import BaseCommand, CommandError

from integration.hub import HubLookupFailed, lookup
from invoices import service
from mytp_core.contracts import InvoiceSeriesHighestAnswer
from mytp_core.contracts.hub import INVOICE_SERIES_HIGHEST_PATH
from payments import service as payments_service
from payments import webhooks
from payments.models import Order, Payment


class Command(BaseCommand):
    help = 'Check the invoice counters against the admin site after a restore, then resume.'

    def add_arguments(self, parser):
        parser.add_argument('--hold-only', action='store_true',
                            help='Put numbering on hold and stop (the runbook\'s first step).')

    def handle(self, *args, hold_only=False, **options):
        service.hold_numbering()
        if hold_only:
            self.stdout.write('invoice numbering is on hold; run resume_invoice_numbering '
                              'once the admin site is reachable')
            return
        try:
            answer = lookup(INVOICE_SERIES_HIGHEST_PATH, InvoiceSeriesHighestAnswer)
        except HubLookupFailed as exc:
            raise CommandError(
                f'resume_invoice_numbering: the admin site gave no answer ({exc}); invoice '
                f'numbering stays on hold, run it again once the admin site is reachable'
            ) from None
        report = service.resume_after_restore(answer)
        for company, year, kind, before, after in report:
            if before is None:
                state = f'created at {after}'
            elif after > before:
                state = f'raised from {before} to {after}'
            else:
                state = f'unchanged at {before}'
            self.stdout.write(f'{company} {year} {kind}: {state}')
        self.stdout.write(f'invoice numbering resumed ({len(report)} series checked)')
        self._recheck_pending_orders()

    def _recheck_pending_orders(self):
        """Re-read, at the gateway, every OPEN payment of each pending-payment order,
        whatever its session time (a long hold leaves payments whose session has expired
        but that may have been paid), and confirm the paid ones. Outside any transaction (the re-read is an HTTP call)."""
        live = Payment.objects.filter(state=Payment.OPEN)
        order_ids = (Order.objects.filter(state=Order.PENDING_PAYMENT, pk__in=live.values('order_id'))
                     .values_list('pk', flat=True))
        confirmed = 0
        for order in Order.objects.filter(pk__in=list(order_ids)).order_by('created_at'):
            for payment in live.filter(order_id=order.pk):
                try:
                    outcome = webhooks.reread_and_confirm(payment)
                except Exception as failure:  # noqa: BLE001 - one order never stops the rest
                    self.stderr.write(f'order {order.reference}: not re-checked '
                                      f'({type(failure).__name__})')
                    continue
                if outcome == payments_service.CONFIRMED:
                    confirmed += 1
                    self.stdout.write(f'order {order.reference}: payment confirmed')
        self.stdout.write(f'{confirmed} pending order(s) confirmed')
