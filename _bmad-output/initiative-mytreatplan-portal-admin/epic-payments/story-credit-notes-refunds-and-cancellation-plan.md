---
title: 'Credit notes, refunds and cancellation'
type: 'feature'
ticket: '7'
created: '2026-11-21'
status: 'done'
baseline_revision: 'e73402a75699a69550eb19de7c2fe53bce6d053f'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A paid order cannot be undone. Nothing records a refund, no credit note exists, and staff have no service to cancel an unpaid order. The only refund path is the `redsys_refund` command, which moves money but changes nothing in the portal (Y6, AD-7).

**Approach:** A staff refund service. It records a `Refund` before calling the gateway, refunds through the gateway (or records a refund made by hand), then issues a credit note and moves the order to `refunded` under the order lock. Credit notes are immutable invoices of kind `credit_note`, numbered in their own series `<company>-<instance>-<year>-R<6 digits>`. Staff cancel unpaid orders through the existing `cancel` with an admin actor. A new `refunded` hook lets vouchers and included cases react later.

## Boundaries & Constraints

**Always:**
- Only staff or the system refund (`admin_actor`, `SYSTEM_ACTOR`); a doctor actor raises `InvalidActor`. The cancel service for staff refuses a doctor actor in the same way.
- A paid order leaves `paid` only through a refund. `cancel` and `expire` stay limited to open states.
- The `Refund` row is written and committed as `pending` before the gateway is called, outside the order lock. The credit note and the state change are written in one transaction under the lock after the gateway answers. A gateway timeout or an unknown answer leaves the refund in `needs_attention`, with a Sentry message naming the order by UUID only. It is never retried automatically: Redsys may already have refunded.
- At most one pending refund per order (a database constraint).
- A credit note copies the original invoice: the seller and buyer snapshots, the lines and the VAT groups, with negated amounts. It references the original by number and issue date, and carries a reason. It is append-only under the invoice guard and has the same e-invoice fields.
- The credit-note counter is separate from the invoice counter, with the same lock, the company time zone and a yearly restart.
- A Redsys notification of type 3 (refund) is logged and marked done, never retried.

**Never:** admin screens or buttons (epic-sales-finance); the accounting feed or a core contract (7.8); the PDF (7.10); releasing vouchers or giving back included cases (receivers on `refunded` belong to their own stories); Revolut (7.4); any change to pricing.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Redsys refund | paid ES order, staff refunds | gateway refund; `ES-EU-2026-R000001`, negated totals; order `refunded`; `refunded` hook fired | — |
| Fake gateway refund | paid order on dev | as above | — |
| Bank-transfer order | staff records a refund made by hand, with a note | refund `done` with no gateway call; credit note; order `refunded` | — |
| Gateway refuses | Redsys answers with an error | refund `failed`; no credit note; order stays `paid` | `RefundFailed` |
| Gateway timeout | no answer | refund `needs_attention`; no credit note; Sentry | — |
| Repeat call | refund while one is pending, or order already `refunded` | no second refund | `RefundPending` / `InvalidTransition` |
| Doctor actor | doctor calls refund or the staff cancel | refused | `InvalidActor` |
| Staff cancel | unpaid order, no live card attempt | `cancelled`; `cancelled` hook | `AttemptOpen` while a card session is live |
| Paid order cancel | `cancel` on a paid order | refused | `InvalidTransition` |
| Year boundary | refund 2026-12-31 23:30 UTC, ES | `…-2027-R000001` | — |
| Concurrent credit notes | 3 refunds at once (PostgreSQL) | R000001–R000003, no gap | lock serialises |
| Refund notification | Redsys type-3 webhook | logged, done, no retry | — |

## Decisions (user, 2026-11-21)

- Full refunds only. The credit note is the whole invoice negated, and the order becomes `refunded`. Partial refunds are deferred until epic-sales-finance settles the refund conditions.
- Orders without a gateway refund (bank transfer, €0): staff record a refund made by hand, with a required note. The portal issues the credit note and marks the order `refunded`. A €0 order gets a €0 credit note with no money movement.
- A used voucher stays used after a refund. 7.7 only fires the `refunded` hook.
- A credit note stores a reason (`cancellation`, `goodwill`, `error`), the original invoice's number and issue date, and an empty `extensions["rectification_code"]` for the tax advisor. A deferred-work entry asks the advisor to confirm the R code and the rectification method before the EU go-live (7.12).
- `refund_order <reference> --reason … [--manual --note …]` replaces `redsys_refund`. `cancel_order <reference>` is added for staff. Both go through the services.
- Merge and staging are delegated. Claude merges after CI is green and runs the staging checks on dev over SSH: a fake-gateway refund, a manual refund, a staff cancel and the credit-note number, all in a rolled-back transaction. The user's answer is the SSH OK for these checks.
- The plan is kept whole although it is over 1600 tokens.

</frozen-after-approval>

## Code Map

- `payments/models.py` -- `Order.STATES` :43-50 (add `refunded`, ≤16 chars; `refunded_at`); `ACTOR_TYPES` :30; `OrderChange` :180 (event gains `refunded`); `Payment` :215 (state `confirmed`, `gateway`, `gateway_payment_id`); `WebhookLog` :261. Next migration `payments` 0006. New `Refund` model: order FK, payment FK null, amount, currency, state `pending|done|failed|needs_attention`, `manual` bool, note, reason, `gateway_refund_id` (not unique: Redsys repeats it per payment), `key` UUID, actor type and uuid, timestamps, credit note link; partial unique on `(order)` where state = pending.
- `payments/service.py` -- `_check_actor` :125, `admin_actor`, `SYSTEM_ACTOR`; `_lock` :276, `_stamp` :255, `_change` :261, `_fire` :267, `_move` :452 (allowed states, already-in-target return, `StaleVersion`); `cancel` :483 (open states only); `mark_transfer_paid` :679 (the pattern for refusing a doctor actor); `_cancel_live_attempts` (the gateway call outside the lock); `report_attention` :627 (Sentry by UUID).
- `payments/hooks.py:16` -- `EVENTS`: add `refunded` and update the docstring. `payments/tests/conftest.py` `events` picks it up.
- `payments/gateways.py` -- `refund(payment, amount) -> str` :22; `supports`, `call`, `UnsupportedOperation`, `GatewayError`. `payments/fake.py:134` `refund`. `payments/redsys.py:351` `refund` (REST type 3, 30 s timeout via `accounts.http.post_json`; over-refund SIS0057, duplicate within about 1 min SIS0295); `verify_webhook` :302-321 (type ≠ 0 → `status=None`). `payments/webhooks.py` + `payments/tasks.confirm_webhook`: a status-less accepted row is retried and ends in Sentry, so short-circuit it.
- `payments/bank_transfer.py` -- no `refund` op: the manual path. `payments/management/commands/redsys_refund.py` -- the current refund command (its docstring says "no credit note: entry 7").
- `invoices/models.py` -- `InvoiceSeries` :46 (unique company, instance, year: add `kind` invoice|credit_note, default invoice, and widen the unique constraint); `Invoice` :63 (`kind` order|manual: add `credit_note`; `order` OneToOne stays for invoices only, null on credit notes; add `original` FK to Invoice, PROTECT, null; `reason`); `InvoiceLine` :162; the append-only guard and `EINVOICE_FIELDS`.
- `invoices/service.py` -- `_next_number` :82 (format; take a kind and put `R` before the digits); `_create`; `_check_line` refuses a negative net, so credit notes build their lines from the original and do not go through it; `issue_for_order`, `issue_manual` :254, `set_einvoice_status` :295. Next migration `invoices` 0002.
- Planning: AD-7 (architecture :136); deferred-work :188-189 (record each refund; Redsys's refund id is not unique) and :197; the 8.4 plan leaves consumed vouchers to 7.7.
- Tests: `payments/tests/conftest.py` (`catalogue_loaded`, `fake_on`, `doctor`, `entity`, `events`); `test_service.py:24-36` (`actor`, `line`, `new`, `pay`); `test_bank_transfer.py` (`bank` :33, `staff()` :57, `start_transfer`); `test_redsys.py` (`redsys_on`, `sentry`, `notify(…, transaction_type=3)`, `FakeRedsys` :457, `paid` :486, `refund` :495); `invoices/tests/test_invoices.py` (€0 and transfer invoice :208-219, append-only :236/:269, concurrency :366 PG-only).
- **Build setup:** 8.4 is merged (master 96bfe91: `subscriptions.vouchers` registers receivers on `created`, `paid`, `cancelled` and `expired`; mytp-core 0.30.0 is vendored). Branch `story/7.7-credit-notes-refunds` is checked out in this repo (baseline e73402a). Portal only. Commit nothing, push nothing. Venv `/private/tmp/claude-501/portal-8.4-venv` (has 0.30.0). Suite (repo root, sandbox off): `FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU=" DATABASE_URL="sqlite:///$TMPDIR/p77.sqlite3" <venv>/bin/python -m pytest -q -p no:cacheprovider --tb=line` (baseline 3903 passed / 15 skipped, 0 failures); also `manage.py makemigrations --check`.

## Tasks & Acceptance

**Execution:**
- [ ] `payments/models.py`, `payments/migrations/0006_…` -- the `refunded` state, `refunded_at`, the `Refund` model -- one record per refund, with its own key.
- [ ] `invoices/models.py`, `invoices/migrations/0002_…` -- series `kind`, `Invoice.kind=credit_note`, `original`, `reason` -- the credit note's place in the invoice record.
- [ ] `invoices/service.py` -- `issue_credit_note(invoice, *, reason, actor)`: negates the lines, groups and totals, takes the next R number, and is idempotent per refund -- one place for numbering.
- [ ] `payments/refunds.py` -- `refund(order, *, expected_version, actor, reason, manual=False, note='')`: checks the actor, state and pending refund; commits the `pending` row; calls the gateway outside the lock, or skips it when manual; then, under the lock, marks the refund `done`, issues the credit note, moves the order to `refunded` and fires `refunded`. A failure marks `failed` and raises `RefundFailed`; a timeout or unknown answer marks `needs_attention`. Add `staff_cancel(order, *, expected_version, actor)`, which refuses a doctor and calls `cancel` -- the services the admin buttons will call.
- [ ] `payments/hooks.py` -- the `refunded` event.
- [ ] `payments/webhooks.py` or `payments/tasks.py` -- mark a status-less (refund) notification done, without a retry.
- [ ] `payments/management/commands/refund_order.py`, `cancel_order.py`; delete `redsys_refund.py` and move its tests -- `refund_order <reference> --reason {cancellation,goodwill,error} [--manual --note TEXT]` and `cancel_order <reference>`, both acting as the system actor through the services and printing the outcome (refund state, credit-note number) -- for staff and staging until the admin buttons exist.
- [ ] `_bmad-output/initiative-mytreatplan-portal-admin/deferred-work.md` -- two entries: partial refunds (wait on epic-sales-finance's refund conditions); confirm the R code and the rectification method with the tax advisor before 7.12.
- [ ] `README.md` -- what 7.7 delivers.
- [ ] `payments/tests/test_refunds.py`, `invoices/tests/test_credit_notes.py` -- every matrix row; immutability of credit notes; the PG-only concurrency test; the old invoice unchanged.

**Acceptance Criteria:**
- Given a paid ES order refunded on dev through the fake gateway, then a credit note ES-EU-2026-R000001 references the order's invoice, its totals are the invoice's negated, and the order is `refunded`.
- Given a refund whose gateway call timed out, then no credit note exists, the order is still `paid`, and a second refund is refused until staff resolve the first.

## Implementation Notes

- Sonnet implementer; suite before review 3941 passed / 16 skipped / 0 failed. It ran the concurrency tests on a local PostgreSQL 17. After the review patches, my suite: 3943 passed / 17 skipped / 0 failed; makemigrations clean.
- Choices made during the build (kept): `gateways.GatewayUnknown`, a subclass of `GatewayError`, lets the service tell a refusal from an unknown outcome. `Invoice.kind` is widened to 12 characters. The refund-notice short-circuit applies to Redsys only, because the fake gateway's events also carry no status.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, Opus, 2026-11-21):** 9 findings: 0 high, 4 medium, 5 low. 7 patched by the implementer, 1 deferred, 2 rejected (one finding was partly patched and partly rejected).

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | A refunded order reads "This order expired" on the return page; `checkout` renders an empty page | medium | patch | `return.html` has no `refunded` branch. Fix: a branch with its text (EN/ES) and a redirect like `paid`, plus view tests. |
| 2 | Inside a caller's `atomic()` the pending row is not committed before the gateway call, which then runs under the order lock | medium | patch | Plan Always rule. The admin buttons will be the first callers. Fix: refuse inside an atomic block, the inverse of `subscriptions/service.py:391`, plus a test. |
| 3 | `issue_credit_note` accepts a doctor actor and runs outside a refund | low | patch (part) | Doctor actor: a direct refusal. "Outside a refund" is rejected: its only caller is `_settle`, and guarding it further adds checks for a call nobody makes. |
| 4 | A `needs_attention` refund can never be cleared; the README and the command promise a retry that does not exist | medium | patch + defer | Per plan, resolution belongs to sales-finance. Docs corrected; deferred-work entry: resolution is needed before the admin refund button ships. |
| 5 | A gateway disabled between the phases is reported as an unknown answer | low | reject | Needs a settings change in the seconds between the phases; the fix adds a guard. |
| 6 | Orders paid before 7.6 have no invoice, so they can never leave `paid` | low | reject | Staging only: no production orders exist before the go-live, and 7.6 decided no backfill. |
| 7 | `refund_order` prints a traceback for an `InvoiceError` | low | patch | A direct correction: catch it as a `CommandError`. |
| 8 | Stale docstrings (`redsys.refund`, `invoices/models.py`) | low | patch | A direct correction. |
| 9 | The concurrency matrix row is tested with `issue_credit_note` only, not 3 refunds; `_settle` locks the refund before the order | medium | patch | Matrix audit gap. Fix: a PG thread test through `refund`, and lock the order first. |

## Design Notes

- Why credit notes go in `Invoice`: 7.8 (feed), 7.10 (PDF) and UAE e-invoicing then read one table. `order` stays null on a credit note, so the one-invoice-per-order rule holds. The note reaches its order through `original.order`.
- The two phases follow 7.3's finding: write the intent first, call the gateway, then settle. A `needs_attention` refund is resolved by staff in the Redsys console; resolving it in the portal is sales-finance's.

## Verification

**Commands:**
- Portal suite (sandbox off; see local-dev command) -- expected: 0 failures
- `manage.py makemigrations --check` -- expected: no changes
- The concurrency test once on PostgreSQL -- expected: no gap

## Staging

- PR #121 merged as 5d9fad9 (feature commit f08abfb). The PR's CI passed on PostgreSQL, so the concurrency tests ran there. Master run 44903707278: test and the dev and dev-ae deploys green.
- My local suite before the PR: 3943 passed, 17 skipped, 0 failed (SQLite).
- Checks passed 2026-11-21 (Claude, over SSH, script check77.py). Everything ran in one rolled-back transaction, so no refund, credit note or order remains. `refund` refuses to run inside a transaction, so for this script only the module's view of the connection was shimmed, and its phases ran as savepoints.
  1. Migrations `payments` 0006 and `invoices` 0002 applied on dev and dev-ae.
  2. dev: order JA-782408, paid through the fake gateway, got invoice ES-EU-2026-000001 (80.00). A staff refund was `done` with a fake refund id. Its credit note was ES-EU-2026-R000001, kind `credit_note`, total -80.00, original ES-EU-2026-000001, reason `cancellation`, `rectification_code` empty. The order became `refunded` and the invoice was unchanged. A second refund was refused (`InvalidTransition`).
  3. A refund by a doctor was refused (`InvalidActor`). A refund recorded by hand, on a second fake-paid order, was `done` with credit note R000002. dev has no bank details on the ES company, so the hand refund was not run on a bank-transfer order; the tests cover that.
  4. A staff cancel of an unpaid order: `cancelled`, actor admin. A staff cancel by a doctor was refused. Cancelling a paid order, or one already refunded, was refused (`InvalidTransition`).
- Left on dev: only fake-gateway cache entries, which expire. No database rows.
