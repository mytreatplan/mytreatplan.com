---
title: 'Redsys adapter'
type: 'feature'
ticket: '3'
created: '2026-11-19'
status: 'done'
baseline_revision: 'e01ff20ea941c9019db4d10abd97c90e41fa82ad'
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

**Problem:** Spanish and Portuguese doctors (company ES, 5.9's Iberia rule: `redsys` then `bank_transfer`) cannot pay by card: entry 2 built the gateway interface and webhook handling, but only the fake adapter exists.

**Approach:** A `redsys` adapter on entry 2's interface: the hosted payment page (redirection, HMAC_SHA256_V1), the signed online notification through the shared webhook handler, and refunds over Redsys REST. First on Redsys's public test terminal on dev; the company's test credentials, typed by the user into settings.env, are a later staging check. Saved cards and repeat charges stay unsupported until entry 11.

## Boundaries & Constraints

**Always:**
- No gateway HTTP call while a database transaction or row lock is held (entry 2's review).
- The order is paid only through `service.confirm_payment`, from a notification whose signature was verified with our key.
- Signatures compared with `hmac.compare_digest` on bytes; any malformed signature is `BadSignature`, never a 500.
- No key, signature, card data, `Ds_MerchantParameters` or personal data in logs, Sentry or URLs (AD-23); the Redsys settings join `MYTP_SENTRY_SENSITIVE_FIELDS`.
- Amounts as integer cents through `Decimal`, never float; currency `978` ↔ `EUR`.
- All URLs sent to Redsys (notification, UrlOK, UrlKO) are built from `settings.SITE_URL`, never the request host.

**Never:**
- No saved cards or repeat charges (`save_card`, `charge_saved_card` unset), no order state change on refund, no credit note, no admin-site action (entry 7).
- No SOAP query of operations; no change to the entry 2 interface or `_go_to_gateway`.

## Decisions

- Decision (user, 2026-11-19): the verified signed notification is the record of the payment. `WebhookEvent` and `WebhookLog` gain a `status` (paid/failed; migration 0003); Redsys's `fetch_payment` returns the latest verified notification of that attempt (status, amount, currency), or PENDING when none. A lost notification leaves the order pending until expiry; staff can check the Redsys admin module.
- Decision (user, 2026-11-19): `start` returns our own URL: a login-required, owner-only page that renders an auto-submitting POST form to Redsys's `realizarPago` (with a no-JavaScript button), as the fake page does.
- Decision (user, 2026-11-19): a CSRF-exempt return view accepting GET or POST answers 303 to the existing return or failure page; posted data is ignored.
- Decision (user, 2026-11-19): `Ds_Merchant_Order` is a fresh random id per attempt (4 digits + 8 of `[0-9A-Z]`), redrawn on a unique-constraint collision, stored as the attempt's `gateway_payment_id`.
- Decision (user, 2026-11-19): the event id is `Ds_Order:Ds_Response`.
- Decision (user, 2026-11-19): the notification's `Ds_Date`/`Ds_Hour` are read in Europe/Madrid and checked with a 30-minute tolerance for this adapter.
- Decision (user, 2026-11-19): Redsys has no `cancel_attempt`; a retry is refused ("payment in progress") until the session times out or a refusal notification closes the attempt. `session_lifetime` 30 minutes, measured on the test terminal and adjusted in this story.
- Decision (user, 2026-11-19): refunds through the adapter's `refund` and a command `redsys_refund <order reference> [--amount X]` (default the full amount); no order state change.
- Decision (user, 2026-11-19): settings `REDSYS_MERCHANT_CODE`, `REDSYS_TERMINAL`, `REDSYS_SECRET_KEY`, `REDSYS_ENVIRONMENT` (`test`/`production`, default `test`); the adapter is enabled when code and key are set; `required_settings` lists the first three (E003); a new check refuses `test` or Redsys's public test key on a public site.
- Decision (user, 2026-11-19): `deploy/nginx/site.conf.template` gets `location ^~ /payments/webhook/` without basic auth (modelled on `/api/`, 64 KB body cap); dev and dev-ae are re-provisioned over SSH by the user (or by me with the user's OK at that time).
- Decision (user, 2026-11-19): the story is done on the public test terminal; Santander's test credentials, once the user has them, are a later staging check.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Start | pending order, Iberia doctor, adapter enabled | `Payment` with a new 12-char Ds_Order; redirect to our page; form posts to `realizarPago` with amount in cents, 978, our three URLs, a valid signature | — |
| Paid notification | signed, fresh, Ds_Response 0000–0099, amount = total | log `accepted` with status paid; task confirms; order `paid`, hook once | — |
| Refused notification | signed, Ds_Response outside 0–99 | attempt `failed`; order stays payable; retry allowed | — |
| Duplicate | same `Ds_Order:Ds_Response` again | 200, log `duplicate`, nothing changes | — |
| Bad signature | wrong key, tampered parameters, URL-safe vs standard base64 of a valid one | tampered/wrong key: 401 `bad_signature`; base64 variant of a valid one: accepted | warning |
| Stale | `Ds_Date`/`Ds_Hour` more than 30 min from now (Madrid) | 400, log `stale` | warning |
| Amount or currency mismatch | signed notification amount ≠ total | order unchanged, payment `needs_attention`, log `amount_mismatch` | Sentry error by order UUID |
| Unknown order | Ds_Order not ours | 404, log `unknown` | warning |
| No notification yet | return page, nothing received | "waiting for confirmation" | — |
| Retry during session | attempt open, doctor retries | "payment in progress" until timeout or a refusal arrives | — |
| Return by POST | Redsys posts to the return view | 303 to the GET page; doctor still logged in | — |
| Refund | `redsys_refund <ref>` on a confirmed payment | REST call type 3, signed response verified, `Ds_Response` 0900 → prints the refund id; order unchanged | other response or HTTP fault: `CommandError`, no body logged |
| Public site safety | `REDSYS_ENVIRONMENT=test` or the public test key, ROBOTS_NOINDEX off | system check error | — |

</frozen-after-approval>

## Code Map

- `payments/gateways.py` (entry 2): `register(name, start, enabled=None, **ops)`, `Adapter` (`session_lifetime`, `timestamp_tolerance`, `required_settings`, `supports`, `call`), `WebhookEvent`, `GatewayPayment` (status PAID/PENDING/FAILED), `check_timestamp`, `BadSignature`/`Stale`/`Malformed`/`GatewayError`. Add `status` to `WebhookEvent` (optional, default None, so the fake keeps working).
- `payments/apps.py` `PaymentsConfig.ready`: register Redsys beside `fake.register()`.
- `payments/fake.py`: the model adapter (own page URL from `start`, bytes HMAC compare).
- `payments/webhooks.py` `handle`/`_log`: store the event's status on the log row. `process` re-reads outside transactions; Redsys's `fetch_payment` reads the log, not the network.
- `payments/models.py` `WebhookLog`: add `status`; migration `payments/migrations/0003_*`.
- `payments/service.py`: `start_payment` (cancels live attempts before its transaction: Redsys has none, so `AttemptOpen`), `set_gateway_payment_id`, `fail_payment`, `confirm_payment`.
- `payments/views.py` / `payments/urls.py`: `_go_to_gateway`, `_doctor_order`, `checkout_return`/`checkout_failure` (GET, login required); new Redsys form page and return view. Template beside `payments/templates/payments/fake_gateway.html`.
- `payments/checks.py`: E003; add the public-test-key/environment check.
- `accounts/http.py` `post_json`: wrap for the refund; failures become `GatewayError`.
- `mytreatplan/settings.py`, `settings.env.example`, `deploy/staging.env.example` (if present): the four settings; Sentry sensitive fields.
- `deploy/nginx/site.conf.template`: basic auth on the whole server, exemptions for ACME, `/healthz`, `/api/` (copy the `/api/` block's proxy lines). `deploy/README.md`.
- 5.9's Iberia rule and `REGION_PROFILE.gateways` (eu) already list `redsys`; `views.GATEWAY_LABELS['redsys']` = "Card": no change.
- Crypto: `cryptography` 50.0.2 is pinned; TripleDES is in `cryptography.hazmat.decrepit.ciphers.algorithms` (check the import). Per-order key: 3DES-CBC, zero IV, `Ds_Order` zero-padded to 8 bytes, key = base64-decoded merchant key; signature = base64(HMAC-SHA256(key, Ds_MerchantParameters)). Normalise `-_` ↔ `+/` before comparing. Decoded JSON keys may differ in case.
- Redsys public test terminal (published by Redsys): FUC 999008881, terminal 1, key `sq7HjrUOBfKmC576ILgskD5srU870gJ7`, card 4548 8120 4940 0004, CVV 123, any future expiry, 3DS code 123456. URLs: test `https://sis-t.redsys.es:25443/sis/realizarPago` and `/sis/rest/trataPeticionREST`; production `https://sis.redsys.es/sis/...`.

## Tasks & Acceptance

**Execution:**
- [ ] `payments/models.py`, `payments/migrations/0003_*`, `payments/gateways.py`, `payments/webhooks.py` -- `status` on `WebhookEvent` and `WebhookLog`, stored by `handle` -- the notification is Redsys's record.
- [ ] `payments/redsys.py` -- the adapter: settings, Ds_Order generation, parameters and signature, `start`, `verify_webhook` (parse form body, verify, Madrid timestamp, map Ds_Response and 978), `fetch_payment` from the log, `refund` over REST with a verified response; `register()`.
- [ ] `payments/apps.py`, `payments/checks.py`, `mytreatplan/settings.py`, `settings.env.example` -- registration, the new public-site check, settings and Sentry fields.
- [ ] `payments/views.py`, `payments/urls.py`, `payments/templates/payments/redsys_form.html` -- owner-only auto-submit page; CSRF-exempt GET/POST return view → 303.
- [ ] `payments/management/commands/redsys_refund.py` -- refund command.
- [ ] `deploy/nginx/site.conf.template`, `deploy/README.md` -- the webhook location without basic auth.
- [ ] `locale/es/LC_MESSAGES/django.po`, `README.md` -- ES strings; a "Payments: Redsys" section (settings, test terminal, refund command, measuring the session lifetime).
- [ ] `payments/tests/test_redsys.py` -- every matrix row, signatures checked against a known vector built from the public test key.

**Acceptance Criteria:**
- Given dev with the public test terminal and the nginx exemption, when a Spanish test doctor pays a test order with the test card, then Redsys's notification is logged `accepted`, the order is `paid` once, and `redsys_refund <ref>` refunds it at Redsys.
- Given a Redsys attempt abandoned on the payment page, when the doctor retries before the session times out, then they see the "payment in progress" message, and after the timeout the retry opens a new attempt with a new Ds_Order.

## Implementation Notes

- Signature vector: `payments/tests/test_redsys.py` checks the per-order key and the signature against values computed independently with OpenSSL (`des-ede3-cbc`, zero IV, `-nopad`; `dgst -sha256 -mac HMAC`) for order `1234ABCDEFGH` under the public test key.
- Small additions beyond the task list: `accounts.http.post_json` takes an optional `timeout` (refund uses 30 s; the default 3 s is the signup checks'); a notification whose `Ds_TransactionType` is not `0` (e.g. a refund's, if the terminal sends one) is logged without a status, so `fetch_payment` ignores it; `start` keeps an attempt's `Ds_Order` when called again (a reload never orphans a number Redsys may have charged); the form page sends a timed-out attempt to the failure page; the auto-submit is `static/js/redsys-form.js` (no inline script); `payments.E004` also refuses Redsys's public test merchant code; `deploy/staging.env.example` gets commented Redsys lines.
- `redsys_refund` refunds only a `confirmed` Redsys payment (a `needs_attention` one is staff's, by hand in the Redsys module).
- Confirmed on dev (2026-11-19, public test terminal 999008881/1, staging below):
  - Notification: a form POST from Redsys's `Java/1.8` client (859 bytes paid, 623 refused), accepted by the signature check; the browser return (`/redsys/ok/`, `/redsys/ko/`) arrives as a GET with `Ds_*` in the query string and a URL-safe base64 signature (`-`), which `verify` already takes.
  - Paid: `Ds_Response` 0000, notification ~1 s before the browser return; the worker task confirmed the payment before the return page read it.
  - User cancel on Redsys's page: Redsys DOES send a signed notification, `Ds_Response` 9915, which closes the attempt as `failed`; the doctor lands on the failure page and can pay again (a new attempt and `Ds_Order`).
  - Refund over REST: answer 0900, the refund id is `Ds_Order:Ds_AuthorisationCode`, the SAME for every refund of a payment (not a refund key). A second refund of the same payment within ~1 minute is refused SIS0295 (duplicate operation; it went through ~2 min after the first); a refund above what is left is refused by Redsys SIS0057.
  - Page timeout: not measured; `SESSION_LIFETIME` stays 30 minutes (open, see Staging).

## Plan Change Log

## Review Triage Log

Quick lens (Opus), iteration 0, 8 findings (one split in two). Patches went back to the same implementer; then my full suite: 3781 passed, 13 skipped, 0 failed.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | `fetch_payment` returns the newest status, so a later refusal hides an earlier paid notification (card charged, attempt failed, no alarm) | high | patch | `order_by('-received_at')`; now a verified paid row wins, then the latest failed, then PENDING; tests both orders. |
| 2 | `Ds_MerchantCode` and `Ds_Terminal` never checked | medium | patch | The SHA-256 key is per merchant code, shared by its terminals; now both must match settings (terminal as an integer), else `Malformed`. |
| 3 | `_verify` accepts `ds_merchant_order`, so our own outgoing signed parameters pass verification | medium | patch | Fallback removed; tests derive the request key themselves and assert our parameters are refused. |
| 4 | E004 only a system check, not enforced at start (the README's restart path skips it); a test key on a public site lets anyone sign "paid" | high | patch | `checks.redsys_test_on_public_site()` shared by E004 and `PaymentsConfig.ready` (`ImproperlyConfigured`). |
| 5 | Our session clock starts before Redsys's: a form loaded at minute 29 can be paid after our expiry while a retry opens a second attempt | medium | patch | `service.extend_session` moves `session_expires_at` forward when the form page renders (own short transaction, no gateway call). |
| 6 | Autumn DST: `replace(tzinfo=MADRID)` picks fold 0, a genuine notification in the repeated hour is read as stale | low | patch | Both folds converted, the nearer one checked; test on 2026-10-25 02:30. |
| 7a | The refund answer's amount, currency and type are not compared with the request | low | patch | Now required equal (type 3 when present), else `GatewayError`. |
| 7b | Repeat or over-refunds not prevented; a timeout after Redsys refunded invites a re-run | medium (unverified) | defer | Needs refund records (entry 7); unverified whether Redsys itself refuses a cumulative over-refund: settled by a second partial refund on the test terminal. Deferred-work entry on master. |
| 8 | 125-column docstring line in `payments/apps.py` | low | patch | Re-wrapped. |

## Verification

**Commands:**
- the full suite (local SQLite command in the README) -- expected: 0 failures.
- `manage.py makemigrations --check` and `manage.py check` -- expected: no changes, no errors.

**Manual checks (staging, dev):**
- Unverified Redsys details confirmed on the first run and recorded in Implementation Notes: the notification's content type and base64 variant, the refund response shape, whether a user cancel sends a refusal notification, the payment page's real timeout (adjust `session_lifetime`).

## Staging

2026-11-19, dev (Redsys public test terminal; dev-ae has no Redsys settings), after #115 merged (0c1e35b) and dev + dev-ae were re-provisioned:

1. Re-provision: the `/payments/webhook/` location is in nginx for both sites; an unsigned POST reaches Django (401 `bad_signature`, no basic-auth challenge). PASS.
2. The user typed the four `REDSYS_*` lines into dev `shared/settings.env`; after a restart `check` is clean and the adapters are `fake`, `redsys`. PASS.
3. Test-card payment, order YG-008970 (1699.00 EUR): one `accepted`/`paid` log row (0000), payment `confirmed`, order `paid`, one `paid` change, worker task successful, return page "paid". PASS.
4. `redsys_refund YG-008970 --amount 100.00` refunded; an immediate `--amount 50.00` refused SIS0295, the same ~2 min later refunded; `--amount 1600.00` (above the 1549.00 left) refused SIS0057. Order stays `paid` (by design: entry 7). PASS.
5. Abandon and retry, order CC-827483 (315.92 EUR): Cancel on Redsys's page → notification 9915, attempt `failed`, failure page "You have not been charged"; Pay again → a second attempt `confirmed`, order `paid`. PASS.
6. Unverified details recorded in Implementation Notes. PASS, except the payment page's real timeout: not measured (open; leave a Redsys tab open past 30 minutes on a later run and adjust `SESSION_LIFETIME` if Redsys's page outlives it).
