---
title: 'Voucher redemption'
type: 'feature'
ticket: '4'
created: '2026-11-20'
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

**Problem:** Vouchers are defined (5.10), and pricing already applies them to an order (`voucher_taken`, `voucher_share`). But nothing records that a voucher was used, so one voucher can be used on any number of orders. The admin site never learns about a use either (S5, AD-20).

**Approach:** A regional `VoucherRedemption` in the portal's `subscriptions` app follows the order's lifecycle through the 7.1 hooks: reserved when the order is created, consumed when it is paid, released when it is cancelled or expires. Database constraints enforce the limits by type. The order service checks the use first, so the doctor gets a clear refusal. Usage events `voucher.redeemed` and `voucher.released` are new contract types in mytp-core 0.30.0. The admin site receives them and stores each redemption's latest state for epic-sales-finance's usage view.

## Boundaries & Constraints

**Always:**
- The redemption keeps `voucher_id` and `doctor_uid` as plain UUIDs, with no foreign key to `catalogue.Voucher`: snapshot tables are re-created on every apply. It is unique per order and idempotent per `order.id` in each receiver.
- A released redemption stays as history and never counts as a use. A consumed redemption is never released by a cancel or an expiry (a refund is 7.7's).
- Before an order is created, the redemption is checked again against the snapshot row: state usable, type matches the order kind, currency, and `client` against the doctor's `uid`. A code that is absent from the snapshot (unknown, or another region's) is refused with its own reason.
- Events go through `integration.outbox.record` inside the hook's transaction. The payload has no patient data, and amounts use the `Amount` type.
- Deploy order: core, then admin (the receiver), then portal.

**Never:** a voucher field on any screen (8.5 and 9.6 build those); the usage view (epic-sales-finance); refunds (7.7); included cases (8.13); changing `payments.hooks._fire`, the order lock or version protocol, or pricing arithmetic.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Reserve | try-me voucher on a service order | order created; redemption `reserved` | — |
| Consume | that order paid (fake gateway) | redemption `consumed` | — |
| Release | bank-transfer order expires after 14 days, or the doctor cancels | `released`; the voucher can be used again | — |
| Full cover | the voucher covers the whole price (€0 order) | reserved and consumed in the same transaction | — |
| Same doctor twice | second order with a try-me voucher still reserved or consumed | no order created | refusal `VOUCHER_USED` |
| Unknown or other region | code not in this region's snapshot | no order | refusal `VOUCHER_UNKNOWN` |
| Wrong currency or doctor | existing pricing checks | no order | `VOUCHER_CURRENCY` / `VOUCHER_DOCTOR` |
| Race | two orders reserve at once | one wins | the constraint makes the other fail (`HookFailed`), no order |
| Hook repeat | `paid` fires again | no change | idempotent |

## Decisions (user, 2026-11-20)

- Limits by type, as decided in 5.10: a subscription voucher has one live redemption (reserved or consumed) in total, and a try-me voucher has one live redemption per doctor.
- The admin site sees the whole lifecycle. `voucher.redeemed` is sent at reserve (state `reserved`, version 1) and at consume (state `consumed`, version 2). `voucher.released` is sent at release (version 2). The admin stores each redemption's latest state.
- A reservation lasts as long as its order: 24 h in checkout, 14 days for a bank transfer. The holder frees it by cancelling the order.
- Merge and staging are delegated. Claude commits and tags mytp-core 0.30.0 locally, and the user pushes it. Claude merges admin first, then the portal, after green CI. Claude runs the staging checks over SSH on admindev and dev; the user's answer is the SSH OK for these checks.

</frozen-after-approval>

## Open Questions

1. **How often can a voucher be used?** 5.10 decided "a subscription code once in total, a try-me code once per doctor". AD-20 says "unique per voucher and doctor".
   (a, recommended) Follow 5.10, enforced by type: a subscription voucher has one live redemption in total, and a try-me voucher has one live redemption per doctor. (b) One rule for both types: once per doctor.
2. **What does the admin site see?**
   (a, recommended) The whole lifecycle. `voucher.redeemed` is sent when a redemption is reserved (state `reserved`, version 1) and again when it is consumed (state `consumed`, version 2). `voucher.released` is sent when it is released (version 2). Sales can then see a voucher held by an unpaid order, such as a subscription voucher blocked by a 14-day bank transfer. (b) Uses only. `voucher.redeemed` is sent when a redemption is consumed, and `voucher.released` is reserved for the refund in 7.7. Simpler, but a held voucher is invisible to admin.
3. **Holding a voucher while an order is unpaid:** a reservation lasts as long as its order: 24 h in checkout, 14 days for a bank transfer. For a subscription voucher (once in total) that blocks everyone else meanwhile.
   (a, recommended) Accept it. The holder can cancel the order to free the voucher. (b) Release the reservation sooner, after 24 h, even on a bank-transfer order. The transfer would then be paid without its discount being guaranteed. Not advised.
4. **Merge and staging delegation** (this story releases mytp-core 0.30.0):
   (a) As for 7.6. I commit and tag core locally, and you push it (`! git -C ../mytp-core push origin master v0.30.0`). I merge admin first, then the portal, after green CI. Then I run the staging checks over SSH on admindev and dev: reserve, consume and release through the shell, then the usage rows on admindev. Choosing (a) is your SSH OK for these checks. (b) You merge and run the checks.

## Code Map

- `payments/service.py` -- `create_order(user, lines, *, actor, voucher=None, entity=None)` :335, `create_subscription_order(...)` :344 (the pricing refusal `OrderRefused` happens before the transaction: put the use check there); `_create` :301 (voucher_id :313; fires `created` inside the atomic block); `_pay_free` :353; `cancel` :483, `expire` :496, `expire_stale` :503 (a bank-transfer order expires when its 14-day attempt ends); `choose_entity` :365 re-prices with `_voucher_of` :243 and must not refuse the order's own reservation; `_fire` :267 (raise → `HookFailed`, reason lost).
- `payments/hooks.py` -- `register(event, name, receiver)` :22; the docstring at :6 names `created` as the place to reserve; receivers get the locked order. No production registration exists yet.
- `payments/views.py:72-75` -- messages for the VOUCHER_* reasons; add `VOUCHER_USED` and `VOUCHER_UNKNOWN` (EN and ES `.po`).
- `catalogue/pricing.py` -- `REFUSALS` :105-114, `_check_voucher` (~:362); `catalogue/resolver.py` `voucher_by_code` :544 (None means absent), `voucher_state` :551; snapshot `catalogue/models.py:527 Voucher` (no region column; `catalogue/applier.py:294` keeps only this region's vouchers).
- `subscriptions/` -- the app that will hold `VoucherRedemption`; `subscriptions/models.py` (pattern: plain UUID ids, docstring :11); its `AppConfig.ready` registers the receivers.
- `integration/outbox.py:137` `record(type, aggregate_id, aggregate_version, payload)` (inside atomic); writer examples `mytp_publicsite/contact_sync.py:69`, `accounts/client_sync.py:152`.
- Core `../mytp-core`: model on `contracts/contact_messages.py` (Writer + Reader models), `contracts/payloads.py:177` `register_payload`, `contracts/__init__.py` SCHEMA_MODELS :128 + `export_contract_schemas`, version in `pyproject.toml:7`, `mytp_core/__init__.py:54`, `tests/test_hub.py:30`; registered-types asserts `tests/test_contracts.py:648`, `../mytp-admin/interface/tests/test_vendored_core.py:37`, `integration/tests/test_vendored_core.py:36`.
- Admin `../mytp-admin`: `interface/events.py` `appliers()` :179, `_read` :84, `_record_and_apply` :270 (versions per kind `voucher` + aggregate id; stale if not increasing); home `vouchers/` (models, migration); example of a receiver app: `contact_messages/` (commit 8b4fce3).
- Release: `scripts/vendor-mytp-core.sh v0.30.0` in both sites; update the version asserts and `layout/tests/test_deploy.py` (admin).
- Tests: `integration/tests/snapshot_rows.py:333` `voucher(...)`, `GENERAL` :340, `pull(..., vouchers=[...])` :103; `payments/tests/conftest.py` (`doctor`, `entity`, `fake_on`, `events`); `payments/tests/test_bank_transfer.py:49` helpers, :314 expiry; full-cover example `payments/tests/test_service.py:394`; admin `interface/tests/test_events.py:88-139`. Suite commands: see `epic-payments/story-bank-details-and-payment-methods-per-country-plan.md:64-68`.

## Tasks & Acceptance

**Execution:**
- [ ] `../mytp-core/mytp_core/contracts/vouchers.py` (or a new `voucher_usage.py`), `contracts/payloads.py`, `contracts/__init__.py`, `schemas/payload_voucher_*.json`, version 0.30.0 (`pyproject.toml`, `__init__.py`, `tests/test_hub.py`), `README.md`, `tests/test_contracts.py` -- add the `voucher.redeemed` and `voucher.released` types with closed Writer and Reader payloads. Fields: `redemption_id`, `voucher_id`, `voucher_type`, `client_uid`, `order_id`, `order_reference`, `state` (`reserved`/`consumed`/`released`), `amount` (`Amount`), `at`. Register them and regenerate the schemas -- the contract both sites share.
- [ ] `../mytp-admin/vouchers/models.py`, `vouchers/migrations/`, `vouchers/events.py`, `interface/events.py` (`appliers()`), vendored 0.30.0 (`scripts/vendor-mytp-core.sh`, `requirements.txt`, `interface/tests/test_vendored_core.py`, `layout/tests/test_deploy.py`), tests -- `VoucherRedemption` stores the latest state per `redemption_id` (region from the envelope; voucher, doctor uid, order id and reference, state, amount, currency, version, timestamps), updated by both appliers. The existing stale-version rule keeps the latest -- what epic-sales-finance's usage view reads.
- [ ] `subscriptions/models.py`, `subscriptions/migrations/` -- `VoucherRedemption` as in Design Notes, with partial unique constraints by type -- the regional entity (AD-20).
- [ ] `subscriptions/vouchers.py` -- `check_use(voucher, doctor_uid)` raises a refusal when a live redemption blocks it. `reserve(order)`, `consume(order)` and `release(order)` are idempotent per `order.id`, do nothing when `order.voucher_id` is null, and record the outbox event at each step. `release` leaves a consumed redemption alone -- the reusable API (epic-ordering's try-me vouchers).
- [ ] `subscriptions/apps.py` -- in `ready()`, register `created`→`reserve`, `paid`→`consume`, `cancelled`→`release` and `expired`→`release` -- the lifecycle.
- [ ] `catalogue/pricing.py` (`REFUSALS`), `payments/service.py` (`create_order`, `create_subscription_order`: call `check_use` before the transaction; `choose_entity` ignores the order's own reservation), `payments/views.py:72-75`, `locale/es/LC_MESSAGES/django.po` -- add `VOUCHER_USED` and `VOUCHER_UNKNOWN` with EN and ES messages -- a clear refusal. A race left to the constraint ends as `HookFailed`.
- [ ] `integration/tests/test_vendored_core.py`, `requirements.txt`, the vendored wheel -- 0.30.0 and the two new registered types.
- [ ] `subscriptions/tests/test_vouchers.py`, `payments/tests/` additions, `../mytp-admin/vouchers/tests/` -- every I/O matrix row (bank-transfer expiry through `expire_stale`; the race as a PG-only thread test). Also check the events' versions and states in the outbox, and that the admin applies `redeemed` v1, then v2, ignores a stale v1 after v2, and that `released` wins -- proves the scenarios.

**Acceptance Criteria:**
- Given a try-me voucher, when a doctor's order with it is paid, then the portal holds one `consumed` redemption and the outbox holds `voucher.redeemed` v1 (`reserved`) and v2 (`consumed`) for it.
- Given a subscription voucher reserved by one doctor's unpaid order, when another doctor tries it, then the order is refused with `VOUCHER_USED`. After the first order is cancelled, the second doctor can use it.
- Given admin receives those events in order, then its record for the redemption shows the latest state, and a replayed older event leaves it unchanged.

## Design Notes

- `VoucherRedemption(id UUID, voucher_id, voucher_type, doctor_uid, order_id unique, state reserved|consumed|released, amount, currency, version, timestamps)`. Partial unique constraints on live states (reserved or consumed): `(voucher_id, doctor_uid)` for try_me, and `(voucher_id)` for subscription. Both work on SQLite and PostgreSQL.
- Aggregate = the redemption id, so each redemption's versions rise on their own.
- Reusable API for epic-ordering: `subscriptions.vouchers.check_use(voucher, doctor_uid)`, `reserve(order)`, `consume(order)`, `release(order)`.
