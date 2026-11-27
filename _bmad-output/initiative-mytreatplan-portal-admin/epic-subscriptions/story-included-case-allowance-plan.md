---
title: 'Included-case allowance'
type: 'feature'
ticket: '13'
created: '2026-11-27'
status: 'in-review'
baseline_revision: 'dcf2a3eeab8d08d96a8f318537d291f677d110cb'
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

**Problem:** Paid subscriptions carry an `included_cases` number in the catalogue (5.17), but nothing gives a doctor their cases, counts them or spends them: `OrderLine.included_case` exists with no hook behind it and the home page shows nothing.

**Approach:** A per-doctor ledger in `subscriptions`: a `CaseGrant` per source (now: one subscription term, frozen from the catalogue at purchase) and a `CaseUse` per order line, reserved on `created`, consumed on `paid`, released on `cancelled`/`expired` (and a consumed use on `refunded` while its grant's term runs), exactly as `vouchers.py` does for redemptions. One service function returns what is left; the doctor's home shows it. Grants for subscriptions activated before this ships are created by a backfill command.

## Boundaries & Constraints

**Always:**
- Grant frozen at activation: `count` = `resolver.included_cases(subscription, 'tps_branded')` read once, `accrual` upfront or monthly from the record's `billing_period`, `starts_on`/`ends_on` = the term; source `subscription_term` + the order id (unique per source, so a repeat `paid` creates nothing). A catalogue edit never touches an existing grant.
- Available on a day = earned − reserved − consumed, where earned = `count` (upfront) or `count × months_paid ÷ 12` (monthly, rounded down); only grants whose term contains the day and whose doctor's record still counts as that paid tier.
- A use points at one grant, the soonest-ending grant with something available first; reserve, consume and release lock the doctor's grants (`select_for_update`) and are idempotent per order line; a `created` with not enough available raises (the order is not created).
- Models read only through `subscriptions.service` / the new module (structure test); no business rule in views.

**Never:** No outbox events for grants or uses (no mytp-core release), no cart tick or wizard change (9.3/9.6), no invoice or ICE change, no renewal/upgrade grants (8.6/8.8 call the grant function later), no manual-subscription allowance (8.11), no admin client record, no packages, no change to pricing or to `payments` hooks module.

## Decisions

- Decision (user, 2026-11-27): a monthly payer's earned count is rounded down (`count × months_paid // 12`): a case is earned only when fully paid for.
- Decision (user, 2026-11-27): a `refunded` order releases its consumed uses while the grant's term still runs (after `ends_on`, nothing comes back).
- Decision (user, 2026-11-27): no outbox events for included cases in this story; sales-finance reads usage later.
- Decision (user, 2026-11-27): one plan, not split (about 2,000 tokens).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Upfront Advanced | catalogue 12, paid | grant 12; home "12 of 12 included cases left" | — |
| Monthly Premium | catalogue 25, 3 months paid | earned 6 (25 × 3 // 12) | — |
| Reserve / consume | included line, order created then paid | available −1 at created, stays −1 at paid | — |
| Release | order cancelled or expired | use released, available +1 | consumed never released |
| Refund in term | paid order with a consumed use refunded, term running | use released, available +1 | after the term: unchanged |
| None left | included line, available 0 | order not created | hook raises → `HookFailed` |
| Repeat hook | `created`/`paid`/`paid` again | nothing changes | idempotent |
| Catalogue edit | 12 → 20 mid-term | grant still 12 | — |
| Basic / expired / dropped | no grant, or record no longer a counting paid tier | home shows nothing; available 0 | — |
| Backfill | paid record without a grant | command creates it; re-run creates none | `--dry-run` counts only |

</frozen-after-approval>

## Code Map

- `subscriptions/models.py` -- add `CaseGrant` (uuid pk, `doctor_uid`, `source` choices `subscription_term`, `source_id` uuid unique with source, `category`, `count`, `accrual`, `starts_on`, `ends_on`, `created_at`) and `CaseUse` (uuid pk, `grant` FK, `doctor_uid`, `order_id`, `line_id` unique, `state` reserved/consumed/released, `version`, timestamps). Plain UUIDs, no FKs to doctor/order (as `VoucherRedemption` :168-224). Migration `0005_included_cases`.
- `subscriptions/vouchers.py` :87-160 -- pattern for `_locked`, `reserve`, `consume` (reserve inside nested atomic when missing), `release` (reserved only). New module `subscriptions/included.py` mirrors it per order line where `line.included_case`; plus `grant_term(...)` (explicit values, idempotent) and `available(user, day)`.
- `subscriptions/service.py` -- `activate` :603-645: after a successful `_write`, call `included.grant_term` with the record's values (inside the same transaction). `_counts` :208 and `effective` :218 decide "still counts". Add `included_left(user, day=None)` → `(left, total)` or None, read by the home tag. `record_of` :191.
- `subscriptions/apps.py` :20-33 -- register `included_reserve` (created), `included_consume` (paid), `included_release` (cancelled, expired), `included_refund` (refunded), beside the voucher receivers.
- `payments/service.py` -- add `months_paid(order_id)`: confirmed `Payment` rows of the order (first payment + `period` charges, `payments/models.py:272,285`), so `subscriptions` does not import `payments.models`.
- `catalogue/resolver.py:376` `included_cases`, `catalogue/models.py:117` `Product.TPS_BRANDED`.
- `accounts/templates/accounts/home.html:67` + new tag `subscriptions/templatetags/included_cases.py` modelled on `upgrade_reminder.py:27-55` (never raises, request-cached).
- `subscriptions/management/commands/grant_included_cases.py` -- backfill modelled on `assign_basic.py` (`--dry-run`, one transaction per doctor, counts only): paid, counting records whose purchase `SubscriptionChange.order_id` has no grant.
- `subscriptions/tests/test_structure.py:107-163` -- add `CaseGrant`/`CaseUse` to the names only the service/module may use if the rule lists them.
- Tests: `subscriptions/tests/test_purchase.py` fixtures (`loaded` sets Advanced `included_cases`, `pay_on_fake` :106, `capture`), `subscriptions/tests/test_vouchers.py` as the hook-test pattern.

## Tasks & Acceptance

**Execution:**
- [x] `subscriptions/models.py` + migration -- the two models and constraints.
- [x] `subscriptions/included.py` -- grant, available, reserve/consume/release.
- [x] `subscriptions/service.py`, `subscriptions/apps.py` -- grant on activate, `included_left`, receivers.
- [x] `payments/service.py` -- `months_paid`.
- [x] tag + `home.html` -- "N of M included cases left this term" (branded TPS), ES string in `locale/es/LC_MESSAGES/django.po`.
- [x] backfill command; `README.md` and `deploy/README.md` paragraph (run it once after deploy).
- [x] `subscriptions/tests/test_included_cases.py` -- every matrix row, plus concurrency guard (unique `line_id`) and the soonest-ending grant first.

**Acceptance Criteria:**
- Given dev, when a doctor buys Advanced upfront, then the home shows the frozen number left.
- Given the full suite, when it runs, then 0 failures; `makemigrations --check`, `check`, `msgfmt --check` clean.

## Implementation Notes

- "N of M left": M is what the grants have earned so far (upfront: the count; monthly: `count × months_paid // 12`), N = M − reserved − consumed.
- "Still counts as that paid tier" = `service.counting_purchase`: the record counts on the day (`_counts`) and is a standard (non-manual) Advanced or Premium; only the grant of its latest `purchase` change's order counts. `CaseGrant` stores no tier (field list kept as in the Code Map).
- A use takes a grant of the line product's category (`tps_branded` today); an included line without a catalogue product, or of another category, is refused at `created`.
- One use per order line, whatever the line's quantity.
- `activate` grants inside a savepoint; a fault there is logged and reported to Sentry, never undoing the payment (the backfill can grant it later).
- Structure test: `CaseGrant`/`CaseUse` may be named only by `subscriptions/models.py` and `subscriptions/included.py`; the service, tag and command go through `included`.
- `consume` for a line without a use reserves it first; when nothing is available it logs an error and the payment stands (as `vouchers.consume`).

## Plan Change Log

## Review Triage Log

Quick lens, pass 1 (2026-11-27): high 0, medium 1, low 2, false 1, maybe-false 0.

| # | Lens | Finding | Verdict | Route | Evidence |
|---|------|---------|---------|-------|----------|
| 1 | quick | A grant counts whenever its dates contain the day and the record is on any standard paid tier: after the record goes back to Basic (`reset_to_basic` on staging; a drop during grace, 8.7) and the doctor buys again, the old grant counts too and is used first | medium | patch | `_eligible` filters by dates and `counts_as_paid` only; `activate` accepts a new purchase on a Basic record and creates a second grant. Smallest fix: only the grant of the record's latest `purchase` order counts (no new public surface beyond a service read). |
| 2 | quick | `records_missing_a_grant` (dry run) skips neither non-client accounts nor held rows missing from the catalogue, which `grant_missing_term` refuses: the dry run never reaches 0 | low | patch | The two functions apply different checks; fix is one shared predicate. Staging staff accounts can hold paid records, so the deploy check would meet it. |
| 3 | quick | `included.py` docstring, README and the service docstring disagree with the structure test about who names/writes `CaseGrant`/`CaseUse` | low | patch | Test allows only `models.py` and `included.py`; the texts name the service too, or call the service the only writer. Direct correction. |
| 4 | quick | Home line does not say "TPS branded" | false | - | The frozen matrix fixes the text: "12 of 12 included cases left"; the task's "(branded TPS)" names which cases are counted, not the wording. |

## Verification

**Commands:**
- `. .venv/bin/activate && python -m pytest -q -p no:cacheprovider --tb=line` -- expected: 0 failures.
- `python manage.py makemigrations --check && python manage.py check` -- expected: clean.

**Manual checks:**
- dev: buy Advanced upfront, run the backfill dry run, see the home count.
