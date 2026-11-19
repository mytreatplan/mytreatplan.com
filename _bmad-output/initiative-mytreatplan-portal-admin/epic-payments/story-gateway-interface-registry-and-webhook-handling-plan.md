---
title: 'Gateway interface, registry and webhook handling'
type: 'feature'
ticket: '2'
created: '2026-11-18'
status: 'done'
baseline_revision: '56b4870edeaf945aa0b71e5d0388199fd497bdde'
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

**Problem:** 7.1's adapter interface only starts a payment, and the fake gateway marks orders paid from its own page. Real gateways confirm by webhook from the internet; before Redsys and Revolut (entries 3, 4) the portal needs one hardened way to accept, check, deduplicate and log those notifications, and an interface that also covers refunds and saved cards.

**Approach:** Extend `payments.gateways` into the full adapter interface (start, verify a webhook, re-read a payment, refund, save a card, charge a saved card) with a payment record per attempt, a public webhook endpoint per gateway, shared handling (signature, timestamp tolerance, re-read, amount and currency match, dedupe on the gateway's id, a log row for every webhook), and the fake adapter rebuilt on it (its Pay sends a signed webhook). Portal only, no mytp-core release, no admin change.

## Boundaries & Constraints

**Always:**
- The order is paid only through `payments.service.mark_paid`, after the payment was re-read from the gateway and its amount and currency equal the order's total and currency.
- Dedupe by a database unique constraint on (gateway, gateway's event or payment id), not the cache.
- Every webhook, accepted or refused, writes one log row; refusals are `logger.warning` with the order UUID only. No raw signature, secret, card data or personal data in logs, Sentry or URLs (AD-23).
- A confirmed payment is matched to its own payment attempt (gateway and gateway id), not just to the order: since 7.1 a retry may switch the gateway, so a late success from an earlier attempt, or a second success, is handled like a payment for an order that is not payable (7.1 review finding 4).
- Secrets come from `settings.env`; a public site with a gateway enabled and its secret missing fails a system check.

**Never:**
- No Redsys, Revolut or bank-transfer adapter (entries 3–5); no invoice, credit note or refund service (entries 6, 7); no admin pages (epic-sales-finance).
- Webhooks never under `/api/` (nginx limits it to the admin site's IPs).

## Decisions

- Decision (user, 2026-11-19): a webhook is checked (signature, timestamp, duplicate), stored as a log row and answered 200 at once; a `django.tasks` task re-reads the payment from the gateway and confirms it, with attempts and next try on the row (AD-8).
- Decision (user, 2026-11-19): a `Payment` row per attempt: order, gateway, the gateway's payment id (unique per gateway), amount, currency, state, timestamps; refunds (7.7), the accounting feed (7.8) and saved cards build on it.
- Decision (user, 2026-11-19): the webhook log keeps gateway, time, IP, result (accepted, duplicate, bad_signature, stale, amount_mismatch, unknown, not_payable), event and payment ids, amount, and the body's SHA-256 and size, never the raw body; kept 180 days, purged by a daily job.
- Decision (user, 2026-11-19): the order is checked payable before the doctor goes to the gateway, and it cannot stop being payable while a payment attempt is open: (1) Pay checks the order (7.1); (2) while a `Payment` attempt is open the order is frozen: the doctor cannot cancel it and the expiry job skips it until the attempt's gateway session has timed out (a per-adapter session lifetime); (3) one open attempt per order: a retry or a second tab first closes the previous attempt at the gateway (adapter `cancel_attempt`; a gateway without it waits for its session to time out), so two payments cannot both succeed. Immediate charge, no card holds. A confirmed payment that still arrives for an order already paid, cancelled or expired (a gateway fault) raises an error to Sentry by order UUID, shows the doctor an error message, and marks the payment `needs_attention` for staff to check and refund by hand if money moved.
- Decision (user, 2026-11-19): the adapter interface defines start, verify webhook, re-read payment, refund, save a card and charge a saved card now; the fake adapter implements all; a real adapter declares what it supports.
- Decision (user, 2026-11-19): the fake page's Pay sends a signed webhook through the same handler (`PAYMENTS_FAKE_WEBHOOK_SECRET`), and a staging command `fake_webhook <reference> [--bad-signature|--stale|--amount X|--replay]` sends each refusal on purpose.
- Decision (user, 2026-11-19): timestamp tolerance 5 minutes, overridable per adapter; where a gateway sends no timestamp, the signature, the re-read and the dedupe carry the check.
- Decision (user, 2026-11-19): no IP allowlist; webhook bodies limited to 64 KB; a per-IP limit on failed signatures; every refusal logged.
- Decision (user, 2026-11-19): the return page re-reads the payment from the gateway once through the same confirm path; otherwise it shows "waiting for confirmation" with a refresh.
- Decision (user, 2026-11-19): Sonnet implementer, Opus security reviewer; merge and staging delegated (the user still says "merge N" and OKs SSH writes); staging uses the fake gateway and `fake_webhook`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Valid webhook | fake, signed, fresh, amount = total | payment confirmed, order `paid`, `paid` hook once, log accepted | — |
| Duplicate | same event id again | 200, nothing changes, log `duplicate` | — |
| Wrong signature | bad HMAC | 400/401, log `bad_signature`, order unchanged | warning |
| Stale | timestamp outside tolerance | refused, log `stale` | warning |
| Amount or currency mismatch | re-read amount ≠ total | refused, log `amount_mismatch`, order unchanged | error to Sentry by order UUID |
| Unknown order or payment | id not ours | refused, log `unknown` | warning |
| Gateway re-read fails | timeout / 5xx | webhook already answered 200; task retries with backoff on the log row; order stays `pending_payment` | after the last try: error to Sentry |
| Cancel while an attempt is open | doctor presses Cancel on the failure page | refused with a message; order unchanged | — |
| Expiry while an attempt is open | order past 24 h, attempt session not timed out | skipped this run; expired after the session timed out | — |
| Retry or second tab | attempt A open, doctor starts B | A closed at the gateway (or waited out), then B opened; only one open attempt | — |
| Payment for a paid, cancelled or expired order | gateway fault: confirmed payment arrives anyway | order unchanged, payment `needs_attention`, log `not_payable`, doctor sees an error message | error to Sentry by order UUID |
| Return page before the webhook | gateway says paid | re-read once → order `paid`, hook once (same path as the webhook) | — |
| Return page, gateway not yet paid | — | "waiting for confirmation" with refresh | — |
| Log purge | rows older than 180 days | deleted by the daily job | — |
| Body over 64 KB / failed-signature flood | — | refused 413 / 429 | logged |

</frozen-after-approval>

## Code Map

Portal; 7.1's `payments` app (branch `story/7.1-order-checkout`, PR pending) is the base: 7.2 builds after it merges (`payments` 0002).

- `payments/gateways.py` -- `Adapter(name, start, enabled)`, `register`, `adapter`, `offered`, `FAKE`: extend `Adapter` with the new operations; keep `offered` semantics.
- `payments/fake.py` -- fake adapter (flag `PAYMENTS_FAKE_GATEWAY`); rebuild on the interface.
- `payments/views.py:219-238` `fake_gateway` -- calls `mark_paid` directly today; must go through the webhook path.
- `payments/service.py:399-425` `_move`/`mark_paid(order, *, expected_version, actor)` -- replay of an already-paid order is a no-op before the version check; `SYSTEM_ACTOR`; `HookFailed` rolls back; `expire_stale` (24 h) may expire a `pending_payment` order.
- `payments/models.py` -- `Order` (`id`, `reference`, `total`, `currency`, `gateway`, `state`, `version`); no gateway payment id yet.
- `integration/calls.py:77-107` `signed_endpoint` (csrf_exempt, never_cache, JSON error) and `InterfaceCall` log + `purge_interface_calls` (90 days, job `purgeinterfacecalls`) -- patterns to copy, not reuse (peer-specific keys).
- `accounts/http.py` -- stdlib urllib helpers, `EXTERNAL_CHECK_TIMEOUT`, `FETCH_ERRORS`; add a header-capable helper for gateway re-reads.
- `eventlog.service.client_ip` -- the client IP for the log.
- `mytp_core/sentry.py` scrubbing; `MYTP_SENSITIVE_FIELDS` in `settings.py:231-256`.
- `payments/checks.py` -- add E003+ for gateway secrets; `settings.env.example` comments.
- `django.tasks` DB backend (`integration/tasks.py` pattern) -- AD-8: retries tracked on the business row.

## Tasks & Acceptance

**Execution:**
- [ ] `payments/models.py` + `0002` -- `Payment` (UUID pk, order FK PROTECT, gateway, `gateway_payment_id` unique per gateway when set, amount, currency, state: open / confirmed / failed / closed / needs_attention, `session_expires_at`, timestamps, attempts) and `WebhookLog` (gateway, received_at, ip, result, event id, payment id, amount, currency, body SHA-256 and size, `processed_at`, `attempts`, `next_try_at`, payment FK SET_NULL; unique (gateway, event id) for dedupe) -- per-attempt record and log.
- [ ] `payments/gateways.py` -- `Adapter` gains `verify_webhook(request) -> WebhookEvent` (raises `BadSignature`/`Stale`), `fetch_payment(payment) -> GatewayPayment`, `cancel_attempt`, `refund`, `save_card`, `charge_saved_card`, `session_lifetime`, `timestamp_tolerance` (default 5 min), `supports(op)`; keep `register`/`adapter`/`offered`.
- [ ] `payments/service.py` -- `start_payment` opens a `Payment` (closing or waiting out an open one); `cancel`/`expire`/`expire_stale` refuse or skip an order with an open attempt; `confirm_payment(payment, gateway_payment)` (amount and currency equal the order's → `mark_paid`; order not payable → `needs_attention` + Sentry error); same path for webhook task and return page.
- [ ] `payments/webhooks.py` + `urls.py` -- `POST /payments/webhook/<gateway>/` (outside i18n and `/api/`; csrf_exempt, never_cache; 64 KB cap; per-IP failed-signature limit); verify → dedupe → log → enqueue task → 200.
- [ ] `payments/tasks.py` -- `confirm_webhook(log_id)` task: re-read, confirm, attempts and backoff on the row.
- [ ] `payments/fake.py`, `payments/views.py` -- fake adapter implements every operation with an in-process store; its Pay posts a signed webhook through the handler; return page re-reads once; error message for `needs_attention`; Cancel refused while an attempt is open.
- [ ] `payments/management/commands/fake_webhook.py`, `purge_webhook_logs.py`, `deploy/jobs.conf` (`purgewebhooklogs` daily), `payments/checks.py` (E003 secret missing for an enabled gateway on a public site), settings + `settings.env.example` (`PAYMENTS_FAKE_WEBHOOK_SECRET`), ES .po, README "Payments: webhooks".
- [ ] `payments/tests/` -- every matrix row; the ticket's verify (valid once; replayed, wrongly signed, stale, mismatched refused and logged; duplicate ignored).

**Acceptance Criteria:**
- Given the fake gateway on dev, when `fake_webhook <ref>` and each refusal option are run, then the order is paid once and each refusal is a log row with its result and no state change.
- Given an open attempt, when the doctor tries to cancel or a second tab starts paying, then only one attempt is open and the order stays payable.

## Implementation Notes

## Plan Change Log

## Review Triage Log

Quick lens (Opus), iteration 0, 12 findings. Patches went back to the same implementer.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | `process()` locks the log row with `select_related('payment')`: FOR UPDATE on the nullable side of an outer join (PostgreSQL refuses it), and locks payment before order | high | patch | Known PostgreSQL refusal on a nullable FK join; the query is outside the try, so the task crashes and the webhook never pays on PG (SQLite hides it). |
| 2 | `process()` re-reads the gateway inside the transaction holding the log-row lock | medium | patch | `reread_and_confirm` runs inside `transaction.atomic()`; real adapters (7.3/7.4) would hold a DB transaction across an HTTP call. |
| 3 | `start_payment` calls `cancel_attempt` under the order/payment locks; a rollback after a successful cancel leaves the attempt `open` | medium | patch | `_close_attempts` runs inside `start_payment`'s atomic block after `_lock`. |
| 4 | A re-read that is still pending is never retried | medium | patch | `STILL_PENDING` takes the not-failed branch and sets `processed_at`; the order stays pending until the doctor reloads. |
| 5 | Fake `verify_webhook`: a non-ASCII signature raises TypeError → 500, no log row, counter skipped | medium | patch | Checked: `hmac.compare_digest('t=1,v1=é','abc')` → TypeError; `handle()` catches only BadSignature/Stale/Malformed. |
| 6 | The failed-signature counter's `incr` on DatabaseCache is not atomic | low | reject | Concurrent bursts lose a few increments; the limit still applies; making it atomic needs a new mechanism. |
| 7 | Fake `cancel_attempt` turns a paid payment into `cancelled` | low | patch | `set_state(..., 'cancelled')` runs whatever the status; a direct one-line refusal. |
| 8 | `fake_webhook` sets the fake payment paid even for `--bad-signature`/`--stale`; `--amount` unvalidated | medium | patch | The next return-page re-read pays the order after a "refused" webhook, against the first AC's "no state change". |
| 9 | 20 bad fake webhooks from 127.0.0.1 make the fake Pay button 429 for everyone | low | reject | Staging-only, needs 20 bad runs in 10 minutes; the return page's re-read still pays. |
| 10 | `report_attention` sends two Sentry events | low | patch | `mytp_core.sentry` keeps the SDK's default logging integration (only ignores listed loggers), so `logger.error` is its own event next to `capture_message`. |
| 11 | `checkout_return` calls the gateway on every GET; `except Exception: pass` hides failures with no log | low | patch (log only) | Doctor-only, own order; no throttle added. The bare pass is fixed with a warning log (order UUID + exception type). |
| 12 | Dead steps in the edited test `test_a_paid_hook_failure_on_the_fake_page_leaves_the_order_pending` | low | patch | Unregister/re-register of the same receiver does nothing; the assertion now tests the return page. |

## Staging

- Merged in #114 as e01ff20 (2026-11-19). The PR run needed two re-runs only for the Ubuntu apt mirror (Chromium, gettext); the push run of d369d40 and the master run 44529261891 passed and deployed dev and dev-ae (both at c87c4de). Local suite: 3716 passed, 13 skipped.
- dev and dev-ae re-provisioned: `mytp-<site>-job@purgewebhooklogs.timer` listed on both (daily 04:40 UTC) (check 1).
- Checks 2–7 on both sites by a script run in one rolled-back transaction (nothing kept): fake Pay → one accepted row, return page pays once, the task marks the row processed with no second paid; `--bad-signature` 401 and `--stale` 400 each one log row, order pending, the fake gateway not holding it paid; `fake_webhook` pays once and `--replay` gives a duplicate; `--amount 1.00` → order unchanged, payment `needs_attention`, row `amount_mismatch`, one Sentry error by order UUID per site; Cancel refused with "A payment for this order is in progress"; `purge_webhook_logs` deleted 0 rows.
- Not reported yet: the user seeing the two Sentry errors (check 5).
