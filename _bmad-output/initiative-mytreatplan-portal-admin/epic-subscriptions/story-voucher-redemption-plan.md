---
title: 'Voucher redemption'
type: 'feature'
ticket: '4'
created: '2026-11-20'
status: 'done'
baseline_revision: '8cfa1c65b19fa0f8ebc5626aefbeb139e596b710'
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

- **Build setup (as for 7.14):**
  - Branch `story/8.4-voucher-redemption` is checked out in all three repos (portal = this repo, baseline 8cfa1c6; `../mytp-core` baseline d8ad580; `../mytp-admin` baseline ebd3aeb). Commit nothing, push nothing, tag nothing: the release is the main session's.
  - Test venvs (the old ones were cleaned up; create them): core `/private/tmp/claude-501/core-8.4-venv`, admin `/private/tmp/claude-501/admin-8.4-venv`, portal `/private/tmp/claude-501/portal-8.4-venv` (each from its repo's requirements, with the local core). Do not install the local wheel into `../.venv` (shared).
  - Local wheel: copy the core working tree's tracked files (`git ls-files | rsync --files-from=-`) to a temp dir, `SOURCE_DATE_EPOCH` = core HEAD commit time, `pip wheel --no-deps`, put it in both sites' `vendor/` and move each `requirements.txt` line to 0.30.0 (remove the 0.29.0 wheel). The main session re-vendors from the tag with `scripts/vendor-mytp-core.sh v0.30.0` before the PRs.
  - Portal suite (repo root, sandbox off): `FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU=" DATABASE_URL="sqlite:///$TMPDIR/p84.sqlite3" <venv>/bin/python -m pytest -q -p no:cacheprovider --tb=line` (0 failures expected; PostgreSQL-only tests skip). Also `makemigrations --check` and `compilemessages`.
  - Admin suite (in `../mytp-admin`): `SECRET_KEY=ci-only-not-a-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,127.0.0.1,testserver DATABASE_URL=sqlite:///$TMPDIR/a84.sqlite3 FIELD_ENCRYPTION_KEYS=<portal test key above> BLIND_INDEX_KEY=<portal test key above> ALLOW_FAKE_VERIFICATION=True <venv>/bin/python -m pytest -q -p no:cacheprovider`.
  - Core suite (in `../mytp-core`): `<venv>/bin/python -m pytest -q -p no:cacheprovider`; regenerate the JSON schemas with the core's own script and the test that checks them.
  - Previous baselines: portal 3871 / 14 skipped (7.6); core and admin: record yours before changing anything.

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

## Implementation Notes

- Sonnet implementer; suites before review: core 1655/4, admin 1660/12, portal 3895/15, 0 failures. After the review patches, my suites on the wheel vendored from the local tag v0.30.0 (core 8bacb9a): core 1655/4, admin 1660/12, portal 3903/15, 0 failures; makemigrations clean. Core release 0.30.0 adds a closed `UsageAmount` (the payload registry needs closed nested models; same JSON as `Amount`).
- Not in the plan, kept (Claude, told the user): `consume` on a paid order that has no redemption row (an order created before this release) reserves first; if another order holds the voucher, it logs an error and lets the payment through, so a hook never undoes money that has arrived.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, Opus, 2026-11-21):** 5 findings: 0 high, 2 medium, 3 low, 0 rejected. All 5 patched by the implementer.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | A code never in this region's snapshot is not refused: `voucher_by_code` returns None and `create_order(voucher=None)` makes a full-price order; only the stale-row path refuses `VOUCHER_UNKNOWN` | medium | patch | The Always rule asks for the refusal; no service maps a code. Fix: `subscriptions.vouchers.voucher_for_code(code)` in the reusable API, raising `VOUCHER_UNKNOWN`, plus tests. |
| 2 | The released event's amount comes from the order, not the stored row | medium | patch | `_record` used `_amount(order)`; `release` does not refresh the row, and `choose_entity` re-prices. Fix: the payload amount from the row. |
| 3 | Portal README lacks the new refusals and receivers | low | patch | README :64-71, :315-329; a direct correction. |
| 4 | Admin README lacks 0.30.0 and the `voucher` kind | low | patch | README :109, :950, :962; a direct correction. |
| 5 | Core README 0.30.0 schema entry is in the middle of the 0.14.0 text | low | patch | README :456; move it to the end of the list. |

## Design Notes

- `VoucherRedemption(id UUID, voucher_id, voucher_type, doctor_uid, order_id unique, state reserved|consumed|released, amount, currency, version, timestamps)`. Partial unique constraints on live states (reserved or consumed): `(voucher_id, doctor_uid)` for try_me, and `(voucher_id)` for subscription. Both work on SQLite and PostgreSQL.
- Aggregate = the redemption id, so each redemption's versions rise on their own.
- Reusable API for epic-ordering: `subscriptions.vouchers.check_use(voucher, doctor_uid)`, `reserve(order)`, `consume(order)`, `release(order)`.

## Staging

- mytp-core 0.30.0: commit 8bacb9a and tag v0.30.0, pushed by the user; core CI green on master and on the tag.
- Admin PR #45 merged as 5144097. Master run 44900498417: test and the admindev deploy green.
- Portal PR #120 merged as 96bfe91. Master run 44901005588: test and the dev and dev-ae deploys green; /healthz ok on dev and admindev.
- My local suites before the PRs: core 1655 / 4 skipped, admin 1660 / 12, portal 3903 / 15, 0 failures.
- Checks passed 2026-11-21 (Claude, over SSH, scripts check84.py and check84b.py):
  1. Migrations `subscriptions` 0003 on dev and `vouchers` 0002 on admindev applied.
  2. dev, rolled back: a try-me voucher order was `reserved` (v1), then paid through the fake gateway and `consumed` (v2). A second order with it by the same doctor was refused `voucher_used`. An unknown code was refused `voucher_unknown`.
  3. dev, rolled back: a subscription voucher held by an unpaid order was refused `voucher_used`, `released` after the cancel, then usable again. dev has only one doctor with a fiscal entity, so "another doctor" was checked with the same doctor; the tests cover two doctors.
  4. dev to admindev, committed: order NZ-539537 (try-me voucher 83692ffc, €10.00) was reserved, then cancelled. Its `voucher.redeemed` v1 and `voucher.released` v2 were delivered. admindev holds the redemption c4ed6498 as region eu, `released`, version 2.
  5. Not run on staging: replaying an older event on admindev (stale). The admin tests cover it.
- Staging data left behind: the cancelled order NZ-539537 on dev, its released redemption, and that admindev record. Orders and their changes are append-only, so they stay.
