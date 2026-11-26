---
title: 'Saved cards and repeat charges on Redsys'
type: 'feature'
ticket: '11'
created: '2026-11-26'
status: 'in-progress'
baseline_revision: '9ecbe907440506005bb9781f767d8f030bcbafb4'
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

**Problem:** Renewals (epic-subscriptions 8.5–8.8) must charge a doctor's card without the doctor present. Today the registry has unused `save_card`/`charge_saved_card` slots, only the fake fills them, nothing stores a card, and no service charges an order for a period.

**Approach:** An order can ask to save the card. The first Redsys payment of such an order sends the COF "initial" fields; its signed notification yields a token that we store encrypted. A gateway-neutral service `payments.repeat.charge_saved_card(order, period)` charges the doctor's saved card for an order (Redsys: REST, MIT with COF). It is idempotent per order and period (AD-8) and returns `paid`, `declined` (the caller decides what follows) or `unknown`. The registry gets a `saves_cards` flag (on for Redsys and fake). Built against Redsys's public test terminal; the check on our own terminal waits for Santander's contract.

## Boundaries & Constraints

**Always:**
- No gateway HTTP call while a transaction or row lock is held; the order is paid only through `service.confirm_payment`.
- Token, COF transaction id and masked card number in `accounts.crypto` encrypted fields; never in logs, Sentry, URLs or the admin (AD-23). Only brand, last 4 digits and expiry are shown.
- Money is never charged twice for one order and period: a `confirmed`, `open` or `needs_attention` repeat charge blocks a new one; only a `failed` (declined) one allows a new attempt with a new `Ds_Order`.
- A Redsys REST answer is trusted only when its signature verifies and its order, amount, currency and type match the request (as `refund` does).
- One saved card per doctor and gateway; a newer save replaces the older (the old token is deleted). Account erasure deletes the cards.

**Never:**
- No renewal job, renewal order, retry schedule or email (epic-subscriptions 8.6/8.7); no Revolut (7.16); no SOAP or REST status query; no change to `_move`, `_start_payment` or the hooks (9.20 collision rule).
- No asking the product owner for Santander credentials.

## Decisions

- Decision (user, 2026-11-26): a card is saved only when the order asks (`Order.save_card`, set by whoever creates the order; 8.5 sets it for monthly or auto-renewing subscriptions). Checkout shows a consent line, no checkbox (Venta + is priced per stored card).
- Decision (user, 2026-11-26): showing and removing the saved card is not in 7.11; it goes to 8.10 (deferred-work entry).
- Decision (user, 2026-11-26): an uncertain repeat charge becomes `needs_attention` with a Sentry error by order UUID; a rerun never charges; staff check the Redsys module. No status query.
- Decision (user, 2026-11-26): one plan, not split (about 2,300 tokens).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Save at checkout | order `save_card`, Redsys | form adds `DS_MERCHANT_IDENTIFIER=REQUIRED`, `COF_INI=S`, `COF_TYPE=R`; checkout shows the consent line | — |
| Paid notification with token | signed, 0000, `Ds_Merchant_Identifier`, `Ds_Merchant_Cof_Txnid`, `Ds_ExpiryDate`, `Ds_Card_Number`, `Ds_Card_Brand` | order paid as today; one `SavedCard` stored (replacing an older one) | — |
| Token without the ask | order not `save_card` | order paid; nothing stored | — |
| Paid without token | order `save_card`, no identifier | order paid; no card; warning by order UUID | Sentry warning |
| Repeat charge approved | saved card, order `checkout`/`pending_payment`, period P | new `Payment` (period P, new Ds_Order), REST with `IDENTIFIER`, `COF_INI=N`, `COF_TYPE=R`, `COF_TXNID`, `DIRECTPAYMENT=true`, `EXCEP_SCA=MIT`; 0000–0099 → confirmed, order paid, hook once; returns `paid` | — |
| Rerun same period | a confirmed or open charge exists for (order, P) | returns its outcome; no gateway call | — |
| Declined | `Ds_Response` outside 0–99 (e.g. 0116, 0190, 0195) | payment `failed` with the code; order unchanged; returns `declined` (code) | — |
| Card expired | `expiry` before this month | `declined` (`card_expired`), no gateway call | — |
| No card / gateway can't | none saved, or `saves_cards` off | `NoSavedCard` / `UnsupportedOperation`, nothing created | — |
| Unknown | HTTP fault, timeout, bad signature, mismatch, `errorCode` | payment `needs_attention`; returns `unknown`; a rerun does not charge | Sentry error by order UUID |
| Order not payable | paid, cancelled, expired | `NOT_PAYABLE`, no gateway call | — |

</frozen-after-approval>

## Code Map

- `payments/gateways.py` -- `Adapter` (:115), `OPERATIONS` (:45), `register` (:149), `supports`/`call`, `GatewayPayment` (:105), `GatewayError`/`GatewayUnknown`. Add `saves_cards` (register kwarg, default False) and `can_save_cards(name)`; replace the docstring signatures (:23-25) with `charge_saved_card(card, payment) -> GatewayPayment` and drop `save_card(user)` (saving happens in the payment flow). `WebhookEvent` gains an optional `card` (dict or None).
- `payments/models.py` -- `Order` (:38) gains `save_card` (bool). `Payment` (:231) gains `period` (blank) and `decline_code`; a conditional unique constraint on (order, period) for non-empty period in states open/confirmed/needs_attention. New `SavedCard` (user FK CASCADE, gateway, encrypted token/cof_txnid, brand, last4, expiry `YYMM`, created_at; unique (user, gateway)). One migration.
- `payments/service.py` -- `create_subscription_order` (:441) and `_create` take `save_card=False`. Do not touch `_move`/`_start_payment`; reuse `_lock` (:294), `fail_payment` (:740), `confirm_payment` (:760).
- `payments/repeat.py` (new) -- `charge_saved_card(order, period, *, actor)` → `RepeatResult(outcome, payment, code)`; `store_card(payment, card)`; `saved_card(user, gateway)`.
- `payments/webhooks.py` `handle` (:94) -- when a verified event carries `card` and status paid, call `repeat.store_card` (own transaction) before enqueueing.
- `payments/redsys.py` -- `form` (:245) adds the COF-initial fields when `order.save_card`; `verify_webhook` (:302) reads the five card fields into `event.card`; new `charge_saved_card` modelled on `refund` (:351-398) with a new `Ds_Order` (`new_order_id` :218), stored on the Payment before the call; `register` (:401) `saves_cards=True`; update docstring :24.
- `payments/fake.py` -- `charge_saved_card` (:146) on the new signature; `save_card` (:140) removed; its checkout stores a fake card when `order.save_card`; a test switch to decline the next charge with a given code.
- `payments/views.py` `checkout` (:188) + `payments/templates/payments/checkout.html` -- consent line when `order.save_card` and the chosen gateway can save; a gateway that cannot is not offered for such an order.
- Tests to change: `payments/tests/test_redsys.py:566`, `test_revolut.py:595`, `test_webhooks.py:67`. Fixtures: `payments/tests/conftest.py` (`doctor`, `entity`, `fake_on`), `redsys_on` and `notify` helpers in `test_redsys.py`.
- Redsys COF fields come from ActiveMerchant `redsys_rest.rb` and creagia/redsys-php, NOT from Redsys's docs (pagosonline.redsys.es is blocked in the session): confirm on the test terminal and record.

## Tasks & Acceptance

**Execution:**
- [ ] `payments/models.py`, migration -- `Order.save_card`, `Payment.period`/`decline_code` + constraint, `SavedCard`.
- [ ] `payments/gateways.py`, `payments/fake.py` -- flag, new op signature, `WebhookEvent.card`, fake decline switch.
- [ ] `payments/repeat.py`, `payments/service.py`, `payments/webhooks.py` -- the service, `save_card` on order creation, storing the card.
- [ ] `payments/redsys.py` -- COF-initial form fields, card fields from the notification, MIT REST charge.
- [ ] `payments/views.py`, `checkout.html`, `locale/es/LC_MESSAGES/django.po`, `README.md` -- consent line; README "Payments: saved cards" (Venta +, test-terminal check, owed staging check).
- [ ] `payments/tests/test_repeat.py`, `test_redsys.py` -- every matrix row; signed REST responses built with the public test key.

**Acceptance Criteria:**
- Given a `save_card` order paid on Redsys's public test terminal on dev, when `charge_saved_card` runs for a second order of the same doctor and period P twice, then Redsys charges once and the second call returns the first outcome.
- Given our own Redsys terminal (after Santander's contract), the same check passes: owed in `## Staging`; the plan stays `built` until then.

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Verification

**Commands:**
- full portal suite (README command) -- expected: 0 failures.
- `manage.py makemigrations --check`, `manage.py check` -- expected: clean.

**Manual checks (staging, dev, public test terminal):**
- Whether the test terminal returns `Ds_Merchant_Identifier`/`Ds_Merchant_Cof_Txnid`; whether a REST charge also sends a notification; the decline code for a refused MIT. Record in Implementation Notes.
