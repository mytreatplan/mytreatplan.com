---
title: 'Restore-safe invoice numbering'
type: 'feature'
ticket: '9'
created: '2026-11-22'
status: 'ready-for-dev'
baseline_revision: ''
route: 'full'
route_source: 'auto'
risk: 'high'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Each invoice and credit-note series keeps its counter in the portal database (`InvoiceSeries.last_number`). A database restore rewinds the counter, and the next invoice would reuse a number already issued and already in the admin's accounting copy (AD-7).

**Approach:** After a restore, the restore runbook runs a portal command before the site serves traffic. The command puts numbering on hold, asks the admin (through a new hub lookup) for the highest number of each of this instance's series, raises any counter that is behind, and lifts the hold. While the hold is on, issuing is refused. The admin also raises a Sentry alert if an `invoice.issued` event arrives with a number it already holds under another invoice.

Decisions (user, 2026-11-22):
- The check is an explicit after-restore command, not an automatic check on each issue.
- While issuing is refused, the paid transition rolls back as for any failed hook: the order stays pending payment, and the existing payment re-check confirms it once the hold lifts.
- A new hub lookup ("invoice-series-highest"), added in a mytp-core minor release. Architecture AD-3 channel 4 is widened to name it.

## Boundaries & Constraints

**Always:**
- The hold is a database row in the portal, so it survives restarts. It is set first, and lifted only after every series has been raised.
- Counters only go up. A series the admin knows but the portal lacks is created at the admin's number.
- The lookup is made outside any series lock and any request. Only the command calls it.
- Refusal while on hold is a new `InvoiceError` subclass. Callers already roll back on `InvoiceError`.
- The lookup answers for the caller's region only (`caller_region`). The answer holds series keys and numbers only, no buyer data.
- The command is idempotent: running it twice, or with the admin's numbers below the portal's, changes nothing.

**Never:**
- No admin call during normal issuing.
- No change to the number format or to `_next_number`'s gapless locking.
- No automatic restore detection.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Restore lost 2 invoices | Portal series at 5, admin highest 7 | Counter raised to 7, hold lifted; next invoice is …-000008 | — |
| Nothing lost | Portal at 7, admin at 7 (or lower) | Counter unchanged, hold lifted | — |
| Series only in admin | Admin has a credit-note series the portal lacks | Series created at the admin's number | — |
| Admin down | Lookup raises `HubUnavailable` | Hold stays on, command exits non-zero with a clear message | Issuing refused; re-run later |
| Payment during hold | A gateway confirms payment while on hold | Paid transition rolls back (HookFailed), order stays pending payment | Confirmed later by the re-check |
| Duplicate at admin | `invoice.issued` arrives with a number already stored under another uid | Not applied; Sentry error logged with both uids | — |

</frozen-after-approval>

## Code Map

- `invoices/models.py:~49` `InvoiceSeries` (kind, company_code, instance, year, last_number; unique on those four). Add the hold model next to it.
- `invoices/service.py:84-108` `_next_number` (select_for_update, IntegrityError retry). `_create` l.114. Callers `issue_for_order` l.139 (paid hook), `issue_credit_note` l.190, `issue_manual` l.352. `InvoiceError` subclasses live here.
- `payments/service.py:275` `_fire` wraps hook errors as `HookFailed`, which rolls the transition back. `payments/views.py:154` shows "try again"; `payments/refunds.py:128` puts the refund in `failed`/`needs_attention`.
- `integration/hub.py` `lookup(path, reader)`: errors are `HubUnavailable`/`HubRefused` (`HubLookupFailed.code`). `integration/management/commands/hub_lookup.py` `LOOKUPS`. Test fake: `integration/tests/test_hub.py` `FakeAdmin`.
- `../mytp-core/mytp_core/contracts/hub.py`: `HUB_PREFIX`, `SampleAvailability`, `LOOKUP_READERS` l.100, `MAX_LOOKUP_BYTES`. `contracts/errors.py`. Core is at 0.31.0; this takes the next free minor (9.1 also needs one). Release flow per staging-ops.
- `../mytp-admin/interface/hub.py` (`@require_GET @signed_endpoint`, `caller_region`), `interface/urls.py:11`. The only lookup so far, sample-availability, is staging-only; the new one is public.
- `../mytp-admin/accounting/models.py:56` `AccountingInvoice` (number, kind, company_code, instance, issue_date; no year column). `accounting/events.py` is the `invoice.issued` applier.
- `deploy/README.md` (restore sections ~l.32/53/380), `deploy/restore-db.sh`.
- `_bmad-output/initiative-mytreatplan-portal-admin/architecture-portal-admin/architecture-portal-admin.md` AD-3 rule 4 (hub lookups), AD-7.
- Tests: `invoices/tests/test_invoices.py`, `test_credit_notes.py`; `../mytp-admin/accounting/tests/test_feed.py`; `../mytp-admin/interface/tests/test_hub.py`; `../mytp-core/tests/test_hub.py`.

## Tasks & Acceptance

**Execution:**
- [ ] `../mytp-core` -- Add the `invoice-series-highest` contract: path constant, closed answer model (a list of {company_code, instance, year, kind, highest} with bounds), reader in `LOOKUP_READERS`, and tests. Bump to the next free minor (0.32.0 unless 9.1 took it), commit and tag locally. The implementer does not push.
- [ ] `../mytp-admin/interface/hub.py`, `urls.py` -- Add the public lookup view. For the caller's region it returns the highest number per series from `AccountingInvoice`; the year comes from `issue_date` in the row's `time_zone`, the sequence is parsed from `number`. Add tests (region isolation, empty answer, signature required). Vendor that core release.
- [ ] `../mytp-admin/accounting/events.py` -- Refuse an `invoice.issued` whose number already exists under another uid, logging an error (Sentry), and test it.
- [ ] `invoices/models.py` + migration -- Add a singleton `NumberingHold` (on, set_at, reason).
- [ ] `invoices/service.py` -- `_create` raises `NumberingOnHold(InvoiceError)` while the hold is on. Add `resume_after_restore(answer)`, which raises counters in one transaction and lifts the hold.
- [ ] `invoices/management/commands/resume_invoice_numbering.py` -- Set the hold, call the lookup, then apply the answer. On a hub error, keep the hold and exit non-zero. Add `--hold-only` for the runbook's first step.
- [ ] `integration/management/commands/hub_lookup.py` -- Register the lookup in `LOOKUPS`.
- [ ] `requirements.txt` + `vendor/` -- Vendor that core release (`scripts/vendor-mytp-core.sh`).
- [ ] `deploy/README.md` -- Restore runbook: run `resume_invoice_numbering --hold-only` right after the restore, before starting the services; run `resume_invoice_numbering` once the admin is reachable.
- [ ] `architecture-portal-admin.md` AD-3 rule 4 -- Widen to "specialist availability and slot holds (AD-21), and the highest invoice numbers per series after a restore (AD-7)". When the admin site is down, booking is unavailable and, after a restore, invoicing too.
- [ ] Portal tests -- One for each matrix row, with `FakeAdmin`, plus a test that issuing during the hold raises `NumberingOnHold`.

**Acceptance Criteria:**
- Given a simulated restore (the portal counter set back by two), when the command runs with the admin's copy at the higher number, then the next invoice continues above the admin's highest.
- Given the admin unreachable right after a restore, when a payment is confirmed, then no invoice number is issued, and the order is paid only after the command succeeds.
- Given the three repos, when their suites run, then they have 0 failures (except the local WeasyPrint-library tests).

## Design Notes

Known gap, accepted: if an invoice issued in the seconds before the failure never reached the admin through the outbox, it is lost in both places and its number can be reused. Point-in-time recovery keeps that window to seconds. The duplicate alarm at the admin catches the other case: a number reused while the admin already holds it.

The parsing helper sits next to the admin's lookup. The sequence is the number's last six digits, after an optional `R`.

## Verification

**Commands:**
- Portal full suite (local-dev-notes) -- expected: 0 failures except the 8 local WeasyPrint tests.
- `../mytp-core`: `uv run --no-cache pytest -q`; `../mytp-admin` suite per staging-ops -- expected: 0 failures.

**Manual checks:**
- On staging, after a merge: run `resume_invoice_numbering` on dev with admindev up, and it reports each series. Stop admindev's app, run it again, and it exits non-zero with the hold on. Start admindev and re-run, and the hold is lifted.
