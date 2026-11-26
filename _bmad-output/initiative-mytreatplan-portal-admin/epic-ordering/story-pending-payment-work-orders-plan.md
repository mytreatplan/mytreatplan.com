---
title: 'Pending-payment work orders'
type: 'feature'
ticket: '9'
created: '2026-11-25'
status: 'done'
baseline_revision: 'ff1c43f13d4a68184f5d759ee41f50f8d6587457'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
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
- [x] `patients/models.py`, `cart/models.py`, `payments/models.py` + migrations -- nullable `order_line_id`; `work_order_id` on cart item and order line
- [x] `patients/service.py` -- manual create, adoption on paid, transfer hold and revert on cancel
- [x] `cart/service.py`, `catalogue/resolver.py` -- add a manual work order to the cart, carry its id to the order line
- [x] `accounts/views.py`, `home.html`, the wizard views -- the home block, and "Add to cart" opening the wizard with patient and product fixed
- [x] the seam's docstring and a fixture helper for tests (`patients/tests/conftest.py`)
- [x] tests -- every row of the matrix, plus the tier price at add time (change the tier, then add)

**Acceptance Criteria:**
- Given a manual work order, when the doctor's tier changes before they add it to the cart, then the cart shows the new tier's price.
- Given a paid order holding a manual work order, when the patient's work orders are listed, then there is exactly one for that service, in `pending_assignment`.

## Implementation Notes

- Migrations: `patients/0005_manual_work_order` (`order_line_id` nullable), `cart/0003_work_order_id`, `payments/0009_orderline_work_order_id` (all nullable, no data change).
- `pricing.OrderLine` (re-exported as `resolver.OrderLine`) gained `work_order_id` (last field, default None); `payments.service._line_rows`/`_inputs` carry it, so re-pricing an order (another fiscal entity) keeps it.
- Seam: `patients.service.create_manual_work_order(doctor, patient_code, product_id, *, actor)`; refuses (`WorkOrderRefused`) an unknown/inactive/other doctor's patient and a product that is unknown or not a cart service category (`MANUAL_CATEGORIES`). Test stand-in for clinical ops: `patients/tests/conftest.py` `manual_work_order` (+ `CLINICAL_OPS` admin actor).
- Transfer hold: the manual row gets the order's `order_id` (version +1, no event: the stage is unchanged). Cancel/expiry: a row without `order_line_id` gets `order_id` cleared (version +1, no event); a 9.8 row is cancelled as before. Paid: `_line_work_order` first (finds a transfer-held manual row by order + patient), then the line's `work_order_id`; a manual row is adopted (`order_line_id`, `order_id` set) and moved, the live-ICE closing still runs. If the named manual row is gone (cancelled by ops), the 9.8 fallback creates a row.
- Cart: `cart.service.check_work_order` / `add_work_order` (new refusal code `work_order`); `add_tps`/`add_virtuaortho` untouched. A branded TPS is always brand-made, an in-house TPS always in-house, an ICE either; an ODB needs the variant (DigitalTwin adds ODB-DT); the product must be offered under the doctor's current subscription.
- Wizard: `cart.wizard.WorkOrderWizard` at `start-a-case/work-order/<work order id>/<step>/` (URL name `work_order_case_step`), steps maker (branded TPS, ICE) / vo_type (ODB only) / options (the one product's row, preselected). `CaseWizard` got small hooks (`options_step`, `step_number`, `first_back_url`, `here` from `get_step_url`); `MakerForm` accepts `options['kinds']`; `_step.html` shows "Step n of total" and the fixed product for this wizard only.
- Home: "Pending payment" block for doctors only; each row shows "Add to cart", "In your cart" (item linked) or "Waiting for your bank transfer" (`order_id` set). Spanish strings added by hand to `locale/es/LC_MESSAGES/django.po` (a full makemessages run reorders the whole catalogue).

## Plan Change Log

## Review Triage Log

Pass 1 (quick, 2026-11-26): high 0, medium 2, low 4, false 0, maybe-false 0.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | An in-house TPS work order shows the branded "included cases" notice | low | patch | No maker step, so `_answer('maker','kind')` is None. Now `included` is 0 unless the product is a branded TPS |
| 2 | "Add to cart" is offered for work orders the wizard cannot complete (retainers without a finished treatment; VirtuaOrtho products not offered), so the doctor is stuck on an empty options step | medium | patch | `check_work_order` now refuses them with `NOT_FINISHED` / `NOT_OFFERED` |
| 3 | A row whose product left the catalogue links to a wizard that 404s | low | patch | The home row now follows `check_work_order` |
| 4 | The paid hook takes over a manual row held by another order's transfer, while the transfer hook refuses that case | medium | patch | No UI path found (the start_payment blocking check stops it first). Refused the same way for consistency |
| 5 | No test for an expired card order (matrix row "Order cancelled") | low | patch | Test added |
| 6 | `test_another_doctor_cannot_add_it` accepts two codes | low | patch | Asserts the ownership code only |

## Verification

**Commands:**
- portal full suite (HANDOFF command) -- expected: passes, ~17 local skips

## Staging

- Merge: PR #148 → aac63a9 (2026-11-26 10:55 UTC), after master (with 9.19) was merged into the branch: no conflict, django.po valid, affected suites 502 passed. PR CI green on 6a4a313. Deploy run 45805777279 green, dev and dev-ae released (Upload release + Release, 11:11 UTC).
- Checks on dev (2026-11-26), read-only script `check_919_99.py` over SSH, doctor pk 18 (ES Premium), everything rolled back:
  - B1. `create_manual_work_order` as clinical ops: `pending_payment`, no order line, no order, listed for the home page, not blocking the patient: passed.
  - B2. `add_work_order`: the cart item is linked to the work order: passed.
  - B3. Approved by bank transfer: the manual row is held by the order (`order_id`) and blocks the patient: passed.
  - B4. That order cancelled: the row is back to `pending_payment` with no order, on the home page again: passed.
  - B5. Added again, approved by transfer, paid by staff: the patient has exactly one work order, the same row, in `pending_assignment`: passed.
- Not checked on dev: the home-page block's layout in a browser.
- Left on dev: nothing (rolled back).
