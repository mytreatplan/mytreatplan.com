---
title: 'Restore-safe invoice numbering'
type: 'feature'
ticket: '9'
created: '2026-11-22'
status: 'done'
baseline_revision: 'a6c3377bafe50e7965f8c38b2e223f3f4aca8fc2'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 1
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Each invoice and credit-note series keeps its counter in the portal database (`InvoiceSeries.last_number`). A database restore rewinds the counter, and the next invoice would reuse a number already issued and already in the admin's accounting copy (AD-7).

**Approach:** After a restore, the restore runbook runs a portal command before the site serves traffic. The command puts numbering on hold, asks the admin (through a new hub lookup) for the highest number of each of this instance's series, raises any counter that is behind, and lifts the hold. While the hold is on, issuing is refused. The admin also raises a Sentry alert if an `invoice.issued` event arrives with a number it already holds under another invoice.

Decisions (user, 2026-11-22):
- The check is an explicit after-restore command, not an automatic check on each issue.
- While issuing is refused, the paid transition rolls back as for any failed hook: the order stays pending payment, and the existing payment re-check confirms it once the hold lifts.
- Decision (user, 2026-11-22, after review): the webhook retries stop after ~2 h 42 min, so after lifting the hold `resume_invoice_numbering` re-checks every order still pending payment that has a live gateway attempt (re-read at the gateway; paid ones confirmed) and prints what it confirmed; `expire_stale` expires nothing while the hold is on.
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

## Plan Change Log

- Loop 1 (2026-11-22): triage #2 (intent_gap) resolved by the user's decision (a) above; frozen block amended with it. The existing code stays (the user chose to extend it, not re-derive): KEEP the hold model, command, lookup, admin duplicate refusal and tests as built; add the re-check and the expiry pause, plus the patches #1, #6-8, #9b, #10.

## Review Triage Log

Pass 1 (quick lens, Opus, 2026-11-22): high 2, medium 2, low 6, false 0, maybe-false 0.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | `payments/refunds.py` `refund()`: during the hold the gateway refunds, then `_settle` → `issue_credit_note` raises `NumberingOnHold`; refund left `needs_attention`, money gone, no credit note | high | patch | Confirmed: `_call_gateway` runs before `_settle` (refunds.py ~l.161-176) and nothing checks the hold. Fix: refuse before `_start` while the hold is on. |
| 2 | "Existing payment re-check confirms it once the hold lifts" is false for a long hold: webhook retries stop after `MAX_ATTEMPTS` (~2 h 42 min) with `report_attention('could not be confirmed')`; nothing re-runs confirmation when the hold lifts; `expire_stale` expires pending orders after 24 h | high | intent_gap | Confirmed in `payments/webhooks.py` l.46-206 and `payments/service.py:521`; the only other re-read is the return page. The frozen decision assumes a re-check that does not exist for holds longer than ~2.7 h. Asked the user. |
| 3 | Every refused confirmation during the hold logs an ERROR with traceback (one Sentry error per attempt) | low | reject | Real but bounded by the retry count; quieting it adds a branch in `_fire`. |
| 4 | Admin answer capped at `MAX_SERIES = 200`; series grow by year, so the lookup eventually 500s | low | reject | 3 companies × 2 kinds = 6 series a year: the cap is reached after ~33 years. Fix adds a year filter for no near-term harm. |
| 5 | A 7-digit sequence is skipped by `_SEQUENCE_RE` | low | reject | Needs over 999,999 invoices in one series-year. |
| 6 | Runbook has no `migrate` before `--hold-only`: a dump older than migration 0003 has no hold table | medium | patch | Confirmed: the section starts with `--hold-only`. Add the migrate step. |
| 7 | Runbook assumes services are stopped; in-place OVH restores in "Who restores" don't link here | medium | patch | Confirmed in deploy/README.md "Who restores". Link the section and say to run `--hold-only` at once if the site runs. |
| 8 | Runbook commands hard-code `/srv/mytp-dev` while the text covers every site | low | patch | Direct correction: use `<site>` placeholders. |
| 9 | Admin duplicate check is unlocked; two clashing events at once could both be stored | low | reject | A reused number arrives long after the original (only after a restore); a simultaneous clash is not met in practice, and the fix needs a unique constraint and migration. |
| 9b | `accounting/events.py` docstring does not mention the `duplicate_number` refusal | low | patch | Direct correction. |
| 10 | `test_a_restore_that_lost_two_invoices_raises_the_counter` consumes 000008 via `_next_number`, so no real invoice is shown as 000008 | low | patch | Direct correction: drop the `_next_number` call, assert the real invoice is 000008. |

## Staging

- Merged 2026-11-22: portal PR #125 → 631d9a6 (deploy run 45051223480 green on dev and dev-ae; CI suite 4035 passed, 1 skipped); mytp-admin PR #47 → a300e64 (deploy run 45051206033 green on admindev); mytp-core v0.32.0 tagged and pushed (CI green).
- Staging checks, run over SSH with the user's delegation (2026-11-22), all passed:
  1. dev with admindev up: `resume_invoice_numbering` checked 2 series (ES 2026 invoice and credit note, unchanged at 1), confirmed 0 pending orders, exit 0; hold off.
  2. admindev's gunicorn socket and service stopped: the command exited 1 ("unavailable (http_502)", "stays on hold"); hold on; `_create` refused with `NumberingOnHold`, invoice count unchanged.
  3. admindev started again (healthz `site: admindev`): the command answered, both series unchanged, hold lifted, exit 0.
  4. `hub_lookup invoice-series` on dev printed the admin's answer for region `eu` (two ES 2026 series at 1).
