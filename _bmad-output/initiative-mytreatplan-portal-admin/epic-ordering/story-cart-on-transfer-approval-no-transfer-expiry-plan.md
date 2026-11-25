---
title: 'Cart on transfer approval, no transfer expiry'
type: 'feature'
ticket: '17'
created: '2026-11-25'
status: 'draft'
baseline_revision: ''
route: 'full'
route_source: 'auto'
risk: 'medium'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A doctor who approves a cart order by bank transfer keeps the items in the cart, and a second "Finish order" silently cancels the pending transfer order. The order also expires on day 14 with a single day-7 reminder, which goes against the user's decision of 2026-11-24 (epic Notes): no automatic expiry, a reminder every 7 days, staff cancel.

**Approach:** Choosing the bank transfer at checkout empties the order's patients' items from the cart, through a new `transfer_approved` payments hook. Transfer attempts get no session end, so the expiry job never expires an order whose transfer is open. The reminder goes every 7 days from `pending_at` until the order is paid or cancelled. "Finish order" no longer cancels a pending transfer order. Voucher reservations stay as they are: released only by `cancelled`.

## Decisions

- Decision (user, 2026-11-25): "Choose another payment method" stays on the transfer page. An order that has ever had a bank-transfer attempt is never expired automatically, even after a switch to card. Reminders go only while a transfer attempt is open.
- Decision (user, 2026-11-25): re-ordering a patient whose transfer is pending is not blocked in 9.17; 9.8's pending-payment work order blocks it.
- Decision (user, 2026-11-25): plan kept whole (~2100 tokens).

## Boundaries & Constraints

**Always:**
- Hook receivers run inside the transition's transaction and are idempotent (`payments/hooks.py` contract).
- One reminder per order per 7-day period even if the job runs twice or fails halfway; `reminded_at` is set only after a successful send.
- No patient name or bank detail in logs.
- EN and ES for every changed text.
- Existing open transfer attempts (dev) lose their deadline through a data migration.
- The doctor's own "Cancel order" on the transfer page stays as it is.

**Never:**
- Work orders at approval or blocking the patient (9.8).
- The admin "confirm / cancel" list (9.18).
- Changes to card gateways' lifetimes or to the 24-hour expiry of `checkout` orders and of non-transfer `pending_payment` orders.
- An ICE reservation hook (none exists; 9.7 priced the ICE at creation, and an open order keeps it).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Approve transfer | cart order for 2 patients, Pay by transfer | order `pending_payment`; both patients' items gone from cart; transfer page without a deadline | — |
| Other cart items | cart also holds a third patient added after finish | that item stays | — |
| Card instead | Pay by Redsys/fake, not yet paid | cart unchanged (9.6 rule) | — |
| Day 20 | transfer order, `pending_at` 20 days ago, attempt open | `expire_orders` leaves it `pending_payment` | — |
| Reminders | `pending_at` 7, 14, 21 days ago, job hourly | one email at each 7-day mark, none in between | failed send retried next run |
| Paid or cancelled | staff mark paid / doctor cancels | no more reminders; cancel releases the voucher | — |
| Switch to card | transfer approved, doctor switches to card and abandons it | order stays `pending_payment`, never expired; no reminders until transfer chosen again | — |
| Finish again | pending transfer order (or one switched to card) + new cart items, Finish order | new order created; transfer order untouched | — |
| Staff mark paid | day 30 | paid, invoice issued (no "expired" refusal) | — |

</frozen-after-approval>

## Code Map

- `payments/hooks.py:17` `EVENTS`: add `transfer_approved` (docstring: fired each time the doctor chooses the bank transfer; the order is `pending_payment`; 9.8 hangs its pending-payment work orders here).
- `payments/service.py`
  - `:499` `_start_payment`: for `BANK_TRANSFER`, set `session_ends = None` (drop the 14-day cap and the `GatewayRefused('the 14 days…')`), and `_fire('transfer_approved', locked)` after the `Payment` is created.
  - `:607` `expire_stale`: exclude `pending_payment` orders that have any `bank_transfer` payment (`~Q(payments__gateway=BANK_TRANSFER)` on the pending branch). `:568` `_live_attempts` already treats `None` as live.
  - `:788` `mark_transfer_paid`: unchanged.
- `payments/bank_transfer.py:22`: drop `SESSION_LIFETIME` (register without it, or with `None`; check `gateways.py:125` `session_lifetime` uses); `REMIND_AFTER` becomes the 7-day interval; `details()` `deadline` is None. Update the module docstring.
- `payments/transfers.py` `_due` / `remind_due`: due when the order is `pending_payment`, the transfer attempt is open, `pending_at` ≤ now − 7d, and the order's latest `reminded_at` (over its transfer attempts) is null or ≤ now − 7d. Keep the per-row lock and recheck. Update the docstring.
- `payments/views.py:116` `_open_transfer`: docstring only (no 14 days).
- `payments/templates/payments/transfer.html:6,26-27` and `payments/templates/payments/email/transfer_reminder.txt:11`: replace "Please pay by {date}… expires" with a line saying the order stays pending until the transfer is received. `locale/es/LC_MESSAGES/django.po`.
- `payments/migrations/00xx_transfer_no_expiry.py`: data migration setting `session_expires_at=None` on open `bank_transfer` payments.
- `cart/apps.py:17`: register `transfer_approved` → `cart_transfer_approved` = new `cart.service.transfer_approved(order)`, which deletes the order's patients' items (the same filter as `paid` :392, factored into one helper). `mark_used` stays on `paid`.
- `cart/service.py:354` `_cancel_earlier`: exclude `pending_payment` orders that have any `bank_transfer` payment, because their items have already left the cart. Resolves deferred-work.md :250.
- Tests to update:
  - `payments/tests/test_bank_transfer.py`: :182, :199, :226, :213, :286, :315 are deadline, expiry and one-reminder tests; rewrite them to the new rules.
  - `cart/tests/test_checkout.py`: :193, :204.

## Tasks & Acceptance

**Execution:**
- [ ] `payments/hooks.py`, `payments/service.py`, `payments/bank_transfer.py`: new event, no transfer session end, fire on transfer choice.
- [ ] `payments/transfers.py`: the 7-day repeating reminder.
- [ ] `payments/migrations/` (data migration), `payments/views.py` docstring.
- [ ] Templates and `locale/es/LC_MESSAGES/django.po`: deadline text removed or replaced (EN + ES); run `compilemessages`.
- [ ] `cart/apps.py`, `cart/service.py`: the receiver, and `_cancel_earlier` skipping transfer orders.
- [ ] `payments/tests/test_bank_transfer.py`, `payments/tests/test_service.py`, `cart/tests/test_checkout.py`: one test per matrix row. The receiver raising rolls the approval back.
- [ ] `_bmad-output/initiative-mytreatplan-portal-admin/deferred-work.md`: mark :250 and the day-7 re-check (:254) resolved by 9.17.

**Acceptance Criteria:**
- Given a doctor with two patients' items, when they approve the order by bank transfer, then the order is `pending_payment`, the cart has no items for those patients, and the transfer page shows no deadline.
- Given a transfer order frozen 20 days ago, when `expire_orders` runs, then the order is still `pending_payment` and its voucher is still reserved.

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Verification

**Commands:**
- `. .venv/bin/activate && python -m pytest -q -p no:cacheprovider --tb=line`: expected 0 failures (18 local skips).
- `python manage.py makemigrations --check --dry-run`: expected no changes once the data migration is committed.
