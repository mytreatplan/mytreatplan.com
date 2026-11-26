---
title: 'Buy Advanced or Premium'
type: 'feature'
ticket: '5'
created: '2026-11-26'
status: 'built'
baseline_revision: '7179d5772bad376461f54c1fa6d73a8e8bec16ea'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 1
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
- Decision (user, 2026-11-26, from review): a subscription voucher applies only to the year upfront. On monthly the page offers no voucher field, and a code posted with monthly is refused with a message, no order (a voucher covering a month would make a 0.00 monthly order with no saved card).

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

- `subscriptions/views.py` `upgrade_purchase` :52 (placeholder) + `subscriptions/templates/subscriptions/upgrade_purchase.html` -- becomes the purchase form (GET shows periods; the voucher field only for the year upfront; POST calls the service and maps its refusals). `_level` builds the prices shown; `upgrade.html:27-38` prices and the Buy link. The view holds no business rule (ONBOARDING.md section 6): no `Order` query, no lock, no period or voucher decision, no `save_card` choice.
- `subscriptions/service.py` -- `upgrade_options` :309, `record_of` :165, `change` :507 (via `_write` :475: `StaleVersion`, `ManualLocked`, `ChangeRefused`; `_check` :421), reasons :97-99 (add `purchase`), `doctor_actor`/`SYSTEM_ACTOR` :125-135. Add `monthly_possible(user)`, the `paid` receiver `activate(order)`, `open_purchase(user)` (the doctor's open subscription order, read through `payments.service`, not `payments.models` from a view) and `purchase(user, level, period, voucher_code)`: locks the doctor's row, returns an open order if one exists, refuses a period not offered (monthly where not `monthly_possible`) and a voucher code with monthly (`OrderRefused` with a new reason the page shows), resolves the voucher, calls `create_subscription_order(..., save_card=monthly_possible(user))`, returns the order. `activate` backfills a missing record with `ensure_basic(user, SYSTEM_ACTOR, reason=BACKFILL)`.
- `subscriptions/apps.py` :24-27 -- register `paid` → `subscriptions_activate` (example name in `payments/hooks.py:4`). Receivers run inside the transition's transaction.
- `catalogue/resolver.py` `subscription_rule` :298 / `billing_rule_for` :266, `catalogue/models.py:503` `gateways_for(country)`, `settings.REGION_PROFILE.gateways`; `payments.gateways.can_save_cards`.
- `catalogue/pricing.py` `price_subscription` :288-317 (monthly = one month), refusals :96-118; `payments/views.py` `REFUSALS` :60-87.
- `payments/service.py` `create_subscription_order` :441 (`save_card`), `_create` :384 (€0 → `_pay_free` when an entity exists; `NoPaymentMethod`, `EntityRefused`, `OrderRefused`); `subscriptions/vouchers.py` `voucher_for_code` :48.
- Pattern: the service owns the lock, the open-order check and the order (`cart/service.py` `finish`); the view only calls it and re-renders on refusal (`cart/views.py:178-188`, `cart/templates/cart/cart.html:37-38`).
- `payments/views.py` `checkout_return` :254, `payments/templates/payments/return.html` -- for a paid SUBSCRIPTION order, a "Continue" link to `welcome` (`accounts/urls.py:13`) instead of "Back to home".
- Tests: `subscriptions/tests/helpers.py` (`ADVANCED`/`PREMIUM` Iberia €60/€600, €120/€1200, `doctor()`, `DAY`), `integration/tests/snapshot_rows.py` (Iberia subscription rule `['redsys']` :246; add a Rest-of-EU subscription rule with `['revolut']` for the IE case; `voucher(...)` :333), `payments/tests/test_redsys.py` `redsys_on`, `payments/tests/conftest.py` `fake_on`.

## Tasks & Acceptance

**Execution:**
- [ ] `subscriptions/service.py` -- `monthly_possible(user)`; `open_purchase(user)`; `purchase(...)` (above; voucher only with upfront); `activate(order)` (SUBSCRIPTION orders only; idempotent; error to Sentry when the record cannot take it; backfill reason `BACKFILL`); reason `purchase`.
- [ ] `subscriptions/apps.py` -- register the receiver.
- [ ] `subscriptions/views.py`, `subscriptions/urls.py`, `upgrade_purchase.html` -- the form (voucher field shown with the year upfront only), guards, POST → `service.purchase` → `checkout` (or the return page for a €0 order); refusals re-render with the message and the typed code.
- [ ] `payments/views.py`, `return.html` -- the welcome link after a paid subscription order.
- [ ] `subscriptions/emails.py`, `subscriptions/tasks.py`, `subscriptions/templates/subscriptions/email/purchase_confirmation{,_subject}.txt` -- the confirmation email, queued by the `paid` receiver after commit (model: `cart/emails.py`, `cart/tasks.py` `send_order_email` with `mytp_core.tasks.run_once`, `invoices.pdf.render_codes_only`, `pdf.language_of`).
- [ ] `locale/es/LC_MESSAGES/django.po`, `README.md` -- ES strings; a "Buying a subscription" section.
- [ ] `subscriptions/tests/test_purchase.py` -- every matrix row (fake and Redsys adapters; IE with a Revolut rule), plus: a voucher code posted with monthly is refused, no order; `service.purchase` tested directly (open order returned, forged period refused); the backfill row's reason is `backfill`.

**Acceptance Criteria:**
- Given dev, when a Basic doctor buys Advanced upfront with a partial subscription voucher, then the record shows Advanced and the doctor sees Advanced prices.
- Given dev with Redsys's test terminal, when a Basic ES doctor buys Advanced monthly, then they are invoiced the first month and their card is saved.
- Given a 100 % voucher, when the doctor buys, then no gateway is used and a €0 invoice is issued.

## Implementation Notes

- `SubscriptionChange.order_id` (nullable UUID, no database link, unique when set; migration `subscriptions.0004_change_order`): the plan asks for a change row naming the order and a per-order idempotent receiver; the column carries it and the unique constraint is the last word on "one change per order". `_write` takes it as an optional argument (`change`'s signature is unchanged).
- `activate` writes with the doctor as the actor (the purchase is the doctor's): an expired manual record then becomes standard, as a doctor's own switch does. Refusals (`ChangeRefused`, `ManualLocked`, `RecordGone`, `StaleVersion`) and a record that moved (manual that counts, a paid tier that counts) are a Sentry error by order UUID (`subscription purchase ...: order <uuid>`), never a raise: the payment is kept. The email is queued only when the record changed.
- The structure test allows only `subscriptions.service` to name the models: the email and the views read through `service.purchase_values(order)`, `service.MONTHLY` / `service.UPFRONT`.
- A doctor on a paid tier that counts (an Advanced doctor clicking "Upgrade to Premium") is redirected to `upgrade` with the message "To change between paid levels, please contact us." until 8.8; the upgrade screen's button stays as it was.
- A level whose only price is monthly, for a doctor where monthly is not possible, shows "This level cannot be bought online for your country yet" (no form).
- `integration/tests/snapshot_rows.py` gains `eu_subscription_rule()` (Rest of EU, Revolut only), added by the purchase tests only, so other tests keep "only Iberia has a `subscription` rule".

## Plan Change Log

- 2026-11-26, review loop 1. Triggers: triage 1 (intent_gap: a voucher covering a month made a 0.00 monthly order with no saved card; user decided vouchers apply to the year upfront only), triage 2 (bad_plan: purchase rules in the view, against ONBOARDING.md section 6), triage 3 (patch: backfill reason). Amended: Code Map (service `purchase`/`open_purchase`, view holds no rule), tasks (voucher upfront only, tests). Avoided: business rules in `subscriptions/views.py`; a monthly order with a voucher. KEEP (from the first implementation, copy in `.handoff/8-5-first-implementation.patch`, which passed the full suite): everything in Implementation Notes below; `monthly_possible` excluding `gateways.FAKE` and requiring a region gateway; `activate` as written (doctor actor, `_counts` guard, `_report` to Sentry by order UUID, `enqueue_on_commit(send_purchase_email)`); `term_of`; `purchase_values`; the email/task modules and templates; `to_welcome` on the return page; the ES strings; README and deploy/README sections; the test helpers and fixtures of `test_purchase.py` (`loaded`, `spanish`, `irish`, `pay_on_fake`, `capture`, `queued`); the updated tests in `test_upgrade.py` and `signup/tests/browser/test_success.py`.

## Review Triage Log

| # | Lens | Finding | Verdict | Route | Evidence |
|---|------|---------|---------|-------|----------|
| 1 | quick | Monthly + subscription voucher >= one month's price: 0.00 order paid by `_pay_free`, no gateway, no saved card; `activate` writes monthly, auto-renew, 12-month commitment that cannot be charged in month 2; email says "Next charge" and "card not saved" | high | intent_gap | `price_subscription` prices monthly as one month (pricing.py:288-317); `_create` pays 0.00 via `_pay_free` (service.py:421) without a gateway; `_periods` offers monthly whatever the voucher. The frozen intent says monthly needs a card-saving gateway and a full voucher pays at once; it does not say which wins. |
| 2 | quick | Purchase business rules (open-order lookup, per-doctor lock, forged-period refusal, `save_card` choice, order creation) live in `subscriptions/views.py`, against ONBOARDING.md section 6 | medium | bad_plan | ONBOARDING.md:144 "Business rules live in services"; the cited pattern `cart/views.py` only calls `cart.service.finish`. The plan's task put "POST -> order -> checkout" in views.py. 8.8 (paid-tier change) would duplicate it. |
| 3 | quick | `activate` calls `ensure_basic(user, SYSTEM_ACTOR)` with the default reason `signup` for a backfilled doctor | low | patch | `ensure_basic` defaults `reason=SIGNUP` (service.py:410); `assign_basic` passes `reason=service.BACKFILL` (assign_basic.py:41). Fix: pass `reason=BACKFILL`. |
| 4 | quick (loop 1) | Without JavaScript, a monthly refusal for a voucher re-renders with the voucher field `hidden` but still holding the code, so the doctor cannot clear it and every monthly submit is refused again | low | patch | `_page` passes `chosen='monthly'`; the template adds `hidden` server-side when `chosen == "monthly"`; only `purchase-period.js` empties the input. ONBOARDING.md section 6: pages work without JavaScript. Fix: no server-side `hidden` (the script hides it), plus a test. |
| 5 | quick (loop 1) | `subscriptions/views.py` imports `payments.models.Order` to test `order.state == Order.PAID`, against the Code Map ("not `payments.models` from a view") | low | patch | The import and the comparison are in the view. Fix: decide "paid at once" without the model (e.g. `order.paid_at is not None`) and drop the import. |
| 6 | quick (loop 1) | `service.purchase` does not check that the doctor may buy the level (Basic or expired, not a manual record that counts, `can_upgrade`); only the view does, outside the doctor lock, so another caller (8.8) or a request racing a `paid` creates an order that `activate` must then refuse with the payment kept | medium | patch | `purchase` checks period, voucher and open order only; the view's guard reads `upgrade_options` before `purchase` takes the lock. Fix: re-check `upgrade_options(user)` under the lock in `purchase` and refuse with an existing exception; the view keeps its redirects. |
| 7 | quick (loop 1) | Two concurrent `paid` runs of one order make `activate` raise `IntegrityError` on the unique constraint | false | - | `paid` fires inside the transition, which locks the order row (`payments.service.lock_order`) and moves it to PAID once (`_pay_free` service.py:453-462, likewise the gateway path), so a second `paid` for the same order never runs concurrently; a later one is the idempotent repeat already tested. |
| 8 | quick (loop 1) | `subscriptions/views.py` module docstring still says the view creates the order with `create_subscription_order` and chooses `save_card` | low | patch | Docstring lines 10-21 describe the first implementation; the view now calls `service.purchase`. Fix: reword. |

## Verification

**Commands:**
- full portal suite (README command) -- expected: 0 failures.
- `manage.py makemigrations --check`, `manage.py check`, `msgfmt --check` -- expected: clean.

**Manual checks (staging, dev):**
- The three acceptance criteria on dev (Redsys public test terminal; a subscription voucher in the catalogue snapshot).
