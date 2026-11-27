---
title: 'Annual renewals'
type: 'feature'
ticket: '6'
created: '2026-11-27'
status: 'in-review'
baseline_revision: '270b2305d3201b940f6f9bf70c17db0f47d2ab3b'
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

**Problem:** A doctor who pays a year upfront (8.5) keeps the paid level forever: nothing reminds them, renews, charges the saved card or ends the term, and the doctor cannot turn auto-renew off.

**Approach:** A daily job in `subscriptions` sends a reminder 30 days before `ends_on`, and on `ends_on` creates the renewal order (system actor) and charges the saved card (`payments.repeat.charge_saved_card`), recording one `Renewal` row per record and term so a rerun never charges twice. No card or a decline emails a payment link (the order's checkout). A paid renewal extends the record by 12 months from the day after `ends_on` and grants the new term's included cases; a term not renewed by the end drops the doctor to Basic. The doctor turns auto-renew off and on from the upgrade screen.

## Boundaries & Constraints

**Always:**
- Only standard (not manual), upfront, paid-tier records that count; monthly records are 8.7's.
- Idempotent per record and term: `Renewal(record, term_ends_on)` unique, holding the order id; the reminder email once per record and term (`run_once`, period = `ends_on`).
- Charging runs outside `transaction.atomic()` (as `charge_saved_card` requires); every record in its own transaction; a failure for one record is logged and never stops the run.
- `paid` of a renewal order goes through `subscriptions.service` (new reason `renewal`, the order id on the change row): term `ends_on + 1 day` → `+12 months − 1 day`, `auto_renew` and `status` kept; `included.grant_term` for the new term; `_purchase_of` counts `purchase` and `renewal`. `activate` tells a renewal order by its `Renewal` row and never treats it as a purchase.
- The record changes only through the service (`_write`, version-checked); emails in the doctor's language, after commit, once.

**Never:** No monthly charges, retries or grace (8.7), no upgrade (8.8), no cancellation status or downgrade (8.9), no admin client record (8.10), no change to `payments` hooks, `charge_saved_card` or order expiry, no outbox events.

## Decisions

- Decision (user, 2026-11-27): the card is charged on `ends_on` itself. A declined or missing card leaves the doctor the rest of that day plus the order's 24 h to pay the link; from the next day they are Basic until they pay, and a late payment restores the level for the full new term. Order expiry is unchanged.
- Decision (user, 2026-11-27): a renewal costs the held subscription's catalogue price on the renewal day (the reminder shows the price as of its own day).
- Decision (user, 2026-11-27): an automatic renewal never takes a voucher (no stored voucher, none on the link's checkout). A doctor who renews in advance by hand (for example with auto-renew off) can type one.
- Decision (user, 2026-11-27): auto-renew is turned off and on from the upgrade screen, under "Your current level": the renewal date, the card's last 4 or "payment link", and a Turn off / Turn on button.
- Decision (user, 2026-11-27): with auto-renew off, the same job drops the record to Basic the day after `ends_on`.
- Decision (user, 2026-11-27): a doctor can renew in advance by hand: within the last 30 days of an annual term, the upgrade screen shows "Renew now", opening the purchase page for the same level, year upfront only, with the voucher field. Paid, it adds the new term from `ends_on + 1` as an automatic renewal does, and the job charges nothing for that term.
- Decision (user, 2026-11-27): one plan, not split (about 2,400 tokens).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Reminder | annual Advanced, auto-renew on, 30 days to `ends_on` | one reminder email (date, price, card last 4 or "payment link", how to turn off) | rerun: none |
| Renewed | `ends_on` today, card on Redsys | order + charge → paid → record runs another 12 months; new grant; receipt email with invoice | rerun: no second order or charge |
| Auto-renew off | turned off | no reminder, no order, no charge; at the end → Basic | — |
| Declined | card declined | payment-link email; still paid level until the end | — |
| No card | Revolut (IE) doctor | order without a charge; payment-link email | — |
| Paid late | link paid after the drop, order still open | record back on the level, term from old `ends_on + 1` | — |
| Renew now | annual Advanced, 20 days left, voucher typed | order (year upfront, voucher applied) → paid → term from old `ends_on + 1`; on `ends_on` the job creates nothing | before the last 30 days: no button; a forged POST refused |
| Unpaid at end | day after `ends_on`, renewal not paid | record → Basic (reason `renewal_unpaid`) | — |
| Row gone | held subscription deactivated or missing at renewal | no order; doctor emailed; → Basic at the end | Sentry warning |
| Manual / monthly | manual or monthly record | ignored | — |
| Unknown outcome | gateway outcome unknown | no email, no drop that day; Sentry error | staff decide |

</frozen-after-approval>

## Code Map

- `subscriptions/models.py` -- add `Renewal` (uuid pk, `record` FK, `term_ends_on`, `order_id` uuid unique null, `outcome`, timestamps; unique (record, term_ends_on)). Migration `0006_renewal`. Structure test: add to the service-only names.
- `subscriptions/service.py` -- reasons (:132-139) add `RENEWAL`, `RENEWAL_UNPAID`; `activate` :651-705 (guard :676 refuses a counting paid record: branch first on `Renewal` for the order → `_renew(order)`); `term_of` :628 (renewal term starts `ends_on + 1`); `_purchase_of` :775 (reason in purchase, renewal); `_counts` :226; `_write` :531; `included.grant_term` (`subscriptions/included.py:55`). New: `due_reminders(day)`, `renew_due(day)`, `drop_unrenewed(day)`, `set_auto_renew(user, on)`, `renew_now(user, voucher_code)` (the 8.5 `purchase` :720 pattern: doctor lock, open-order check, refusals; creates the `Renewal` row with the order so `activate` takes the renewal branch).
- `payments/service.py` `create_subscription_order` :441 (actor `SYSTEM_ACTOR`, `save_card=True`, no voucher), `EXPIRY_HOURS` :70 (24 h: the link lives a day), `expire_stale` :621; `payments/repeat.py` `charge_saved_card` :232 (outcomes :45 `paid`/`declined`/`unknown`, `NoSavedCard`), `saved_card` :74. Read through `payments.service`/`repeat` only.
- Job: `deploy/jobs.conf` (line: `renewsubscriptions | ... | daily 05:00 UTC`), thin command `subscriptions/management/commands/renew_subscriptions.py` → reminders, renew, drop; pattern `payments/management/commands/remind_transfers.py` + `payments/transfers.py:69` `remind_due`.
- Emails: `subscriptions/emails.py`, `subscriptions/tasks.py:20` (`run_once`, `enqueue_on_commit`); new `renewal_reminder`, `renewal_receipt` (invoice codes-only PDF as the purchase email), `renewal_payment_link` (absolute `SITE_URL` + `checkout` URL; login required), templates under `subscriptions/templates/subscriptions/email/`. ES strings in `locale/es/LC_MESSAGES/django.po`.
- Auto-renew control: `subscriptions/views.py` `upgrade` :59 + `upgrade.html` (the "Your current level" block): a POST to a new URL calling `service.set_auto_renew` (version-checked, doctor actor); the view holds no rule.
- Tests: `subscriptions/tests/test_purchase.py` (`loaded`, `spanish`, `irish`, `buy`, `pay_on_fake`, `capture`), `payments/tests/test_repeat.py` (`saved_on_redsys`, `fake_charge` for declines), `subscriptions/tests/test_included_cases.py`.

## Tasks & Acceptance

**Execution:**
- [x] `subscriptions/models.py` + migration -- `Renewal`.
- [x] `subscriptions/service.py` -- reminders, renew, drop, `set_auto_renew`, `activate` renewal branch, `_purchase_of`.
- [x] `subscriptions/emails.py`, `tasks.py`, templates -- the three emails.
- [x] command + `deploy/jobs.conf` + `deploy/README.md` -- the daily job.
- [x] `subscriptions/views.py`, `urls.py`, `upgrade.html`, `upgrade_purchase.html` -- the renewal line, Turn off / Turn on, "Renew now" in the last 30 days (the purchase page in renewal mode: same level, upfront, voucher).
- [x] `locale/es/...django.po`, `README.md` -- ES strings; a "Renewals" section.
- [x] `subscriptions/tests/test_renewals.py` -- every matrix row; rerun idempotence; the 8.13 grant of the new term counts and the old one stops.

**Acceptance Criteria:**
- Given dev with Redsys's test terminal, when an annual Advanced doctor's `ends_on` is set to today and the job runs twice, then they are charged once, invoiced, and the record runs to next year.
- Given the full suite, then 0 failures; `makemigrations --check`, `check`, `msgfmt --check` clean.

## Implementation Notes

- `save_card` of the job's renewal order is `monthly_possible(user)` (as `purchase`), not a bare
  `True`: a save-card order is offered only card-saving gateways, so `True` raised
  `NoPaymentMethod` for the IE (Revolut-only) doctor and the "No card" row could not get an order.
- `Renewal.outcome`: `pending`, `paid`, `declined`, `no_card`, `unknown`, `not_sold` (no order,
  `order_id` null). The charge period is the term's `ends_on` (ISO). An abandoned "Renew now"
  order (expired/cancelled) is replaced on the same row by the job's order on `ends_on`; an open
  one is charged.
- A 0.00 renewal order (a voucher covering "Renew now" in full) fires `paid` inside its creation,
  before the `Renewal` row can name it: a `contextvars` marker (`_RENEWING`) lets `activate`
  take the renewal branch then.
- Unknown outcome: no email; `drop_unrenewed` skips the record while the order has a payment
  needing attention (`payments.service.needs_attention`), i.e. staff decide.
- `_purchase_of(record, day)` takes the latest `purchase`/`renewal` change whose `after.started_on`
  is on or before `day`, so a term renewed in advance does not cut off the running term's grant.
- Paid late after the drop: `_renew` restores tier/row/period/active with the `auto_renew` from the
  `renewal_unpaid` change's `before` (False when that drop was not of this term).
- New change reason `auto_renew` for the doctor's Turn off / Turn on. The renewal change's actor is
  the doctor when the order's `created` change was the doctor's ("Renew now"), else the system.
- Structure test: `subscriptions_renewal` added to the scanned tables and `Renewal` as a code-only
  name (strings are not scanned for "renewal", common prose).
- The not-sold email reuses the payment-link template without a link (no fourth template).
- ES strings appended by hand to `django.po` (a full `makemessages` run rewrites unrelated
  entries); `django.mo` recompiled.

## Plan Change Log

## Review Triage Log

## Verification

**Commands:**
- `. .venv/bin/activate && python -m pytest -q -p no:cacheprovider --tb=line` -- expected: 0 failures.
- `python manage.py makemigrations --check && python manage.py check` -- expected: clean.

**Manual checks:**
- dev (Redsys test terminal): reminder, renewal, rerun, auto-renew off, declined card, IE link.
