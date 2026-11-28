---
title: 'Monthly charges and failed payments'
type: 'feature'
ticket: '7'
created: '2026-11-27'
status: 'in-review'
baseline_revision: '499998d749a7bc9da659e5723ede006673369047'
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

## Decisions

- Decision (user, 2026-11-28): each month costs the catalogue's monthly price on its charge day (as 8.6 prices a renewal).
- Decision (user, 2026-11-28): each failed attempt emails the doctor the order's payment link (open 24 h) and the next retry date; the doctor keeps the level until the day-7 retry fails, then drops to Basic that day.
- Decision (user, 2026-11-28): the upgrade screen shows a monthly payer the next charge date and the card, with "Turn off auto-renew": they keep paying until the commitment ends, then Basic.
- Decision (user, 2026-11-28): the 30-day reminder before the commitment renews, as for annual.
- Decision (user, 2026-11-28): each paid month emails the receipt with its invoice, as 8.6's renewal receipt.
- Decision (user, 2026-11-28): one plan, not split (about 2,300 tokens).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Month charged | monthly Advanced, month 2 due today, Redsys card | one order, one charge, one invoice; receipt email; included cases earned for 2 months | rerun: nothing more |
| Reminder | 30 days before month 13, auto-renew on | the 30-day reminder (annual's) | once |
| Due on the 31st | started 31 Jan | month 2 due 28/29 Feb | — |
| Declined | month 3 declined | record `past_due`; email with link and next retry date at each failed attempt; retries day 1, 3, 7 | — |
| Retry pays | paid on day 3 | record `active`; no more retries | — |
| Grace over | day-7 retry declined | record → Basic that day (reason `renewal_unpaid`); cases end | — |
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
- Emails: `subscriptions/emails.py` :92-139, `tasks.py` :49-97 (`run_once`, period = due date + attempt): a failed-attempt email (link + next retry date), the monthly receipt (8.6 receipt pattern), the 30-day reminder before month 13 (8.6 `due_reminders` extended to monthly).
- Upgrade screen: `upgrade.html:17` renewal block, `renewal_info` (:964-970 returns None for monthly: extend with the next charge date), `set_auto_renew` (:986-987 refuses monthly: allow; off = no charge after `ends_on`). No "Renew now" for monthly.
- Tests: `subscriptions/tests/test_renewals.py` fixtures, `subscriptions/tests/test_purchase.py` (`spanish`, `buy`, `pay_on_fake`), `payments/tests/test_repeat.py` (`saved_on_redsys`, `fake_charge`), `subscriptions/tests/test_included_cases.py` (monthly earning).

## Tasks & Acceptance

**Execution:**
- [x] `subscriptions/models.py` + migration -- `MonthlyCharge`.
- [x] `subscriptions/service.py` -- monthly charges, retries, `past_due`, drop, commitment renewal, `activate` branch.
- [x] `subscriptions/included.py` (+ a service read) -- monthly earning from paid months of the term.
- [x] command -- the monthly step in `renew_subscriptions`; `deploy/README.md`, `README.md` "Renewals" section.
- [x] `subscriptions/emails.py`, `tasks.py`, templates, `upgrade.html`, `locale/es/...django.po` -- failed-attempt email, monthly receipt, reminder before month 13, the monthly block (next charge, card, auto-renew).
- [x] `subscriptions/tests/test_monthly.py` -- every matrix row; rerun idempotence; included cases earned month by month; PostgreSQL-safe (row locks inside transactions).

**Acceptance Criteria:**
- Given dev with Redsys's test terminal, when a monthly Advanced doctor's month 2 is made due today and the job runs twice, then they are charged once and get the month's invoice.
- Given the full suite on PostgreSQL, then 0 failures; `makemigrations --check`, `check`, `msgfmt --check` clean.

## Implementation Notes

- Due dates: month n+1 of a term is due `started_on + n months` (n = 1..11, while within the
  term; relativedelta from `started_on`, so a 31st start never drifts); the renewal month is
  `ends_on + 1 day` (`_due_dates`), charged only with auto-renew on. The plan's "month k is due
  `started_on + k months`" is read with the matrix (month 2 due `started_on + 1 month`).
- `MonthlyCharge.outcome`: `pending`, `paid`, `declined`, `unknown` (as planned); no card, a
  refused charge and an order that could not be made (row no longer sold, no payment method:
  `order_id` null, a Sentry warning, an email saying to contact us) all count as `declined`.
  Charge period: the due date (ISO). One month per record per run (`_month_due` takes the first
  unpaid month of the term; a later month waits while an earlier one is retried).
- Retries: after a decline on `day`, `next_try_on` is the first of due + 1, 3, 7 days still after
  `day`; none left drops the record (`_drop`, reason `renewal_unpaid`) that day. A run that
  missed days therefore catches up on the schedule, never past day 7 + the first run after it.
  The same order is charged again while payable; an expired/cancelled one is replaced
  (`_bind_month` refuses to move off an open order).
- New change reasons: `month_unpaid` (active -> `past_due`, system) and `month_paid` (back to
  `active`, the change naming the order). A paid month of an `active` record writes no change
  (the `MonthlyCharge` row is the record of it; a repeated `paid` is a no-op through it).
  Writing `past_due` when the held row left the catalogue (`_check` refuses) is a Sentry
  warning; the attempt is still recorded.
- Lock order: doctor -> record -> charge (`_prepare_month`), record -> charge (`_after_charge`,
  `_month_paid` inside the `paid` hook). A `_CHARGING_MONTH` contextvar covers a 0.00 month
  order paid inside its creation (as 8.6's `_RENEWING`).
- Late payment after the drop: `_month_paid` restores the dropped monthly term from the
  `renewal_unpaid` change's `before` (its last change must be that drop), or renews it when the
  month was the renewal month.
- `drop_unrenewed` also drops a monthly record the day after `ends_on` when auto-renew is off or
  the renewal month has no charge (row no longer sold, skipped); never while a charge of the
  term needs staff attention or its renewal month is being retried.
- Included cases: monthly months paid = `payments.months_paid(source order)` (its confirmed
  payments: 1 for a paid order, keeping 8.13's test of repeat payments on one order) +
  `service.paid_months(doctor_uid, starts_on, ends_on)` (paid rows due after `starts_on`, up to
  `ends_on`), capped at 12. The renewal month's grant is accrual `monthly`.
- Emails: the reminder template takes `monthly` and `renews_on` (annual: `ends_on`, unchanged
  output); new `month_payment_failed` (link + next retry + last retry; dropped variant) and
  `month_receipt` (renewed variant) templates; tasks `send_month_failed(charge_id, attempt)`
  (`run_once` period `<due>#<attempt>`, only for the latest attempt) and
  `send_month_receipt(order_id)`. ES strings appended by hand to `django.po`.
- `RenewalInfo` gains `monthly`, `next_charge_on`, `past_due`, `renews_on`; `set_auto_renew`
  now accepts a running monthly record. `upgrade_purchase` for the held level redirects to the
  screen (no "Renew now": `can_renew_now` False).
- 8.6's `test_manual_and_monthly_records_are_ignored` became `test_manual_records_are_ignored`
  (monthly records now take part); the structure test scans `MonthlyCharge` and its table.
- Command output gains `months: <outcome> <count>` (`paid`, `declined`, `dropped`, `unknown`,
  `pending`). `deploy/jobs.conf` unchanged.

## Plan Change Log

## Review Triage Log

Quick lens, pass 1 (2026-11-28): high 0, medium 3, low 0, false 0, maybe-false 0.

| # | Lens | Finding | Verdict | Route | Evidence |
|---|------|---------|---------|-------|----------|
| 1 | quick | A monthly record whose held row is no longer sold mid-term: the screen says the level ends on `ends_on` and mentions a link, the dropped email says the card could not be charged, but each month is treated as failed (past_due, retries, drop ~day 7) with no link | medium | patch | `_prepare_month` raises `OrderRefused` for no `_sold_row`; `_charge_month` counts it declined; `upgrade.html` not-sold branch and `month_payment_failed` texts disagree. Patch: texts say what happens. The behaviour (drop after grace vs keep the level to `ends_on`) is not settled by the frozen intent: put to the user. |
| 2 | quick | During the renewal month's retries the upgrade screen's monthly block disappears and auto-renew cannot be turned off | medium | patch | `renewal_info` returns None for `day > ends_on`; the renewal month is due `ends_on + 1` and retried to `ends_on + 8` while past_due counts. Decision 3 asks for the next charge date and the button. |
| 3 | quick | A renewal month skipped or failing before its charge row exists is dropped to Basic by the same run, with no attempt, email or grace | medium | patch | `_prepare_month` returns/raises before a `MonthlyCharge` row in several paths; `_drop_monthly` drops when `renewing is None` regardless of auto-renew. Matrix: a failed renewal is retried, then Basic. |

## Verification

**Commands:**
- `DATABASE_URL=postgres://... python -m pytest -q -p no:cacheprovider --tb=line` (CI runs PostgreSQL) -- expected: 0 failures.
- `python manage.py makemigrations --check && python manage.py check` -- expected: clean.

**Manual checks:**
- dev (Redsys test terminal): a month charged, a rerun, a declined card through the retries to Basic, a commitment renewal.
