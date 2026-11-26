---
title: 'Pending-payment work orders'
type: 'feature'
ticket: '9'
created: '2026-11-25'
status: 'ready-for-dev'
baseline_revision: ''
route: 'full'
route_source: 'auto'
risk: 'medium'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-mytreatplan-portal-admin/architecture-portal-admin/architecture-portal-admin.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Clinical ops sometimes open a case for a doctor before it is paid (epic-clinical-ops). The portal has no way to create such a work order, and the doctor has no way to see it and pay for it.

**Approach:** Add a portal service that creates a manual work order in `pending_payment` for one of a doctor's patients. Show the doctor's manual pending work orders on the home page (6.1), each with "Add to cart". Adding one prices it with the doctor's effective tier at that moment (8.1). Once its order is paid, that same work order moves to `pending_assignment` (live). A manual pending work order never blocks other services (9.8 already ignores rows without an `order_id`).

**Decisions (user, 2026-11-26):**
- This story builds the portal service seam only, plus a test fixture standing in for clinical ops (as `integration/sample.py`). epic-clinical-ops later adds the mytp-core resource, the remote write and the admin screen.
- A manual work order carries the product. The doctor chooses that product's options when adding it to the cart (add-ons, quantity, shipping address for VirtuaOrtho), through the case wizard with the patient and the product fixed.
- While its order waits for a bank transfer, a manual work order blocks the patient like a 9.8 hold (it gets the order's `order_id`). If that order is cancelled or expires, it goes back to a plain manual work order (`order_id` cleared, still `pending_payment`), never `cancelled`.
- The doctor cannot decline it; only clinical ops cancel one (later, in their epic).

## Boundaries & Constraints

**Always:**
- A manual work order has no order line until it is ordered. When its order is paid it is adopted (its `order_line_id`, `order_id` set) and moved, never duplicated.
- Patients are identified by code across services (AD-23). The service emits `work_order.created`/`stage_changed` like 9.8.
- The one-live rule is still checked at payment (the 9.8 constraint).
- Built on 9.8 (branch from master after 9.8 merges).

**Never:**
- No admin screen and no remote write (epic-clinical-ops owns them).
- No decline action for the doctor.
- A manual pending work order never blocks the cart, order creation or payment checks of other services.
- No fixed price stored on the work order (tier price when added to the cart).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Created | Service called for P1 + product | Row in `pending_payment`, no order; `created` event; shown on home | — |
| Unknown patient | Code not the doctor's active patient | Refused, nothing created | `WorkOrderRefused` |
| Not blocking | P1 has a manual pending WO, doctor orders a TPS for P1 | Allowed | — |
| Added to cart | Doctor clicks "Add to cart" | Cart item linked to the WO, priced at the current tier | `CartRefused` if P1 already in the cart |
| Paid | Order with the linked line paid | Same row moves to `pending_assignment` with `order_line_id`/`order_id` set; no second row | Live conflict rolls back (`HookFailed`) |
| By transfer | Order with the linked line approved by bank transfer | The manual row gets the `order_id` and blocks the patient; no second row | — |
| Order cancelled | Linked order (card or transfer) cancelled or expired | Manual WO is still `pending_payment`, `order_id` cleared, no longer blocking, still on the home page | — |
| Doctor declines | — | No decline action exists | — |

</frozen-after-approval>

## Code Map

- `patients/models.py:91` `WorkOrder.order_line_id` -- make it nullable. Postgres allows several NULLs under `unique`, so replays stay idempotent. The 9.8 docstring already describes a manual row as `pending_payment` without an `order_id`.
- `patients/service.py` (after 9.8):
  - Add `create_manual_work_order(doctor, patient_code, product_id, *, actor)`, a variant of `_create_work_order` (:239) with no order or line.
  - In `create_work_orders_for_order` (:311) and `_line_work_order` (:283), adopt the manual row named by the line's `work_order_id` before falling back. The live-ICE closing still runs.
  - `hold_work_orders_for_transfer` (:292) and `cancel_work_orders_for_order` (:329) change: at approval the line's manual row gets the `order_id` (no new row); on cancel or expiry a manual row (no `order_line_id`) gets its `order_id` cleared, while a 9.8 row is cancelled as today.
  - `blocking_work_order` (:153) stays as it is.
- `cart/models.py:24-45` `CartItem` -- a nullable `work_order_id`. Keep the unique constraint (doctor, patient) at :44.
- `cart/service.py` -- `add_work_order(doctor, work_order)`: the doctor's own manual `pending_payment` row, priced like `add_tps`/`add_virtuaortho` (:183/:255, `resolver.price(product, subscriptions.effective(doctor).subscription, ...)` :220/:301). `_order_lines` (:340) passes `work_order_id` through.
- `payments/models.py:129` `OrderLine` -- a nullable `work_order_id`, carried from the cart (`catalogue/resolver.py` `OrderLine`). Migration in payments.
- `accounts/views.py:405` `home` and `accounts/templates/accounts/home.html` (tiles :50-55) -- a "Pending payment" block listing the manual rows (patient code, product name, Add to cart), and nothing when there are none.
- Seam only: the service is the entry point clinical ops will call later. Precedent: `integration/sample.py` (epic-clinical-ops.md:47). No route and no mytp-core change.
- Case wizard (9.16, django-formtools, `cart/wizard.py`): add an entry that starts the wizard for a manual work order, with the patient and the product fixed, and saves the cart item with `work_order_id`.
- Tests to copy: 9.8's `patients/tests/test_work_orders.py`, `cart/tests/test_service.py`, the home-page tests in `accounts/tests/`.

## Tasks & Acceptance

**Execution:**
- [ ] `patients/models.py`, `cart/models.py`, `payments/models.py` + migrations -- nullable `order_line_id`; `work_order_id` on cart item and order line
- [ ] `patients/service.py` -- manual create, adoption on paid, transfer hold and revert on cancel
- [ ] `cart/service.py`, `catalogue/resolver.py` -- add a manual work order to the cart, carry its id to the order line
- [ ] `accounts/views.py`, `home.html`, the wizard views -- the home block, and "Add to cart" opening the wizard with patient and product fixed
- [ ] the seam's docstring and a fixture helper for tests (`patients/tests/conftest.py`)
- [ ] tests -- every row of the matrix, plus the tier price at add time (change the tier, then add)

**Acceptance Criteria:**
- Given a manual work order, when the doctor's tier changes before they add it to the cart, then the cart shows the new tier's price.
- Given a paid order holding a manual work order, when the patient's work orders are listed, then there is exactly one for that service, in `pending_assignment`.

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Verification

**Commands:**
- portal full suite (HANDOFF command) -- expected: passes, ~17 local skips
