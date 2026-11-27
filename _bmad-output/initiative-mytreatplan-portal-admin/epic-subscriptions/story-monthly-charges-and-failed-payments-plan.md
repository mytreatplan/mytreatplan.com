---
title: 'Monthly charges and failed payments'
type: 'feature'
ticket: '7'
created: '2026-11-27'
status: 'draft'
baseline_revision: ''
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

**Problem:** A doctor who buys Advanced or Premium monthly (8.5) pays the first month only: nothing charges months 2–12, renews the 12-month commitment, handles a failed charge or ends the term, and the included cases they earn stay at month 1 (8.13 counts confirmed payments of the purchase order, which can be paid only once).

**Approach:** The daily `renew_subscriptions` job (8.6) also charges each monthly record on its monthly due date: one order per month (own invoice), created by the system and charged to the saved card, recorded in a `MonthlyCharge` row per record and due date so a rerun never charges twice. A failed charge sets the record `past_due` and is retried on days 1, 3 and 7 after the due date; still unpaid, the doctor drops to Basic. The first month after the commitment renews it for another 12 months unless auto-renew is off. Monthly included cases are earned from the paid months of the term.

## Boundaries & Constraints

**Always:**
- Only standard (not manual), monthly, paid-tier records with auto-renew or within their commitment; upfront records stay 8.6's.
- Due dates: month k of a term is due `started_on + k months` (relativedelta clamps 29–31); month 1 is the purchase or renewal order itself.
- Idempotent per record and due date: `MonthlyCharge(record, due_on)` unique, holding the current order id and the retry state; each retry uses a fresh order when the previous one is no longer payable; `charge_saved_card` runs outside `transaction.atomic()`.
- Every record in its own transaction; a failure for one record is logged and never stops the run; the record changes only through `subscriptions.service` (`_write`, version-checked).
- `past_due` keeps the level and the included cases (8.13 `counting_purchase`); the drop to Basic ends them.
- A paid month order goes through `activate`'s renewal-like branch (recognised by its `MonthlyCharge` row, never a purchase); the month after `ends_on` paid renews the term (reason `renewal`, new monthly grant).

**Never:** No change to `charge_saved_card`, order expiry or the payments hooks; no cancellation status or downgrade (8.9); no upgrade (8.8); no Revolut saved cards (7.16); no outbox events.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Month charged | monthly Advanced, month 2 due today, Redsys card | one order, one charge, one invoice; included cases earned for 2 months | rerun: nothing more |
| Due on the 31st | started 31 Jan | month 2 due 28/29 Feb | — |
| Declined | month 3 declined | record `past_due`; retries day 1, 3, 7 | — |
| Retry pays | paid on day 3 | record `active`; no more retries | — |
| Grace over | day-7 retry declined | record → Basic (reason `renewal_unpaid`); cases end | — |
| Commitment renews | month 13 due, auto-renew on, paid | term +12 months, new grant, status active | — |
| Auto-renew off | month 13 due | nothing charged; → Basic the day after `ends_on` | — |
| No card | card removed or expired | treated as declined (retries, then Basic) | — |
| Unknown outcome | gateway unknown | no drop while staff decide | Sentry error |
| Upfront / manual | annual or manual record | ignored here | — |

</frozen-after-approval>

## Code Map

- `subscriptions/models.py` -- add `MonthlyCharge` (uuid pk, `record` FK, `due_on`, `order_id` uuid null unique, `outcome` pending/paid/declined/unknown, `attempts`, `next_try_on` null, timestamps; unique (record, due_on)). Migration `0007_monthly_charge`. `Renewal` (:168-202) unchanged.
- `subscriptions/service.py` -- 8.6 machinery to reuse/extend: `_prepare` (:1195-1243, lock order user → record, open-order handling), `_charge` (:1246-1271, outcome mapping), `_bind`/`_set_outcome`, `_sold_row`, `_card_last4`, `_warn`, drop write (:1326-1329, reason `renewal_unpaid`), `drop_unrenewed` (:1296-1334: extend to monthly records whose term ended without a paid next month), `_renew` (:1087/:1099 upfront-only: add the monthly renewal), `activate` (:682-742) renewal branch, `term_of` (:659-662), `_counts` (:257-264: `past_due` counts), `counting_purchase` (:274). New: `charge_months_due(day)` (charges + retries), `_month_due(record, day)`, status writes to `past_due`/`active`.
- `payments/service.py` `create_subscription_order` :441 (system actor, `period=monthly`, `save_card=True`), `months_paid` :669-674; `payments/repeat.py` `charge_saved_card` :232 (per (order, period); a paid order is `NOT_PAYABLE`, so one order per month), outcomes :45; `invoices` one per order on `paid`.
- `subscriptions/included.py` `earned` :85-92 -- monthly: `count × min(months paid in the term, 12) // 12`, months paid = the grant's order paid (1) + paid `MonthlyCharge` rows of the term (through a service read; `included` must not name the subscription models).
- Job: `subscriptions/management/commands/renew_subscriptions.py` (add the monthly step before the drop), `deploy/jobs.conf:74-78` unchanged schedule.
- Emails: `subscriptions/emails.py` :92-139, `tasks.py` :49-97 (`run_once`, period = due date) -- per Open Questions 2, 4, 5.
- Upgrade screen: `upgrade.html:17` renewal block, `renewal_info` (:964-970 returns None for monthly), `set_auto_renew` (:986-987 refuses monthly) -- per Open Question 3.
- Tests: `subscriptions/tests/test_renewals.py` fixtures, `subscriptions/tests/test_purchase.py` (`spanish`, `buy`, `pay_on_fake`), `payments/tests/test_repeat.py` (`saved_on_redsys`, `fake_charge`), `subscriptions/tests/test_included_cases.py` (monthly earning).

## Tasks & Acceptance

**Execution:**
- [ ] `subscriptions/models.py` + migration -- `MonthlyCharge`.
- [ ] `subscriptions/service.py` -- monthly charges, retries, `past_due`, drop, commitment renewal, `activate` branch.
- [ ] `subscriptions/included.py` (+ a service read) -- monthly earning from paid months of the term.
- [ ] command -- the monthly step in `renew_subscriptions`; `deploy/README.md`, `README.md` "Renewals" section.
- [ ] emails, upgrade screen, ES strings -- per Open Questions.
- [ ] `subscriptions/tests/test_monthly.py` -- every matrix row; rerun idempotence; included cases earned month by month; PostgreSQL-safe (row locks inside transactions).

**Acceptance Criteria:**
- Given dev with Redsys's test terminal, when a monthly Advanced doctor's month 2 is made due today and the job runs twice, then they are charged once and get the month's invoice.
- Given the full suite on PostgreSQL, then 0 failures; `makemigrations --check`, `check`, `msgfmt --check` clean.

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Verification

**Commands:**
- `DATABASE_URL=postgres://... python -m pytest -q -p no:cacheprovider --tb=line` (CI runs PostgreSQL) -- expected: 0 failures.
- `python manage.py makemigrations --check && python manage.py check` -- expected: clean.

**Manual checks:**
- dev (Redsys test terminal): a month charged, a rerun, a declined card through the retries to Basic, a commitment renewal.

## Open Questions

1. **Monthly price.** (a) The catalogue's monthly price on each charge day, as 8.6 prices a renewal (recommended; no payments change). (b) Frozen at the start of the 12-month commitment: needs `payments` to create an order at a given price.
2. **After a failed charge.** (a) An email at each failed attempt, with the order's payment link (open 24 h) and the next retry date; the doctor stays on the level until the day-7 retry fails, then drops that day (recommended). (b) One email at the first failure only, the drop the day after the day-7 retry.
3. **Monthly payer's control.** (a) On the upgrade screen, the next charge date and the card, and "Turn off auto-renew": they keep paying until the commitment ends, then Basic (recommended; it is the epic's "a monthly payer who cancels keeps paying until the commitment ends"). (b) Nothing now: 8.9 adds cancellation.
4. **Before the commitment renews.** (a) The 30-day reminder, as for annual (recommended). (b) No reminder.
5. **Monthly receipts.** (a) Each paid month emails the receipt with its invoice, as 8.6's renewal receipt (recommended). (b) No email; invoices in the doctor area only.
