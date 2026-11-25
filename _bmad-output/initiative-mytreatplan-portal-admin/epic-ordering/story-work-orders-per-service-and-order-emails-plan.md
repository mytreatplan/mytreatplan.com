---
title: 'Work orders per service and order emails'
type: 'feature'
ticket: '8'
created: '2026-11-25'
status: 'ready-for-dev'
baseline_revision: ''
route: 'full'
route_source: 'auto'
risk: 'high'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-mytreatplan-portal-admin/architecture-portal-admin/architecture-portal-admin.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Only a paid ICE gets a work order. A bank-transfer order does not block its patients while it waits for the money, and no work-order events reach the admin site.

**Approach:** One work order per patient-service line. A paid order creates them in `pending_assignment`. A bank-transfer order creates them at approval in `pending_payment`, where they block the patient. When the order is paid they move to `pending_assignment`; when it is cancelled they are cancelled. Paying a planning moves the patient's live ICE work order to a new final stage, `closed`. Every create and stage change records a patient-free work-order event (new mytp-core contract), which the admin site stores as workload rows.

**Decisions (user, 2026-11-25):**
- Split: the order confirmation and receipt emails leave this story (deferred-work.md, with the user's email answers 2A/3A/4A).
- When a planning (TPS or VirtuaOrtho) for a patient with a live ICE work order is paid, the ICE work order becomes `closed`, never `cancelled`, and a new work order is created for the planning. `closed` is a new final stage in mytp-core.
- The admin side is in this story: a new mytp-admin `work_orders` app stores the workload rows.

## Boundaries & Constraints

**Always:**
- Events carry no patient name or code.
- Every hook receiver stays idempotent per order line. A replayed `transfer_approved` or `paid` adds nothing.
- An order's own pending-payment work orders never block that same order (approve again, switch gateway, pay).
- One branch per repo, `feat/9-8-work-orders-and-order-emails`. Same wheel vendored in both apps.

**Never:**
- No order emails in this story.
- epic-clinical-ops' manual pending-payment work orders (9.9) must never block.
- No work order for a line without a patient code (test or subscription orders).
- No change to `refunded` handling.
- The admin site never sets a stage (AD-21).
- An ICE work order is never `cancelled` because a planning was paid.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Two patients paid | Card order, TPS for P1, VirtuaOrtho for P2 | 2 work orders in `pending_assignment`, 2 `created` events | — |
| Transfer approved | Transfer chosen for an order with P1 | P1 work order in `pending_payment`; P1 refused in a new order or cart | `patient_refused` |
| Transfer chosen again | Same order re-approves | No new work order or event | — |
| Transfer then paid | Staff record the money (or doctor switches to card and pays) | Same row moves to `pending_assignment` (version +1, `stage_changed` event) | — |
| Transfer cancelled | Order cancelled (doctor or admin) | Its pending work orders become `cancelled` | — |
| Planning after ICE | P1 has a live ICE, planning paid | ICE work order becomes `closed`, planning work order created, both events, in one transaction | Constraint failure rolls back (`HookFailed`) |
| Planning after ICE, by transfer | P1 has a live ICE, planning approved by transfer | Planning work order in `pending_payment`, ICE stays live until paid | — |
| Manual pending WO | 9.9-style work order (no order) | Does not block | — |

</frozen-after-approval>

## Code Map

- `mytp-core/mytp_core/stages.py` -- add `CLOSED = 'closed'` to `STAGES` (:33) as a final stage, outside `LIVE_STAGES` (:41). In `_allowed()` (:46-60), every live stage may go to `closed`, and `closed` goes nowhere.
- `mytp-core/mytp_core/contracts/work_orders.py` (new) -- copy `contracts/voucher_usage.py:115`. Event types `work_order.created` and `work_order.stage_changed`. Payload: `work_order_id`, `stage`, `product_category`, `at`. Writer and Reader models, plus `WORK_ORDER_READERS`.
- `mytp-core/mytp_core/contracts/{__init__,payloads}.py` -- docstring and re-exports at `__init__:19,:137`. Import at `payloads:40`; register in the block at `:183-193`.
- `mytp-core/tests/test_contracts.py:654` (exact list of registered types), new `tests/test_work_orders.py`, and stage tests. Generate the JSON schemas with `export_contract_schemas` (README `:480`).
- `mytp-core` release -- `pyproject.toml` 0.34.0 → 0.35.0 (and its description), `mytp_core/__init__.py:43`, README section (~`:1283`).
- `patients/models.py:80` `WorkOrder` -- add a nullable `order_id` UUID. With it set, a `pending_payment` row is a transfer work order (blocking); without it, a 9.9 manual one. It also finds an order's rows if line ids change. The migration also picks up the new `closed` choice. Keep `patients_one_live_work_order` as it is.
- `patients/service.py`:
  - `live_work_order` :133 stays.
  - Add `blocking_work_order(patient, exclude_order=None)`: a live row, or a `pending_payment` row with an `order_id` that is not `exclude_order`'s.
  - `_is_case` :188 becomes "the line has a patient code".
  - `create_work_orders_for_order` :196: reuse the line's `pending_payment` row (move it to `pending_assignment`), otherwise create one. For a non-ICE line whose patient has a live ICE work order, move that ICE to `closed` first.
  - New `hold_work_orders_for_transfer(order)` and `cancel_work_orders_for_order(order)`.
  - `move_work_order` :166 and every create call `_record_event`.
- `patients/apps.py:19` -- register `transfer_approved`→`work_orders_hold`, and `cancelled`/`expired`→`work_orders_cancel`. Keep the `paid` order `invoice_issue` → `work_orders`.
- `payments/service.py:318-375` `_check_patients`/`_patient_free`/`_patients_still_free` -- use `blocking_work_order`, with `exclude_order=order` in `_patients_still_free`. A live ICE still lets a planning through; a pending transfer work order never does.
- `cart/service.py:196,:267` (`add_tps`, `add_virtuaortho`) -- same blocking check.
- `integration/outbox.py:137` `record(...)` -- copy the caller at `subscriptions/vouchers.py:75-82`. `product_category` comes from `resolver.product`, or `''`.
- `mytp-admin`:
  - A new app `work_orders/` (`apps.py`; `models.py` `WorkloadEntry`: uid, stage, product_category, region_id, version, updated_at; migration; `events.py` copying `vouchers/events.py:21-49`; tests).
  - Add the app to `INSTALLED_APPS` and an entry to `interface/events.py:188-205` `appliers()`.
  - Re-vendor the wheel and update the `requirements.txt:43` path.
- Tests to copy: `patients/tests/test_ordering.py` (:32-51 helpers, :116-158, :196), `patients/tests/test_service.py:121-150`, `patients/tests/test_ice_deduction.py`.

## Tasks & Acceptance

**Execution:**
- [ ] `mytp-core` -- `closed` stage, contract, schemas, tests, and the 0.35.0 release commit; the tag is created by the user (the proxy refuses tag pushes) -- the contract comes first
- [ ] `patients/models.py` + migration -- `WorkOrder.order_id`, set on every order-created row
- [ ] `patients/service.py` + `patients/apps.py` -- blocking check, hold, cancel, paid conversion, ICE `closed`, events, hook registrations
- [ ] `payments/service.py`, `cart/service.py` -- switch to the blocking check
- [ ] `vendor/` + `requirements.txt` (both apps) -- vendor the 0.35.0 wheel
- [ ] `mytp-admin/work_orders/` -- model, migration, applier, registration, tests
- [ ] tests in each repo -- every row of the matrix, the replay of each hook, the blocking check in cart, order and start_payment, and an event payload check showing no name or code

**Acceptance Criteria:**
- Given a paid order for two patients, when the paid hook runs, then each patient has one work order and two `work_order.created` events are in the outbox, with no patient name or code in them.
- Given those events delivered to the admin site, when the applier runs, then two workload rows hold the stage, product category and region. A replayed or older event changes nothing.
- Given a `closed` ICE work order, when the patient is checked for a new service, then it does not block, and it does not count as a finished treatment.

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Design Notes

Blocking stays in application checks, not in the database constraint. A transfer work order for a planning can exist while the patient's ICE is still live, which the one-live constraint would forbid. A race between two approvals is still stopped at payment by the constraint.

## Verification

**Commands:**
- `cd /home/user/mytp-core && python -m pytest -q` -- expected: passes
- portal full suite (HANDOFF command) -- expected: passes, ~17 local skips
- admin suite with env from `.github/workflows/ci.yml` -- expected: passes
