---
title: 'Buy Advanced or Premium'
type: 'feature'
ticket: '5'
created: '2026-11-26'
status: 'in-progress'
baseline_revision: '7179d5772bad376461f54c1fa6d73a8e8bec16ea'
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

**Problem:** A Basic doctor sees Advanced and Premium on the upgrade screen, but "Buy" leads to a "coming soon" placeholder (`upgrade_purchase`): no order, no payment, and nothing changes the subscription record when a subscription order is paid.

**Approach:** The purchase page lets the doctor pick monthly (12-month commitment, only where one of their real gateways saves cards, 7.11) or annual upfront, and type a subscription voucher. It creates the order with `create_subscription_order` (saving the card whenever a real gateway of the doctor can) and sends the doctor to the existing checkout; a voucher covering the whole price pays the €0 order at once with a €0 invoice. A `paid` receiver in `subscriptions` moves the record to the bought tier through the subscriptions service. The return page of a paid subscription order leads to the welcome page.

## Boundaries & Constraints

**Always:**
- The record changes only through `subscriptions.service` (`change`, version-checked), in the `paid` hook's transaction, idempotent per order (a repeat `paid` changes nothing).
- On activation: tier and `subscription_id` of the order line, `billing_period` of the line, `started_on` the payment day (Europe/Madrid), `ends_on` 12 months later minus one day, `auto_renew` on, `status` active; a `SubscriptionChange` with reason `purchase` and the order id.
- Monthly is offered only when the doctor's subscription billing rule (their country, the region's gateways) lists a real gateway with `gateways.can_save_cards`; the fake gateway never makes monthly possible. A Revolut-country doctor sees annual only.
- Prices on the page are net with "+ VAT where applicable"; the order carries the VAT.
- A manual record (`ManualLocked`) and a doctor already on a paid, active tier cannot buy here (the page sends them back to `upgrade`).

**Never:**
- No upgrade between paid tiers (8.8), no renewal, monthly charge, retry or renewal emails (8.6, 8.7), no cancellation (8.10), no admin client record (8.11), no change to `_move`, `_start_payment`, the hooks module or pricing arithmetic, no new `_private` imports across modules.

## Decisions

- Decision (user, 2026-11-26): an annual upfront purchase also saves the card when one of the doctor's real gateways can save cards (as monthly does), so 8.6 can charge the annual renewal; the payment link is for when that charge fails.
- Decision (user, 2026-11-26): a paid subscription purchase sends one confirmation email after commit: level, billing (monthly with 12-month commitment, or annual), amount, the end of the term (monthly: the next charge date), whether the card is saved for renewals, how to cancel (contact us until 8.10), with the codes-only invoice PDF attached; once per order, one try, as 9.20's `cart/emails.py`/`cart/tasks.py`. Renewal and failure emails stay with 8.6/8.7.
- Decision (user, 2026-11-26): 8.5 stores nothing for included cases; 8.13 creates the grant from the purchase (and for subscriptions bought before it ships).
- Decision (user, 2026-11-26): one plan, not split (about 1,900 tokens).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Annual, card | Basic ES doctor, Advanced, upfront | order (one line, upfront price, `save_card`) → checkout → paid → record advanced, upfront, auto-renew, ends in 12 months; card saved; return page links to welcome; one confirmation email with the invoice | — |
| Annual, Revolut | Basic IE doctor, upfront | order without `save_card` (Revolut cannot save cards yet); email says renewal by payment link | — |
| Email once | task re-run, or `paid` fires again | no second email | failure logged by order UUID, no retry |
| Monthly | Basic ES doctor (Redsys), Premium monthly | order with `save_card`, one month's price → checkout shows consent line → paid → record premium, monthly; invoice for the first month | — |
| Revolut country | Basic IE doctor | page offers upfront only; a forged monthly POST | refused, no order |
| Voucher, partial | subscription voucher €100 on Advanced upfront | order total reduced; voucher reserved, consumed on payment | — |
| Voucher, full | voucher ≥ price | €0 order paid at once (no gateway), €0 invoice, record changed, straight to the return page | — |
| Voucher refused | unknown, used, wrong type or currency | page re-shown with the message and the typed code; no order | existing `REFUSALS` texts |
| Open order | doctor already has an open subscription order | the page sends them to that order's checkout; no second order | — |
| Already paid tier / manual | advanced active, or manual record | redirected to `upgrade`; no order | — |
| Paid twice | `paid` fires again | record unchanged | idempotent |
| Record moved meanwhile | record became manual or paid between order and payment | record unchanged; Sentry error by order UUID; payment kept | staff decide (refund) |

</frozen-after-approval>

## Code Map

- `subscriptions/views.py` `upgrade_purchase` :52 (placeholder) + `subscriptions/templates/subscriptions/upgrade_purchase.html` -- becomes the purchase form (GET shows periods + voucher field; POST creates the order). `_level` builds the prices shown; `upgrade.html:27-38` prices and the Buy link.
- `subscriptions/service.py` -- `upgrade_options` :309, `record_of` :165, `change` :507 (via `_write` :475: `StaleVersion`, `ManualLocked`, `ChangeRefused`; `_check` :421), reasons :97-99 (add `purchase`), `doctor_actor`/`SYSTEM_ACTOR` :125-135. Add `monthly_possible(user)` and the `paid` receiver `activate(order)`.
- `subscriptions/apps.py` :24-27 -- register `paid` → `subscriptions_activate` (example name in `payments/hooks.py:4`). Receivers run inside the transition's transaction.
- `catalogue/resolver.py` `subscription_rule` :298 / `billing_rule_for` :266, `catalogue/models.py:503` `gateways_for(country)`, `settings.REGION_PROFILE.gateways`; `payments.gateways.can_save_cards`.
- `catalogue/pricing.py` `price_subscription` :288-317 (monthly = one month), refusals :96-118; `payments/views.py` `REFUSALS` :60-87.
- `payments/service.py` `create_subscription_order` :441 (`save_card`), `_create` :384 (€0 → `_pay_free` when an entity exists; `NoPaymentMethod`, `EntityRefused`, `OrderRefused`); `subscriptions/vouchers.py` `voucher_for_code` :48.
- Pattern for POST → order → checkout with a voucher field and re-render on refusal: `cart/views.py:178-188`, `cart/templates/cart/cart.html:37-38`.
- `payments/views.py` `checkout_return` :254, `payments/templates/payments/return.html` -- for a paid SUBSCRIPTION order, a "Continue" link to `welcome` (`accounts/urls.py:13`) instead of "Back to home".
- Tests: `subscriptions/tests/helpers.py` (`ADVANCED`/`PREMIUM` Iberia €60/€600, €120/€1200, `doctor()`, `DAY`), `integration/tests/snapshot_rows.py` (Iberia subscription rule `['redsys']` :246; add a Rest-of-EU subscription rule with `['revolut']` for the IE case; `voucher(...)` :333), `payments/tests/test_redsys.py` `redsys_on`, `payments/tests/conftest.py` `fake_on`.

## Tasks & Acceptance

**Execution:**
- [ ] `subscriptions/service.py` -- `monthly_possible(user)`; `activate(order)` (SUBSCRIPTION orders only; idempotent; error to Sentry when the record cannot take it); reason `purchase`.
- [ ] `subscriptions/apps.py` -- register the receiver.
- [ ] `subscriptions/views.py`, `subscriptions/urls.py`, `upgrade_purchase.html` -- the form, guards, POST → order → `checkout` (or the return page for a €0 order).
- [ ] `payments/views.py`, `return.html` -- the welcome link after a paid subscription order.
- [ ] `subscriptions/emails.py`, `subscriptions/tasks.py`, `subscriptions/templates/subscriptions/email/purchase_confirmation{,_subject}.txt` -- the confirmation email, queued by the `paid` receiver after commit (model: `cart/emails.py`, `cart/tasks.py` `send_order_email` with `mytp_core.tasks.run_once`, `invoices.pdf.render_codes_only`, `pdf.language_of`).
- [ ] `locale/es/LC_MESSAGES/django.po`, `README.md` -- ES strings; a "Buying a subscription" section.
- [ ] `subscriptions/tests/test_purchase.py` -- every matrix row (fake and Redsys adapters; IE with a Revolut rule).

**Acceptance Criteria:**
- Given dev, when a Basic doctor buys Advanced upfront with a partial subscription voucher, then the record shows Advanced and the doctor sees Advanced prices.
- Given dev with Redsys's test terminal, when a Basic ES doctor buys Advanced monthly, then they are invoiced the first month and their card is saved.
- Given a 100 % voucher, when the doctor buys, then no gateway is used and a €0 invoice is issued.

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Verification

**Commands:**
- full portal suite (README command) -- expected: 0 failures.
- `manage.py makemigrations --check`, `manage.py check`, `msgfmt --check` -- expected: clean.

**Manual checks (staging, dev):**
- The three acceptance criteria on dev (Redsys public test terminal; a subscription voucher in the catalogue snapshot).
