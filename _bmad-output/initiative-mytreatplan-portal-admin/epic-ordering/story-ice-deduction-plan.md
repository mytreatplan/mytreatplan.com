---
title: 'ICE deduction'
type: 'feature'
ticket: '7'
created: '2026-11-25'
status: 'draft'
baseline_revision: 'a1323173ee371c462473d9e44cd2f332581ce2b0'
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

**Problem:** The pricing already takes an ICE credit per line (5.12), but nothing ever supplies one: `cart.service._order_lines` passes `None`, so a doctor who paid an ICE pays the full planning price afterwards.

**Approach:** When the cart is turned into an order, each TPS or VirtuaOrtho line whose patient has a paid, unused, non-refunded ICE in the same billing company and currency gets that ICE line's snapshot `net` as its ICE credit. A new deduction record follows the order through the 7.1 hooks like a voucher redemption: reserved at `created`, consumed at `paid`, released at `cancelled`/`expired`. A database constraint ensures only one live deduction per ICE line.

## Decisions

- Decision (user, 2026-11-25): merge, staging and completion are DELEGATED for this story: push the story branch and open the PR; merge when the user says "merge"; give the user a read-only script for the dev checks over SSH (a cloud session has no SSH); push the completion commit to master.
- Decision (user, 2026-11-25): plan kept whole (~2400 tokens).
- Decision (user, 2026-11-25): 5.12's `ICE_TWICE` becomes one credited line per patient code; each patient's planning in one order can take that patient's own ICE.
- Decision (user, 2026-11-25): the checkout page shows "ICE deducted −80.00 EUR" under a credited line, whose amount is its reduced net; no change on the cart page.
- Decision (user, 2026-11-25): a refunded planning order keeps its deduction consumed (like a voucher); the ICE is not offered again.
- Decision (user, 2026-11-25): an ICE order refunded while a pending order holds its deduction changes nothing; that order can still be paid with the credit.

## Boundaries & Constraints

**Always:** Credit = the ICE order line's `net` at its own snapshot, capped by the pricing at the discounted planning line (existing `taken = min(...)`). Only an ICE whose order is `paid` (a `refunded` order gives none); same doctor, same patient code, same `company_code` and currency as the planning line — otherwise no credit (never a refusal). Oldest eligible ICE first. A line with `included_case` set gets no credit. A refused ICE credit never blocks the order. No patient name in logs, events or messages. EN and ES.

**Never:** Changes to gateways, refunds or invoices numbering; the included-case tick (8.13); closing the live ICE work order (9.8); a deduction on subscription orders.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy | patient has a paid ICE (net 80 EUR); medium TPS in cart | order line `ice` = 80, net reduced; deduction reserved | — |
| Paid | that order paid | deduction consumed; a later TPS for the patient gets no credit | — |
| Two pending | second order for the same ICE while first is open | — | DB refuses the second live deduction (`HookFailed`), order not created, cart shows a message |
| Cancel/expire | first order cancelled or expired | deduction released; next order gets the credit | — |
| Finish again | 9.6 cancels the earlier unpaid order | earlier deduction released, new order reserves it | — |
| Refunded ICE | ICE order `refunded` | no credit | — |
| Other company/currency | ICE billed by another company or currency | no credit, order created | — |
| ICE bigger than line | credit > discounted line | line net 0, credit capped | — |
| Two patients | each with own paid ICE | both lines credited | — |
| Voucher too | try-me voucher + ICE | ICE first, voucher spread on the rest (existing) | zero-before-voucher refusal still applies |
| Entity change | `choose_entity` re-prices | credit kept (`ice_asked`) | — |
| Transfer approved | 9.17 bank transfer approval | deduction stays reserved | — |

</frozen-after-approval>

## Code Map

- `catalogue/pricing.py:327-345` `_price` — the ICE credit. `ICE_TWICE` (:103, :330) limits an order to one credited line: change it to one credited line **per patient code**. Keep `ICE_NOT_PLANNING`, `ICE_CURRENCY`, and the cap. `PLANNING_CATEGORIES` :88. Tests: `catalogue/tests/test_pricing.py:284-306` (the test ICE is 80 EUR, `ICE-EVAL`).
- `cart/service.py:334` `_order_lines(items)` passes `None` as the ICE: look up the credit here. `:373` `finish` → `payments.create_order` (`payments/service.py:422`). To know a line's company and currency before pricing, use the resolver rule for the product and the billing country. If that is awkward, price once without credits, read `company_code`/`currency`, then build the credited lines.
- `payments/models.py`:
  - `Order.state` (PAID, REFUNDED :43-48), `company_code` :64, `currency` :63.
  - `OrderLine.ice_asked` :142, `ice` :152, `net` :154, `included_case` :158, `patient_code` :162, `product_id` :139.
  - Tell an ICE line by `resolver.product(product_id).category == Product.ICE` (`catalogue/models.py:117`). Precedent: `payments/service.py:371`.
- `payments/service.py:216` `_inputs` already re-asks `ice_asked` on re-pricing; `:284` `_fire` wraps hook errors into `HookFailed`; `created` fires at :412 inside the transaction.
- `payments/hooks.py` `register(event, name, receiver)`; events `created`, `paid`, `cancelled`, `expired` (nothing on `transfer_approved` or `refunded`).
- Copy the pattern from `subscriptions/vouchers.py:87-160` (`_locked`, `reserve`, `consume`, `release`: idempotent per order, `select_for_update`), `subscriptions/models.py:160` `VoucherRedemption` (plain UUIDs, partial unique constraint on live states), and the `subscriptions/apps.py` registration. Tests to copy: `subscriptions/tests/test_vouchers.py:99-335`.
- `patients/apps.py:18` registers `create_work_orders_for_order` on `paid`; `patients/service.py:127` `patient_of`.
- `payments/templates/payments/checkout.html:24-38` shows only each line's `net`. `payments/views.py:71-73` holds the ICE refusal texts. `cart/views.py` refusal mapping (9.6 `_refusal_message`).
- Do not change `invoices/` (line net is already the reduced amount) or `patients/service.py` `_is_case` (9.8).

## Tasks & Acceptance

**Execution:**
- [ ] `patients/models.py` + migration -- `IceDeduction`: `ice_line_id`, `order_id`, `order_line_id`, `doctor_uid` (UUIDs), `patient_code`, `state` (reserved/consumed/released), `amount`, `currency`, timestamps; unique on `order_line_id`; unique on `ice_line_id` where state is live.
- [ ] `patients/ice.py` (new) -- `available_credit(doctor, patient_code, company_code, currency)` → (ICE line, Money) or None. Plus `reserve`, `consume`, and `release` order receivers. The receivers re-derive each credited line's ICE line, find nothing for an order without `ice_asked`, and are idempotent.
- [ ] `patients/apps.py` -- register the receivers: `created` → reserve, `paid` → consume, `cancelled`/`expired` → release.
- [ ] `catalogue/pricing.py` -- `ICE_TWICE` per patient code; update `payments/views.py:71`'s text.
- [ ] `cart/service.py` -- `_order_lines` asks `available_credit` for planning lines; a `HookFailed` from a taken ICE maps to a cart message.
- [ ] `payments/templates/payments/checkout.html` -- "ICE deducted −X" note under a credited line (`line.ice` > 0).
- [ ] `locale/es/LC_MESSAGES/django.po` -- Spanish for new strings.
- [ ] `patients/tests/test_ice_deduction.py`, `catalogue/tests/test_pricing.py`, `cart/tests/test_checkout.py` -- one test per matrix row; race (two orders) → `HookFailed`; idempotent hooks.

**Acceptance Criteria:**
- Given a patient with a paid 80 EUR ICE, when the doctor orders a medium TPS for that patient and pays with the fake gateway, then the line's `ice` is 80, the order total is reduced by 80 plus VAT, the deduction is consumed, and a later TPS for the patient gets no credit.
- Given a pending order holding the ICE, when it is cancelled, then a new order for the patient gets the credit again.

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Design Notes

The ICE line is re-derived in the hooks (oldest paid, non-refunded ICE line of the doctor with that patient code, company and currency, without a live deduction) rather than stored on the order line. This avoids a new `OrderLine` field and pricing-dataclass change. The partial unique constraint turns a race between two orders into `HookFailed`, exactly as for try-me vouchers. If the re-derived ICE amount differs from `ice_asked` (e.g. the earlier choice was taken meanwhile), reserve fails and no order is created.

## Verification

**Commands:**
- `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU="; . .venv/bin/activate && python manage.py compilemessages -v 0 && python -m pytest -q -p no:cacheprovider --tb=line` -- expected: 0 failures (18 local skips normal).
- `python manage.py makemigrations --check --dry-run` -- expected: no changes.
