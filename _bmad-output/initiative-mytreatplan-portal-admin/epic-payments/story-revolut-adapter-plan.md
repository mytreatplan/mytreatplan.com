---
title: 'Revolut adapter'
type: 'feature'
ticket: '4'
created: '2026-11-22'
status: 'built'
baseline_revision: '4cb00274418431f3280b95ccae247720b7d937ad'
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

**Problem:** Irish doctors (billing company IE) cannot pay by card: the EU region offers `revolut`, but no adapter is registered, so the checkout refuses it.

**Approach:** A `revolut` adapter on entry 2's interface, against the Revolut Merchant API (sandbox first): a Revolut order per attempt and its hosted checkout page, signed webhooks through the shared handler with the payment re-read from Revolut, cancel of a live attempt, and refunds. The user types the sandbox secret key into dev's settings.env; the production key is a later go-live step.

## Boundaries & Constraints

**Always:**
- No gateway HTTP call while a database transaction or row lock is held.
- The order is paid only through `service.confirm_payment`, after `fetch_payment` read the Revolut order as `completed` with the expected amount and currency.
- Webhook signature: `Revolut-Signature` holds one or more comma-separated `v1=<hex>` entries; each is HMAC-SHA256 (signing secret) over `v1.{Revolut-Request-Timestamp}.{raw body}`, compared with `hmac.compare_digest`; the timestamp is Unix milliseconds, 5-minute tolerance. Malformed headers are `BadSignature`, never a 500.
- No key, signing secret, card data or personal data in logs, Sentry or URLs (AD-23); the Revolut settings join `MYTP_SENTRY_SENSITIVE_FIELDS`.
- Amounts as integer minor units through `Decimal`; EUR only (`accepts` refuses other currencies).
- Every URL sent to Revolut is built from `settings.SITE_URL`.
- Every Merchant API call sends `Authorization: Bearer <secret key>` and a pinned `Revolut-Api-Version: 2026-09-22`.

**Never:**
- No saved cards or repeat charges (entry 11), no SEPA (entry 15), no Revolut Pay widget or card field (hosted page only).
- No outgoing request signing (`Revolut-Api-Signature`): the key has no signing key.
- No change to the entry 2 interface, the Redsys adapter or the refund flow of entry 7.

## Decisions

- Decision (user, 2026-11-22): a command `revolut_webhook register|list|delete`, run by the user over SSH. `register` registers `SITE_URL/payments/webhook/revolut/` for `ORDER_COMPLETED`, `ORDER_FAILED`, `ORDER_CANCELLED` and prints the signing secret once, in the user's terminal only, for the user to type into settings.env (`REVOLUT_WEBHOOK_SECRET`). The secret is never written to a file or log.
- Decision (user, 2026-11-22): Revolut receives only the order reference (`merchant_order_data.reference`) and a neutral description ("MyTreatPlan order <reference>"): no doctor name, email or other personal data.
- Decision (user, 2026-11-22): a refund accepted by Revolut (201) counts as refunded, as with Redsys; entry 7's refund flow is unchanged. A refund that later fails at Revolut shows only in Revolut's dashboard; no refund-order webhooks.
- Decision (user, 2026-11-22): staging: I create an Irish test doctor with an IE fiscal entity on dev and check that admindev's IE billing rule offers `revolut`, adding it if not (command actor `staging-7.4`). The user types `REVOLUT_SECRET_KEY` into dev's settings.env and runs `revolut_webhook register`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Start | pending EUR order, IE doctor, adapter enabled | `POST /api/orders` (amount in cents, EUR, reference = order reference, `redirect_url` = our return URL, `expire_pending_after` = session lifetime); attempt stores the Revolut order id; browser goes to `checkout_url` | Revolut error or timeout → `GatewayError` ("try again"), no attempt left live |
| Paid | `ORDER_COMPLETED` webhook, valid signature | log accepted; task GETs the order: `completed`, amount and currency match → order paid, hook once | mismatch → not paid, logged |
| Failed / cancelled | `ORDER_FAILED` or `ORDER_CANCELLED` | re-read `failed`/`cancelled` → attempt failed; order payable again | — |
| Bad webhook | wrong signature, stale timestamp, malformed header, unknown order id | refused as the shared handler does (403/400/200-ignored) | rate limit as for Redsys |
| Duplicate | same event resent | DUPLICATE row, 200 | — |
| Retry while live | doctor starts again | `cancel_attempt` POSTs `/cancel` on the pending Revolut order, then a new one | cancel refused (already completed) → existing re-check decides |
| Refund | completed order, amount ≤ paid | `POST /api/orders/{id}/refund` → refund order id returned | 4xx or 200 with `decline_reason` → `GatewayError`; timeout → `GatewayUnknown` |
| Public site on sandbox | `REVOLUT_ENVIRONMENT=sandbox` on a public site | system check error | startup refused as for Redsys |

</frozen-after-approval>

## Code Map

- `payments/gateways.py` -- `register(name, start, enabled, **operations)`, `OPERATIONS`, `Adapter` (`session_lifetime`, `timestamp_tolerance`, `required_settings`, `accepts`), `WebhookEvent`, `GatewayPayment`, errors (`GatewayError`, `GatewayUnknown`, `BadSignature`, `Malformed`, `Stale`), `check_timestamp`. Reuse; do not change.
- `payments/redsys.py` -- the model adapter (406 lines): `start` l.224 with `service.set_gateway_payment_id`, `to_cents`/`from_cents` l.201-213, `refund` l.351 (30 s timeout, `GatewayUnknown` on fetch errors), `register()` at the end. New `payments/revolut.py` mirrors its shape; `start` returns Revolut's `checkout_url` directly (a redirect, no form page).
- `payments/apps.py:16-36` -- import and `register()` the adapter; mirror the `ImproperlyConfigured` startup check.
- `payments/webhooks.py:94` `handle` (64 KB cap, bad-signature rate limit, `WebhookLog` unique on (gateway, event_id), enqueues `tasks.confirm_webhook` which calls `fetch_payment`). Revolut's payload is `{event, order_id, merchant_order_ext_ref}` only: event id `"{order_id}:{event}"`, status None, so the task always re-reads. `webhooks.py:173` special-cases Redsys refund notices: a webhook for a refund order (unknown `order_id`) is logged and ignored.
- `payments/views.py:54` `GATEWAY_LABELS['revolut']` exists; `checkout_return` l.229 re-reads once on return. Return URL: the existing `checkout_return`.
- `payments/service.py:578` `_cancel_live_attempts` calls `cancel_attempt`.
- `accounts/http.py` -- urllib helpers only (`get_text`, `post_json`…), no custom headers, require status 200. Add a small JSON request helper accepting method, headers and 200/201, used only by Revolut.
- `mytreatplan/settings.py:135-148, 260` -- add `REVOLUT_SECRET_KEY`, `REVOLUT_WEBHOOK_SECRET` (comma-separated during rotation), `REVOLUT_ENVIRONMENT` (`sandbox`/`production`, default `sandbox`); sensitive fields. Base URLs `https://sandbox-merchant.revolut.com`, `https://merchant.revolut.com`.
- `payments/checks.py` -- E003 covers `required_settings` (key and webhook secret); add E005 for sandbox on a public site.
- Revolut order states: `pending`, `processing`, `authorised` → PENDING; `completed` → PAID; `failed`, `cancelled` → FAILED. Retrieve `GET /api/orders/{id}` (`state`, `amount`, `currency`, `type`). Cancel `POST /api/orders/{id}/cancel` (pending only). Refund body `{amount, currency, merchant_order_data.reference}` with an `Idempotency-Key`; answer 201 = a new order of `type: refund`. Webhooks: `POST /api/webhooks` `{url, events}` returns `{id, url, events, signing_secret}`. Source: Revolut's OpenAPI spec `merchant-2026-09-22.yaml` (github.com/revolut-engineering/revolut-openapi) and the signature helper in github.com/revolut-engineering/revolut-checkout-example.
- Tests: model on `payments/tests/test_redsys.py` (`FakeRedsys` replaces `accounts.http.post_json`; signature vectors, stale, duplicate, mismatch, refund unknown vs refusal, checks, sensitive fields). `payments/tests/test_checkout.py:179` expects `revolut` refused: rework it for a disabled adapter.
- Docs: `settings.env.example:119-127`, `deploy/staging.env.example:111-118`, `README.md` "Payments: Redsys" l.487 (add "Payments: Revolut"), `deploy/README.md` webhook registration.

## Tasks & Acceptance

**Execution:**
- [ ] `accounts/http.py` -- JSON request helper (method, headers, accepted statuses, timeout) -- Revolut needs Bearer, version header, GET and 201.
- [ ] `payments/revolut.py` -- adapter: `start`, `verify_webhook`, `fetch_payment`, `cancel_attempt`, `refund`, `register()`; `session_lifetime` 30 min.
- [ ] `payments/apps.py`, `payments/checks.py`, `mytreatplan/settings.py` -- registration, settings, E005, sensitive fields.
- [ ] `payments/management/commands/revolut_webhook.py` -- `register` (prints the signing secret once), `list`, `delete`, per the decision.
- [ ] `payments/tests/test_revolut.py` -- one test per matrix row, with a fake Revolut on the HTTP helper; signature vectors computed from the documented format; rework `test_checkout.py:179`.
- [ ] Example env files, `README.md`, `deploy/README.md` -- settings and the registration steps.

**Acceptance Criteria:**
- Given the sandbox key on dev and an Irish doctor with an IE fiscal entity, when they pay a test order on Revolut's sandbox page with a test card, then the webhook arrives, the order is paid and its invoice is issued.
- Given the portal suite, when it runs, then 0 failures (except the local WeasyPrint tests).

## Design Notes

`fetch_payment` is a real `GET`, unlike Redsys, so a lost webhook is recovered by the existing return-page re-read and the payment re-check. Revolut retries a webhook three times, 10 minutes apart.

## Verification

**Commands:**
- Portal full suite (local-dev-notes) -- expected: 0 failures except the 8 local WeasyPrint tests.

**Manual checks:**
- Staging, after the user has typed `REVOLUT_SECRET_KEY` into dev's settings.env and the webhook is registered: a sandbox payment by an Irish test doctor is paid by webhook; a sandbox refund of it is accepted; a second start cancels the first Revolut order.

## Review Triage Log

Quick lens (Opus), 2026-11-22. Revolut's request/response shapes were checked against merchant-2026-09-22.yaml before review.

| # | Finding | Verdict | Evidence | Route |
|---|---------|---------|----------|-------|
| 1 | `refund()` read `decline_reason` at the top level; a declined/failed Revolut Pay refund (HTTP 200) counted as refunded | high | Spec examples Res-Refund-Declined/Failed put it in `payments[]`; credit note would issue with no money moved | patch: refused when any `payments[]` entry is declined/failed or has a reason; test fake fixed |
| 2 | A second Pay could close an attempt whose `start` was in flight; `start` then sent the doctor to a live Revolut page | high | `cancel_attempt` returned with no id, `set_gateway_payment_id` does not check state; card charged, order NOT_PAYABLE | patch: `start` re-reads the attempt after storing the id, cancels the new Revolut order and raises if it is closed |
| 3 | `_go_to_gateway` failed attempts whose Revolut order could still be paid (GatewayUnknown on GET, processing/authorised) | medium | Existing-id branch raised for those cases and the view called `fail_payment` | patch: fail only with no stored id, else redirect to the return page; processing/authorised return the return URL |
| 4 | Session lifetime equal to `expire_pending_after`, portal clock starting earlier | medium | `session_expires_at` set in `start_payment` before the Revolut order exists; late completion lands on a closed attempt | patch: 35 min session, `PT30M` expiry, `extend_session` on redirect (as Redsys) |
| 5 | Refund `Idempotency-Key` identical for every attempt of a payment | medium | Staff retry after a refusal would replay Revolut's stored refusal | patch: the pending `Refund` row's `key`, else uuid4 |
| 6 | `request_json` followed redirects with the Bearer key | low | urllib's redirect handler copies Authorization; POST→GET could read as success | patch: no-redirect opener (direct small change, security-relevant) |
| 7 | Unknown order id answered 404, matrix row says 200-ignored | low | Shared handler answers 404 for unknown ids; only effect is Revolut retrying webhooks of refund orders 3 times | reject (shared handler unchanged per plan; fix would add a branch to it); noted in deferred-work.md to check at staging |
