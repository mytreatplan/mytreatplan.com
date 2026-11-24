---
title: 'Cart and checkout'
type: 'feature'
ticket: '6'
created: '2026-11-24'
status: 'done'
baseline_revision: '0b99db7dfd261695501dbde6066918726004f493'
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

**Problem:** Cart items (9.3/9.4) cannot become an order: there is no "finish order", no voucher entry, and the order path refuses a patient on any line but an ICE (and quantity > 1), has no maker or shipping snapshot, and never re-checks the one-live-work-order rule before taking a payment.

**Approach:** "Finish order" on the cart page turns the doctor's cart into one service order through 7.1's checkout (`payments.service.create_order`), with an optional try-me voucher (8.4), the maker and shipping address snapshotted per line, and the one-live rule checked at creation and again before payment.

## Decisions

- Decision (user, 2026-11-24): no "use an included case" tick in 9.6; it comes with 8.13 (deferred-work). Cases are priced normally until then.
- Decision (user, 2026-11-24): the try-me voucher code is entered on the cart page next to "Finish order"; the order is created with it; a refused code re-shows the cart with the reason.
- Decision (user, 2026-11-24): items stay in the cart until the order is paid. "Finish order" again cancels the doctor's earlier unpaid order from the cart and creates a fresh one. On payment (gateway or €0), the paid patients' items leave the cart.
- Decision (user, 2026-11-24): a cart needing two billing companies (or currencies) is refused at "Finish order" with a message to order those items separately.
- Decision (user, 2026-11-24): new `OrderLine` fields: maker, other maker name, shipping address snapshot (VirtuaOrtho lines); `accounts.shipping.mark_used` runs when the order is paid. An item whose address was deleted makes "Finish order" refuse, asking the doctor to remove and re-add that item.
- Decision (user, 2026-11-24): a voucher on an order that already comes to zero before the voucher is refused with a message and stays unused.
- Decision (user, 2026-11-24): every patient is checked again when payment starts and before a €0 order is paid; a patient with a new live work order cancels the order with a clear message. A live ICE still lets a TPS or VirtuaOrtho through.
- Decision (user, 2026-11-24): plan kept whole (~2300 tokens); approved and stopped (build in a fresh session). Merge, staging checks over SSH, branch push and the completion commit to master are DELEGATED for this story.

## Boundaries & Constraints

**Always:** One service per patient per order; every line carries its patient code; Spark lines keep their quantity. Usable fiscal entity required (block with `settings_fiscal_add?next=` as in the wizard). Prices only from the existing pricing (`create_order` → `price_order_for`); no new pricing rules. No patient name in URLs, logs, messages or work-order events. Doctor-only views. EN and ES.

**Never:** Work orders for TPS/VirtuaOrtho lines and closing a live ICE (9.8), the ICE deduction (9.7), the included-case tick (8.13), order/receipt emails (9.8), changes to gateways or the checkout page's payment steps.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy | two patients' items, Finish order | one order, two lines with patient codes, maker/address snapshots; checkout page | — |
| Persisted | sign out and in | cart unchanged | — |
| Try-me voucher | valid code | order created with it; checkout total reduced | unknown/used/wrong currency → cart with reason |
| Voucher covers all | voucher ≥ total | €0 order paid at once; voucher consumed; items leave cart | — |
| Voucher on zero order | order already 0 before voucher | — | refused, voucher unused |
| Voucher reuse | same doctor, second order | — | refused (used) |
| Finish twice | earlier unpaid order exists | earlier one cancelled (voucher released), fresh order | — |
| Paid | Redsys/fake gateway pays | address marked used; paid items removed from cart | — |
| Two companies | items billed by two companies | — | refused with message |
| Address gone | VO item's address deleted | — | refused: remove and re-add the item |
| Live WO at payment | patient got a live non-ICE work order after creation | — | order cancelled, message |
| Spark quantity | Spark 6 aligners | line quantity 6 | — |
| No entity | no usable fiscal entity | block with link | refused |
| Empty cart | no items | no Finish button | POST refused |

</frozen-after-approval>

## Code Map

- `payments/models.py:38` `Order`, `:129` `OrderLine` (`patient_code`, `quantity`, `add_ons`; add `maker`, `other_name`, `shipping` JSON snapshot + migration).
- `payments/service.py:383` `create_order(user, lines, *, actor, voucher=None, entity=None)`, `:339` `_create`, `:313` `_check_patients` (ICE only, quantity 1 → generalise: every service line needs its own active patient once per order, any quantity; live rule with the live-ICE exception for non-ICE lines), `:212` `_inputs`, `:402` `_pay_free`, `start_payment` (add the re-check), cancel path (voucher release hook already wired).
- `payments/views.py:181` `checkout`, `:74-79` voucher refusal texts, `:375` `test_order` (precedent for creating an order from a page).
- `payments/hooks.py` -- paid hook: add a cart receiver (`cart/apps.py`) that removes the paid patients' items and calls `shipping.mark_used` for snapshotted addresses; runs inside the transition.
- `subscriptions/vouchers.py` `voucher_for_code`, `check_use`; `catalogue/pricing.py:386` `_check_voucher` (add the zero-before-voucher refusal), `:307` `_price` (`TWO_COMPANIES`, `TWO_CURRENCIES`).
- `patients/service.py:115` `live_work_order`, `:121` `live_is_ice`; `:170` `_is_case` -- do not change (9.8).
- `cart/service.py` `items_of`, `remove`; add `finish(doctor, voucher_code)` building `pricing.OrderLine`s from items (product, quantity, add-ons, patient code, maker, address); `cart/views.py:319` `cart`, `cart/templates/cart/cart.html` -- "Add another" (to `start_a_case`), voucher field, "Finish order", fiscal block.
- `accounts/shipping.py:283` `mark_used`; `accounts/fiscal.py` `has_usable_entity`.
- `invoices/service.py:236` copies line fields -- the new fields must not reach invoices with any patient name.

## Tasks & Acceptance

**Execution:**
- [ ] `payments/models.py` + migration, `payments/service.py`, `catalogue/pricing.py` -- line fields; `create_order` accepts them; generalised `_check_patients`; re-check in `start_payment` and before `_pay_free`; zero-before-voucher refusal.
- [ ] `cart/service.py`, `cart/views.py`, `cart/urls.py` (`cart_finish`, POST), `cart/templates/cart/cart.html`, `cart/apps.py` (paid receiver) -- finish flow, cancel earlier unpaid cart order, refusals mapped to texts, cart cleanup and `mark_used` on payment.
- [ ] `locale/es/LC_MESSAGES/django.po` -- Spanish for every new string.
- [ ] `cart/tests/test_checkout.py`, `payments/tests/` -- one test per matrix row (fake gateway; €0 path; hook rollback on a raise).
- [ ] `_bmad-output/initiative-mytreatplan-portal-admin/deferred-work.md` -- entry: included-case tick waits for 8.13.

**Acceptance Criteria:**
- Given a doctor with two patients' items and a try-me voucher, when they finish the order and pay with the fake gateway, then one order with two lines is paid once, the voucher is consumed and the cart is empty.
- Given the same voucher, when the doctor uses it on a second order, then it is refused.

## Implementation Notes

## Plan Change Log

## Review Triage Log

Quick lens (Opus), iteration 0.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | `_is_case` reads a product missing from the snapshot as a case; TPS/VO lines now carry patient codes, so such a line would get a work order | low | reject | `resolver.product` returns inactive products too; None only when the snapshot row is deleted between finish and payment (rare). The fix touches `_is_case`, which the plan reserves for 9.8, where it is rewritten anyway. |
| 2 | Two concurrent "Finish order" requests both create an open order for the same patients | medium | patch | `_cancel_earlier` reads without a lock; without a voucher both orders survive and both can be paid. Lock the doctor's row inside the transaction. |
| 3 | A pending bank transfer order is cancelled silently by a second finish | medium | defer | `cancel` closes a bank-transfer attempt locally. Matches the user's decision ("cancels the earlier unpaid order"); whether a transfer in progress should refuse instead is the user's call (asked). |
| 4 | Every `OrderError` in `_cancel_earlier` is shown as "payment in progress" | low | patch | StaleVersion/HookFailed also map to `PAYMENT_OPEN`; catch only `AttemptOpen`. |
| 5 | A patient archived/erased after finish passes the payment re-check | low | patch | `_patients_still_free` `continue`s on a missing active patient; creation refuses the same state. One-line fix. |
| 6 | `VOUCHER_NOTHING_TO_COVER` in the shared `_price` also hits subscriptions and `choose_entity` re-pricing | low | reject | Consistent with the user's decision (a voucher is never spent on a zero order); the re-pricing case needs a catalogue change making a reserved order free mid-checkout (unlikely). |
| 7 | Fiscal block shows on an empty cart | low | patch | Include sits outside `{% if lines %}`. |
| 8 | Template header comment out of date | low | patch | Still says "Checkout is 9.6's." |
| 9 | Unused `logging`/`logger` in cart/service.py | low | patch | Never referenced. |
| 10 | cart/views.py import order; private `_refusal_message` imported across apps | low | patch (order only) | Order fixed; the private import reuses the one refusal table rather than duplicating it (kept). |
| 11 | No end-to-end test of AC 1 | low | patch | Pieces tested separately; add one test. |
| 12 | Misleading comment in `test_a_free_order_is_checked_again_before_it_is_paid` | low | patch | Only `total` is forced to 0. |
| 13 | ICE quantity-2 guard test deleted | low | patch | The guard now lives in the resolver only; add a test back. |

## Verification

**Commands (sandbox disabled):**
- Portal, from the repo root: `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU="; export DATABASE_URL="sqlite:///$TMPDIR/s96.sqlite3"; ../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: 0 failures except the 8 local-only `invoices/tests/test_pdf.py` WeasyPrint failures (run `compilemessages` after editing the .po); `makemigrations --check --dry-run` -- no changes after the migration is committed.
- Commit on the story branch (new commits); never push, tag or amend.

**Result (2026-11-24):** full suite 4341 passed, 17 skipped, 8 failed (the known local WeasyPrint `invoices/tests/test_pdf.py` failures only); `makemigrations --check` clean. The per-doctor lock in `finish` needs PostgreSQL to be exercised (CI/staging).

## Staging

- Merge: PR #135 → 8774e5c; deploy run 45403665465 green (dev + dev-ae); CI test job passed on both PR runs (45401012456, 45401049927). Local suite 4341 passed, 17 skipped, 8 local-only WeasyPrint failures.
- Checks on dev (2026-11-24), doctor pk 18 (ES, Premium), run over SSH:
  1. Two patients' items (TPS + VirtuaOrtho DigitalTwin medium) shown in two fresh sessions, EN and ES: passed (rolled back).
  2. "Finish order" with try-me voucher EHKPU-FDSDJ: checkout with 50.00 EUR deducted, gateways redsys/bank_transfer/fake; fake-gateway payment paid, voucher consumed, cart empty (rolled back). Real Redsys test payment by the user (public test terminal, test card): order d01d7c71 (three patients, 1937.24 EUR) paid once by Redsys's notification, one confirmed attempt, one invoice, voucher consumed, cart empty, shipping address marked used, VirtuaOrtho line snapshotted.
  3. The same voucher on a second order refused (`voucher_used`), reason shown on the cart page: passed (rolled back).
  4. A second service for the same patient refused at add (`in_cart`) and at order creation (`patient_refused`): passed (rolled back).
  5. Maker and shipping snapshots on the lines, address marked used, no patient name in the order changes, no address on the invoice lines: passed.
- Left on dev: paid order d01d7c71 with its invoice, shipping address "Staging96" of doctor pk 18, voucher EHKPU-FDSDJ consumed by that doctor.
- Follow-ups decided by the user (deferred-work.md, 2026-11-24): bank-transfer order flow (cart emptied on transfer approval, pending-payment work orders, no automatic expiry, admin confirm button).
