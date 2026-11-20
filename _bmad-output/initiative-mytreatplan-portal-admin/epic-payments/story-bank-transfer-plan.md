---
title: 'Bank transfer'
type: 'feature'
ticket: '5'
created: '2026-11-19'
status: 'built'
baseline_revision: '3a147957397e5f33841916d322054d5022e95fe1'
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

**Problem:** Billing rules already offer `bank_transfer` (EU profile, the Iberia rule), but there is no adapter: a doctor cannot pay an order by transfer, staff cannot record one as paid, and nothing reminds or expires an unpaid transfer order (Y5; user decision 2026-10-30: reminder on day 7, expiry after 14 days).

**Approach:** A `bank_transfer` adapter with a 14-day attempt: Pay opens a page with the billing company's bank details, the amount and the order reference as the transfer concept; the order stays `pending_payment`. A service records a received transfer as paid (the admin button is epic-sales-finance's). A job sends one reminder on day 7; the existing expiry job expires the order when the 14 days end, firing the `expired` hook.

## Boundaries & Constraints

**Always:**
- Paid only through the new service, which settles the transfer attempt (`confirmed`) and calls `mark_paid` in one transaction, with `expected_version` and an actor (AD-22); idempotent on a repeat.
- The 7.2 frozen-order rule holds: one open attempt per order; the transfer attempt is the open attempt for its 14 days.
- Bank details are shown only for the order's own billing company; never logged, never sent to Sentry.
- Reminder idempotent on the business row (AD-8): one per attempt, even if the job runs twice or a run fails halfway.
- Plain-text email EN/ES in the doctor's language, `accounts` pattern (`translation.override`, `EmailMessage`, error class only on failure).

**Never:** matching bank statements or any bank API; the admin buttons; invoices or a "payment received" email (entry 6); partial payments; a transfer on the UAE site.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Choose transfer | checkout, ES doctor, company has bank details | attempt open 14 days, transfer page: holder, IBAN, BIC, bank, amount, reference, deadline date | No error expected |
| Not offered | company lacks bank details, or the rule limits transfer to other countries (entry 14) | `bank_transfer` not in the radios | No error expected |
| Return later | pending order, open transfer attempt | checkout and return pages lead to the same details, no "Try again" | No error expected |
| Switch to card | transfer attempt open, doctor picks Redsys | transfer attempt closed locally, card attempt opens | No error expected |
| Day 7 | attempt open ≥ 7 days, not reminded | one reminder email, `reminded_at` set | send failure: logged, retried next run |
| Day 14 | attempt open 14 days | next expiry run: attempt closed, order `expired`, hook once | No error expected |
| Mark paid | pending, open transfer attempt, amount = total | order `paid`, attempt `confirmed`, `paid` hook once | stale version → refused |
| Mark paid twice | already paid by this service | no change, same result | No error expected |
| Wrong amount / not pending | amount ≠ total, or order expired/cancelled | refused, nothing changes | error names the reason |
| Doctor cancels | pending, open transfer attempt | attempt `closed`, order `cancelled`, `cancelled` hook once | stale version → refused |
| Cancel with a card attempt open | pending, open Redsys attempt | refused as today (`AttemptOpen`) | on-screen message as today |

## Decisions (user, 2026-11-19: all a; 2026-11-20)

1. Bank details live on the billing company (built by entry 14). `bank_transfer` is offered only when the order's company has them all, and only in the countries its rule allows (entry 14; Spain only at launch).
2. The doctor may cancel a pending transfer order: Cancel closes the transfer attempt locally (`cancel_attempt`) and cancels the order (`cancelled` hook). A transfer that still arrives is refunded by staff by hand.
3. The day-7 reminder goes to the login email, in `User.language`.
4. Mark as paid is refused once the order is expired or cancelled; staff refund or re-sell by hand (deferred-work entry: reopening an expired transfer order).
5. (2026-11-20) Plan split: the bank details and per-country limits are entry 14, built first; this plan is the portal only.

</frozen-after-approval>

## Code Map

- `payments/gateways.py` -- `register(name, start, enabled=, **ops)` (`Adapter` dataclass :105, `session_lifetime`), `offered(order)` :170; `cancel_attempt` optional (without it `_cancel_live_attempts` raises `AttemptOpen`).
- `payments/fake.py`, `payments/redsys.py:399` `register()` -- adapter models; called from `payments/apps.py:33` `ready`.
- `payments/service.py` -- `start_payment` :389 (sets `session_expires_at` from `session_lifetime`), `mark_paid` :460, `cancel` :467 → `_move` :436 (raises `AttemptOpen` on live attempts), `expire_stale` :481 (`EXPIRY_HOURS=24`; skips orders with a live attempt, so a 14-day `session_lifetime` gives day-14 expiry), `_live_attempts` :519, `_set_payment_state`, `_cancel_live_attempts` :538, `confirm_payment` :609 (copy its locking/amount checks).
- `payments/views.py` -- `GATEWAY_LABELS` has `bank_transfer` (ES in the .po); `_go_to_gateway` :111; `checkout` :149; `_checkout_post` :169 (cancel action); `checkout_return` :211 (pending branch: "Try again", wrong for a transfer); `checkout_failure` :235.
- `payments/templates/payments/` -- `checkout.html` radios, `return.html`, `failure.html`.
- `payments/models.py` -- `Order.company_code` (no company id: look the company up with `BillingCompany.objects.filter(code=order.company_code).first()`), `Payment` :211; next migration `payments` 0004.
- `catalogue.BillingCompany.has_bank_details` and bank fields (entry 14).
- `accounts/password_reset.py:174-226`, `accounts/profile.py:322-350` -- email + `in_background` pattern; `User.language`.
- `deploy/jobs.conf:68` `expireorders` line format.
- `payments/tests/test_service.py` (`new`, `pay`, backdating via `.update`), `payments/tests/conftest.py`.
- `mytp_publicsite/tests/test_i18n.py:20` `TRANSLATABLE_FOLDERS` lacks `payments`.
- **Build setup:** branch `story/7.5-bank-transfer` is checked out (baseline 3a14795, mytp-core 0.29.0 already vendored by entry 14). Commit nothing, push nothing. Venv `../.venv` lacks the 0.29.0 wheel: use `/private/tmp/claude-501/portal-6.9-venv` (has it). Suite (repo root, sandbox off): `FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU=" DATABASE_URL="sqlite:///$TMPDIR/p75.sqlite3" /private/tmp/claude-501/portal-6.9-venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` (baseline 3805 passed / 13 skipped, 0 failures). After editing the .po, run `compilemessages` for the i18n tests.

## Tasks & Acceptance

**Execution (portal only, branch `story/7.5-bank-transfer` after entry 14 is merged):**
1. `payments/gateways.py`: optional `Adapter.accepts(order) -> bool`; `offered()` keeps a name only when its adapter has no `accepts` or `accepts(order)` is true.
2. `payments/bank_transfer.py` (new): `NAME='bank_transfer'`, `SESSION_LIFETIME=timedelta(days=14)`, `REMIND_AFTER=timedelta(days=7)`; `accepts`: the order's company (by code) `has_bank_details`; `start` returns the transfer page URL; `cancel_attempt` does nothing (the attempt is closed locally by the caller); `register()` from `apps.py` `ready`. No settings, no secrets.
3. `payments/service.py`: `_cancel_live_attempts(order_id, only=None)`; `cancel` first closes the order's open `bank_transfer` attempts, so other gateways still refuse with `AttemptOpen`. New `AmountMismatch(OrderError)` and `mark_transfer_paid(order, amount, *, expected_version, actor)`: actor admin or system; under the order lock refused unless pending, the open attempt is `bank_transfer` (else `NoPaymentMethod`) and `amount == order.total`; then attempt `confirmed` (+`confirmed_at`) and `mark_paid` in one transaction; already paid with a confirmed transfer attempt → order returned unchanged.
4. `Payment.reminded_at` (nullable) + migration `payments` 0004. `payments/transfers.py` `remind_due(now=None)`: per open `bank_transfer` attempt created ≤ now − 7 days, `reminded_at` null, order pending, session not expired: lock the row, re-check, send synchronously, set `reminded_at` only on success (failure: log error class + payment UUID; next run retries). Order without a user: marked reminded, no email. Command `remind_transfers` (prints a count only); `deploy/jobs.conf`: `remindtransfers | remind_transfers | *-*-* *:30:00 UTC`.
5. Email `payments/templates/payments/email/transfer_reminder.txt` + subject: reference, amount, bank details, deadline date, link to the transfer page; to `order.user.email` under `translation.override(user.language)`.
6. View `transfer_page` at `checkout/<uuid:pk>/transfer/` (`_doctor_order`): pending with an open transfer attempt → holder, IBAN grouped by 4, BIC, bank, amount, reference as concept, deadline date in the company's time zone, Cancel (posts `action=cancel` to checkout); otherwise → `checkout_return`. `checkout` GET and `checkout_return` redirect there while that attempt is open. Template `transfer.html`; ES strings in the .po; `payments` added to `TRANSLATABLE_FOLDERS`.
7. Tests `payments/tests/test_bank_transfer.py`: every matrix row; the job twice; a failing send; bank details absent from logs (`caplog`).

**Acceptance Criteria:**
- Given an ES order whose company has bank details and whose rule allows transfer in ES, when the doctor pays by transfer, then the transfer page shows the six details and a deadline 14 days on, and the order is `pending_payment`.
- Given a company without bank details or a PT doctor under an ES-only limit, when the checkout shows, then Bank transfer is not a choice.
- Given an attempt opened 7 days ago, when the job runs twice, then one email is sent; given the send fails, then the next run sends it.
- Given an attempt opened 14 days ago, when `expire_orders` runs, then the order is `expired` and its hook fired once.
- Given a pending transfer order, when `mark_transfer_paid` runs with the total, then it is `paid` and the attempt `confirmed`; again: unchanged; another amount, or an expired or cancelled order: refused, nothing changed.
- Given a pending transfer order, when the doctor cancels, then it is `cancelled`; with an open Redsys attempt the cancel is still refused.
- Given the full suite, then 0 failures and no missing migrations.

## Implementation Notes

- Built by a Sonnet implementer; Opus quick review: 8 findings, all patched (table below). Suite (main session) 3840 passed / 13 skipped; makemigrations and check clean. Migration `payments` 0004 (`Payment.reminded_at`); job `remindtransfers` (dev needs a re-provision).
- Deviations accepted: the reminder language is `Profile.language` (`accounts.profile.profile_of`; `User` has none), site `LANGUAGE_CODE` as fallback; `checkout/<id>/?change=1` plus a "Choose another payment method" link on the transfer page (the redirect would otherwise block the switch to card); a company that lost its bank details mid-attempt shows "details not available, contact us".
- From the review: an open transfer attempt is closed only inside the locked transaction after every check (a refused pay POST keeps it); a transfer attempt's deadline is capped at `pending_at + 14 days` (re-choosing transfer never extends it; refused once past); the reminder is due from `pending_at + 7 days`, once per order; the page and email show the last full day to pay (`session_expires_at - 1 day`, company time zone); `mark_transfer_paid` refuses another currency.

## Plan Change Log

- 2026-11-20: split (user): bank details on billing companies moved to entry 14 (with the SEPA creditor id and per-country gateway limits); this plan regenerated for the portal only.

## Review Triage Log

Quick lens (Opus), 2026-11-20:

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | `start_payment` closes an open transfer attempt (no-op cancel, own transaction) before the version/state/gateway checks: a refused pay POST loses the attempt, the order then expires after 24 h | high | patch | `_cancel_live_attempts` runs before `_check_version`; transfer `cancel_attempt` is a no-op so nothing guards the attempt |
| 2 | re-choosing transfer (or card then transfer) opens a fresh 14-day attempt: the deadline can be renewed for ever | medium | patch | `session_expires_at = now + lifetime`; nothing ties it to the order's first freeze (`pending_at`); intent says expiry after 14 days |
| 3 | page/email say "expires after {date}" but `expire_stale` expires during that date | medium | patch | date is `session_expires_at.date()`; expiry at that time of day |
| 4 | `mark_transfer_paid` drops a `Money` currency | low | patch | direct correction, as `confirm_payment` |
| 5 | README not updated (transfer page, cancel rule, second paying path, job, `reminded_at`, `accepts`) | low | patch | earlier entries kept README current |
| 6 | limit test has dead code and tests ES-under-PT instead of the AC's PT-under-ES | low | patch | direct correction |
| 7 | no tests for findings 1 and 2 | medium | patch | added with their fixes |
| 8 | three new lines over 99 chars | low | patch | baseline `payments/` has none |

## Verification

**Commands:**
- the full suite (local SQLite command in the README) -- expected: 0 failures.
- `manage.py makemigrations --check` and `manage.py check` -- expected: no changes, no errors.

**Manual checks (staging, dev; needs entry 14's staging data):**
1. Re-provision dev (new `remindtransfers` timer).
2. An ES test doctor's checkout offers Bank transfer; a PT one's does not.
3. A transfer order shows the bank details; day 7 and day 14 simulated by backdating the attempt in a rolled-back shell script (check72 pattern): one reminder email, then `expired` with the hook; another order marked paid through the service; a third cancelled by the doctor.
