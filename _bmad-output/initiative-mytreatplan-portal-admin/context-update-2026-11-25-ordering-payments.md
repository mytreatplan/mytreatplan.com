---
type: context-update
date: 2026-11-25
initiative: initiative-mytreatplan-portal-admin
covers: [9.17, 9.7, 9.18, 9.19]
audience: AI agents (BMad / Claude Code) working on mytreatplan.com, mytp-admin or mytp-core
---

# Context update, 2026-11-25: ordering and payments

Read this before planning or changing anything in ordering, payments, patients/work orders, the admin site's
Sales orders area, or mytp-core remote contracts. Every fact below is on master in the repo named; the plans
(source of truth, with decisions, code maps and staging results) are listed per story.

## State after this day

| Ref | Status | Plan (in `_bmad-output/initiative-mytreatplan-portal-admin/epic-ordering/`) | Merged as |
| --- | --- | --- | --- |
| 9.17 | done | `story-cart-on-transfer-approval-no-transfer-expiry-plan.md` | portal a1323173 (PR 142) |
| 9.7 | done | `story-ice-deduction-plan.md` | portal 2aeeb4e (PR 144) |
| 9.18 | done | `story-pending-bank-transfers-on-the-admin-site-plan.md` | mytp-core 57ebe03 (PR 1, tag v0.34.0), portal ec61cb6 (PR 145), mytp-admin 8dce127 (PR 50) |
| 9.19 | planned (new) | none yet; ticket in `tickets.toml` | - |
| 9.8 | next | none yet | - |

Order agreed with the product owner: 9.8, then 9.19.

## Behaviour that now exists (do not re-implement; build on it)

### Bank-transfer orders (9.17)
- Choosing bank transfer fires the new order hook `transfer_approved` (`payments/hooks.py` EVENTS). It fires EACH
  time the doctor chooses bank transfer: receivers must be idempotent. Today only `cart_transfer_approved`
  (`cart/apps.py`) listens: it empties the order's patients' items from the cart.
- The order stays `pending_payment`; bank-transfer attempts have no deadline (`session_expires_at` None) and
  `payments.service.expire_stale` never expires an order that ever had a bank-transfer attempt.
- Reminder email every 7 days while the transfer attempt is open (`payments/transfers.py` `remind_due`).
- A second "Finish order" does not cancel an earlier order that has a bank-transfer attempt
  (`cart/service.py` `_cancel_earlier`).

### ICE deduction (9.7)
- `patients/ice.py` + model `patients.IceDeduction` (migration `patients/0003_ice_deduction.py`).
- When the cart becomes an order (`cart/service.py` `finish` -> `_order_lines(items, doctor)` ->
  `ice.credited_lines`), each TPS/VirtuaOrtho line of a patient takes the `net` of that patient's OLDEST paid,
  non-refunded ICE line of the same doctor, billed by the same company in the same currency, that no live
  deduction holds and that no later planning paid in full already used (`_planned_since`, covers data from
  before 9.7). No match = no credit, never a refusal.
- Lifecycle like a voucher redemption: hooks `created` -> `ice.reserve`, `paid` -> `ice.consume`,
  `cancelled`/`expired` -> `ice.release` (`patients/apps.py`). DB constraint `ice_deduction_live_once`: one live
  (reserved/consumed) deduction per ICE line; a race fails as `HookFailed` and the cart shows `ICE_TAKEN`.
- Pricing (`catalogue/pricing.py` `_price`): `ICE_TWICE` now means one credited line PER PATIENT CODE, not per order.
- Decisions: a refunded planning keeps its deduction consumed; an ICE refunded while held changes nothing;
  a line with `included_case` set takes no deduction in the receivers. **8.13 must also keep the credit off
  included-case lines in pricing** (not done; nothing sets `included_case` yet).
- Checkout shows "ICE deducted -X" under a credited line (`payments/templates/payments/checkout.html`).

### Pending bank transfers on the admin site (9.18)
- mytp-core 0.34.0: resource `pending_transfer` (`mytp_core/access.py`, area `sales_orders`; Sysadmin and
  Financial Manager all regions + writable `state`; Commercial Manager all, Commercial Regional Manager own
  regions, view only). New LIST read contract `RemoteList` / `remote_list` (`contracts/remote.py`, max 500
  records, `truncated` flag); `PendingTransferData`; `PendingTransferChanges` (`state` in {paid, cancelled}).
- Portal: `integration/transfers.py` + `integration/remote.py` `remote_list`; route
  `GET|POST /api/v1/remote/pending-transfers` (`integration/views.py` `remote_pending_transfers`). The list logs
  every listed order in the access log before any data leaves (503 if it cannot). The write locks the order,
  checks `expected_version`, checks the order is still `pending_payment` with an open bank-transfer attempt, then
  calls `payments.service.mark_transfer_paid(order, order.total, ...)` or `cancel(...)` as
  `admin_actor(<staff uuid>)`; anything else is 409 `stale_version`.
- mytp-admin: app `orders` (no model), `/sales-orders/` (GET only, `no-store`), buttons behind
  `staff.stepup.fresh_code_required`; the confirm/cancel URL carries region, order id, version and idempotency
  key because the fresh-code re-post drops the POST body. Audit via `audit.service.record_remote(..., change=)`.
  `interface/remote.py` `read_list`; test fake `FakeTransfers` in `conftest.py`.

## Decisions to respect (product owner, 2026-11-25)
- No separate doctor "orders" page: unpaid transfer orders will appear in the doctor's invoices list as a
  pending-payment document, clearly not an invoice, with the order reference and bank details (9.19).
- Finance confirms a transfer for the order total; nothing is typed.
- Transfer work orders (9.8) are created at approval as `pending_payment` and BLOCK the patient like a live
  work order; 9.9's manual pending-payment work orders do NOT block. Note: mytp-core `LIVE_STAGES` excludes
  `pending_payment`, so 9.8 must make every live check (cart add, `_check_patients`, `_patients_still_free`)
  see transfer work orders explicitly and tell them apart from manual ones.

## Conventions confirmed in practice
- One branch per story per repo from the latest master (`feat/<ref>-<slug>`), one PR per repo.
- New remote resources or event payloads go into mytp-core first, released by tag, then vendored into BOTH apps
  with `scripts/vendor-mytp-core.sh vX.Y.Z` (same wheel file in both). Current release v0.34.0 (57ebe03).
- Staging deploys from CI on every push to master (portal: dev, dev-ae; admin: admindev). Dev checks are
  read-only Django-shell scripts run over SSH by a person, everything inside one rolled-back transaction, no
  patient names printed; dry-run them against the test fixtures first.
- Known flaky-test pattern: asserting a short digit string is absent from text that contains a random UUID.
  Assert whole values instead (fixed in `accounts/tests/test_shipping.py` and the remembered-device test).

## Open items
- deferred-work.md: a transfer arriving after its order was cancelled; whether cancel should refuse while a
  transfer is pending.
- Built but not marked done in the ticket tree: 3.5, 1.13, 3.10, 3.11, 7.4 (needs Revolut sandbox credentials), 9.12.
- Left on dev by checks: order d01d7c71 (9.6) and CY-800532 (9.18), both paid.
