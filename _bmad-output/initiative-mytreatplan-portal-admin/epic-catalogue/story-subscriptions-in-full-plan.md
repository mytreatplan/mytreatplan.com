---
title: 'Subscriptions in full'
type: 'feature'
ticket: '8'
created: '2026-11-11'
status: 'done'
baseline_revision: 'f2cb66a3ebec3da5bfd4af183c160c58e834251e'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** C7 (AD-6, AD-16): a subscription is a tier in a country group with two TPS discounts, and every active product is offered to every subscription. The company needs subscriptions with a code and public texts, standard and manual ones, the products each gives access to, a discount per product category, call extras, their own price per period, and one default per tier and country group.

**Approach:** On the admin site, complete `Subscription` and publish it in the catalogue section (mytp-core 0.22.0, new parts optional); the portal stores it and the resolver offers a doctor only the products of their subscription, at that subscription's discount for the product's category.

## Boundaries & Constraints

**Always:**
- The resolver stays the only code that prices (AD-16); line order as decided (net price → subscription discount → …), rounded once.
- Contract compatibility as in entries 5 and 6: new fields and parts optional, absent ones not dumped; a portal on mytp-core 0.21 keeps applying snapshots.
- Never hard-deleted: deactivated only.
- Internal notes never leave the admin site.
- Every change audited and rebuilding the snapshot.

**Never:** no assignment of a subscription to a doctor, no payment, renewal or commitment logic (epic-subscriptions); no appointment counting (epic-appointments reads the extras); no billing rules (entry 9); no vouchers (entry 10).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Two defaults | an active standard subscription for Advanced · Iberia; a second standard one for them | refused | a manual one for the same tier and group is accepted |
| Linked products | Advanced Iberia linked to a VirtuaOrtho product; Advanced rest of EU not | an ES doctor on Advanced is offered it; a DE doctor is not | a product linked to no subscription is offered to nobody |
| Existing data | the products and subscriptions of before this release | every existing product linked to every existing subscription: the offers are unchanged | — |
| Discount per category | Premium with 12 % on `tps_branded`, 0 on `virtuaortho_one` | a 100.00 TPS branded line is 88.00; a VirtuaOrtho One line is at its net price | a discount outside 0–100 or for an unknown category refused |
| The two former fields | a subscription saved before this release (10 % branded, 0 % in-house) | its `tps_branded` and `tps_in_house` discounts are 10 and 0; a 0.21 portal still reads both | — |
| Manual subscription | a manual "Ormco Premium" in Premium · Iberia | not returned for (ES, premium); read by its id, priced with its own products and discounts | — |
| Own price | Advanced: EUR, 60.00 a month, 600.00 upfront; Basic: none | stored and readable on the portal; Basic reads as free | an amount without a currency, or a negative one, refused |
| Call extras | Premium: +1 call and 35 minutes on the VirtuaOrtho categories | readable on the portal: a VirtuaOrtho product with 3 calls of 25 minutes gives 4 calls of 35 under Premium; other categories unchanged | extra calls below 0 or minutes below 1 refused |
| Code and texts | code `adv-ib`, English name, no Spanish | saved as `ADV-IB`; Spanish reads fall back to English | a taken code or a missing English name refused; an existing subscription without a code keeps working until its next edit |
| No standard | the standard subscription of a tier and group deactivated | allowed; the page says the tier and group have no default; no offers for them | — |
| Notes | internal notes on a subscription | kept and audited on the admin site | never in the snapshot |
| Old snapshot | an admin before this release | dev keeps working: every product offered under each subscription, as before | — |
| Old portal | a portal on mytp-core 0.21 and a snapshot with manual subscriptions | it applies the snapshot and ignores the new parts | — |

## Decisions

- Decision (user, 2026-11-11): a subscription is either standard or manual. The standard one of a tier and country group is its default: at most one active per tier and group. Manual ones are any number per tier and group, hidden from doctors who do not have them, and travel in a new optional list of the snapshot: the reader rule of today is untouched and the snapshot format is not bumped.
- Decision (user, 2026-11-11): a subscription lists the products it gives access to, edited on the subscription's page; a product linked to no subscription is offered to nobody; the migration links every existing product to every existing subscription.
- Decision (user, 2026-11-11): each subscription has a discount (0 to 100, default 0) per product category; the two existing TPS discounts become the `tps_branded` and `tps_in_house` ones; VirtuaOrtho is 0 unless set.
- Decision (user, 2026-11-11): a subscription's own price is in one currency, with a monthly amount and an upfront yearly amount, each optional; none means free. Another currency is another subscription.
- Decision (user, 2026-11-11): call extras are per product category: extra calls and a call length that replaces the product's. The longer length for several cases stays with epic-appointments.
- Decision (user, 2026-11-11): Premium's "two one-to-one sessions a year" is not in this entry; it is raised with epic-appointments.
- Decision (user, 2026-11-11): a manual subscription has a tier and a country group like any other; the resolver prices by subscription; asking by country and tier returns the standard one; a manual one is read by its id. Who has it is epic-subscriptions.
- Decision (user, 2026-11-11): code and texts as products: a unique uppercase code (required from now on; existing subscriptions get one at their next edit), a public name and short description in English and Spanish (English required), internal notes kept on the admin site.
- Decision (user, 2026-11-11): a tier and group may have no standard subscription; deactivating the standard one is allowed and the page says so.
- Decision (user, 2026-11-11): one plan, kept whole; mytp-core 0.22.0 → admin → portal.

</frozen-after-approval>

## Code Map

- Repositories: this plan is in the portal (`mytreatplan.com`); mytp-core is `../mytp-core` (baseline 8ec0e350a574b4afdcdfaef33e582c7ed34800ec) and the admin site `../mytp-admin` (baseline 4bad4bcdf6a1a6408cc9c6f817002317e66d66d8). The branch `story/5.8-subscriptions` is checked out in all three: work there, commit nothing, push nothing, tag nothing. Both sites vendor a provisional 0.22.0 wheel built from the core branch, the way 0.21.0 is vendored (the release rebuilds it from the tag). Run each repository's full suite as its README says (the portal's venv is `../.venv`; the admin site's test venv is `/private/tmp/claude-501/admin-venv`). The line numbers below predate small edits on master; search by name when one is off.

- Requirements: `MyTP_Reqs_Def_V1_21Oct2026_v3.docx` ¶154-156 (Basic free; Advanced 720 yearly, 60 a month or 600 upfront, 6 % TPS; Premium 1440, 120 or 1200, 12 %), ¶171-177 (regional grid: Advanced Iberia includes VirtuaOrtho, rest of EU does not; manual subscriptions assigned by an administrator), ¶238-240 (calls; Premium's extras), ¶360-370 (products shown only when linked to a subscription; the default per tier and country group; one default). Epic Notes :60-61, :66; Done when :40-41.
- Admin: `catalogue/models.py` `Subscription` :347-395 (constraint `catalogue_one_active_subscription_per_tier_group` :375; `audit_repr` "Tier · Group" :390, no longer unique); `service.py` `SUBSCRIPTION_FIELDS` :71, `DECIMAL_FIELDS` :90, `_save` :183-205, subscription functions :583-605; `views.py` `SubscriptionForm` :551-588, `SubscriptionTable` :221, `_subscription_details` :696, Kind :711; `snapshot.py:83-89`; `audit/registry.py:46-49`. Reuse from entry 5: `check_texts` (`models.py:94`), `normal_texts` (`service.py:279`), `normal_code` :299, `TextsForm` (`views.py:347-390`), `_text_details` :658.
- mytp-core: `contracts/catalogue.py` `CatalogueSubscription` :672-693, `_consistent` :722-753 (the one-active-per-tier-and-group rule :745-752; relaxing it needs a `min_reader` bump, docstring :128-133), `_without_absent` :228, `add_ons` :707-713 (the pattern for a new optional list); `contracts/config.py:49-51`.
- Portal: `catalogue/models.py` `Subscription` :178-198; `applier.py:85-91`; `resolver.py` `subscription` :106, `discount_percent` :115, `price` :152, `offers` :208 (every active product today); `price_catalogue.py:90,156`; tests that pin today's behaviour: `catalogue/tests/test_catalogue.py:83,136,175-193,461`, `test_add_ons_costs.py:116,232,328`.
- The snapshot re-apply fix (plan-reapply-snapshot-after-deploy.md) means the portal needs no migration that clears the applied version.

## Tasks & Acceptance

**Execution:**
- [x] mytp-core 0.22.0 (`mytp_core/contracts/catalogue.py`, `schemas/config_snapshot.json`, version, README, tests): optional subscription fields (code, texts, `products`: the linked product ids, `discounts`: a category → percent map, `price`: currency with monthly and upfront amounts, `call_extras`: category → extra calls and minutes) and the section's optional `manual_subscriptions` list (the same shape); the two TPS discount fields stay and equal the map's `tps_branded` and `tps_in_house` values; absent parts not dumped; the one-active-per-tier-and-group rule unchanged for `subscriptions` and not applied to the manual list; helpers: a subscription's discount for a category (the map, else the two former fields, else 0), whether it offers a product (its list, else every product when the list is absent: an older admin site), the calls of a product under it.
- [x] Admin (`catalogue/models.py`, `service.py`, `views.py`, `snapshot.py`, `audit/registry.py`, templates, a migration, README, tests): `Subscription` gains kind (standard or manual), code, texts, notes, the linked products, the discounts per category, the price (currency, monthly, upfront), the call extras per category; the partial unique rule applies to active standard ones only; the migration makes the existing ones standard, links every existing product to each and turns the two TPS fields into category discounts; form, table, details (the "no default" notice), `audit_repr` with the code or name; snapshot entries (standard in `subscriptions`, manual in `manual_subscriptions`, no notes).
- [x] Portal (`catalogue/models.py`, `applier.py`, `resolver.py`, `management/commands/price_catalogue.py`, a migration, `deploy/README.md`, tests): store the new fields and the manual subscriptions; resolver: `subscription(country, tier)` returns the standard one, `subscription_by_id`, `offers` only the subscription's linked products (every product when the snapshot has no links), `discount_percent` by category, the calls of a product under a subscription; `price_catalogue` takes a subscription id or code; no migration clears the applied version (the re-apply fix does it).
- [x] Both sites: vendor the new core. — Portal done. Admin: `requirements.txt`, README and the tests point at 0.22.0, but the wheel file is not in `../mytp-admin/vendor/` yet (see Implementation Notes).
- [x] Tests for every row of the matrix; the tests that pin "every active product is offered" updated.

**Acceptance Criteria:**
- Given an active standard subscription for a tier and country group, when a second active standard one is saved for them, then it is refused.
- Given Advanced Iberia linked to a VirtuaOrtho product and Advanced rest of EU not, when dev prices ES and DE doctors on Advanced, then only the ES doctor is offered it.
- Given a manual "Ormco Premium", when a doctor without it is priced, then it is not among what the resolver returns for their country and tier.
- Given Premium's call extras, when dev applies the snapshot, then they are readable there.

## Implementation Notes

Choices the plan left open (2026-11-10, implementer):

- Contract shape (mytp-core 0.22.0): `code`, `texts`, `products` (ids), `discounts` (`{category: "12.00"}`), `price` (`{"currency", "monthly", "upfront"}`, amounts optional), `call_extras` (`{category: {"extra_calls": n, "minutes": m}}`, `minutes` optional: absent keeps the product's length). Helpers: `subscription_discount`, `subscription_offers`, `calls_under` and the `CatalogueSubscription` methods `discount_for`, `offers`, `calls_for`; `CatalogueSection.subscription(id)` reads both lists. New cross-row rules, on the new parts only: an id is in one of the two lists; a code is unique among the subscriptions that have one; a manual subscription's group is listed; the two TPS fields equal the map's values.
- A product that grants no calls gets none under a subscription: the extras add to a product's calls.
- Existing subscriptions get their tier's label ("Basic", "Advanced", "Premium") as their English public name in admin migration 0006 (they had no name; the English name is required); no code until the next edit.
- The admin model drops the two TPS columns (now properties over `discounts`); a discount of 0 is left out of the stored map. Linked products are a many-to-many, audited as the sorted ids.
- A subscription is named `<code or name> · <Tier> · <Group>` (`audit_repr`).
- Portal: a subscription whose own price is in a currency `Money` does not take is skipped (never stored as free). `resolver.price` refuses a product the subscription does not give access to (`ProductRefused`). `price_catalogue` takes `--subscription <id or code>` and now prints a `subscription:` line (code, name, own price) and each offer's calls under the subscription.
- `audit/registry.py` needs no change: no new change view.



Until then the admin suite has one failure (`layout/tests/test_deploy.py::test_requirements_ship_the_worker_and_the_vendored_mytp_core`), and both local venvs still hold mytp-core 0.21.0 (the suites were run with `PYTHONPATH=../mytp-core`).
- Released as mytp-core v0.22.0 (997ec60); both sites vendor the wheel built from the tag, and the suites were rerun against it installed: core 1453, admin 1384, portal 2511 (+ the 3 known PostgreSQL-only failures), on SQLite.
- After review: an amount of 0 is saved as none (a subscription with only zero amounts is free and sends no price); a price with neither amount is refused by the contract; `calls_under` and `subscription_discount` validate their inputs; inactive subscriptions and products are marked on the admin pages, and a product whose linked subscriptions are all inactive reads "offered to nobody".
- For epic-appointments: Premium's "two one-to-one sessions a year" (requirements ¶156) is not in this entry (user decision 2026-11-11).

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, one reviewer per repository, 2026-11-11): 11 findings — 11 low (4 of them rejected), 0 false. No unmet acceptance criterion and no bug in the portal code.

| # | Where | Finding | Verdict | Route | Evidence / action |
|---|-------|---------|---------|-------|-------------------|
| 1 | core `calls_under` | returns before its checks; unvalidated count and minutes; unbounded sum | low | patch | every argument validated first; a sum above `MAX_QUANTITY` refused |
| 2 | core `subscription_discount` | validates nothing; a former field given as None raises | low | patch | a discount outside 0–100 or not a Decimal refused; None reads as 0 |
| 3 | core discounts / call extras maps | a legacy `tps` key validates but is never read | low | rejected | no writer emits the pre-0.20.0 code in a map (the admin site writes its fixed categories) |
| 4 | core `SubscriptionPrice` | a currency with no amount validates, and would be skipped instead of read as free | low | patch | a price with neither amount refused; free is `price` absent |
| 5 | core `CatalogueSection.subscription`, `subscription_offers` | ids compared as raw text: an uppercase id misses | low | rejected | the portal passes UUIDs (`resolver.subscription_by_id` normalises); the contract's ids are canonical |
| 6 | admin `_subscription_values` | a product id given as text is refused | low | patch | normalised to UUID; a malformed one is the `products` error |
| 7 | admin `_product_details`, `_subscription_details` | inactive subscriptions and products listed unmarked; a product linked only to inactive subscriptions reads as offered | low | patch | marked; "offered to nobody" when no linked subscription is active |
| 8 | admin `test_subscriptions.py` | the "0.21 portal reads both" check is vacuous | low | patch | the test asserts what it can prove (both former fields equal the map's values); the portal's suite holds the old-snapshot row |
| 9 | admin `Subscription.free`, form | an amount of 0 counts as a price | low | patch | 0 is saved as none: free sends no price |
| 10 | admin `vendor/` | the 0.22.0 wheel is not in the admin site's `vendor/` | low | rejected | not a defect of the change: the release rebuilds the wheel from the tag in both sites (`scripts/vendor-mytp-core.sh v0.22.0`) |
| 11 | portal local venv | the suite ran with `PYTHONPATH=../mytp-core`, the venv still on 0.21.0 | low | rejected | same cause; after the release the tag's wheel is installed in both venvs and the suites rerun without the override |

## Verification

**Commands:**
- mytp-core, admin and portal suites — all pass; CI on PostgreSQL.

**Manual checks (staging):**
- On admindev: a second standard Advanced · Iberia refused; Advanced Iberia linked to a VirtuaOrtho product, Advanced rest of EU not; a manual "Ormco Premium"; Premium with 12 % TPS branded, +1 call and 35 minutes on VirtuaOrtho, 120 a month or 1200 upfront. On dev: after the deploy the first pull prints `applied`; `price_catalogue` for ES and DE on Advanced shows VirtuaOrtho only for ES; the manual one is not listed for (ES, premium) and is priced by its id; the call extras and the price are shown.

## Staging

Done 2026-11-11 (the user delegated the merges and the staging checks). mytp-core v0.22.0 (997ec60, CI green on master and the tag); admin PR #32 (run 42910857612: test and deploy green), portal PR #90 (run 42911824164: test, dev and dev-ae deploys green). CI on PostgreSQL: all pass.

- dev after its deploy, before any admin write: the first pull printed `v56 applied` (the snapshot of before this release, applied again by the new release) and ES basic kept its 18 offers: a snapshot without links offers every product.
- admindev: migration 0006 made the existing Basic · Iberia standard, named "Basic", linked to all 20 products, with `tps_branded` 10.00. Through the service, as the command actor `staging_check_5_8`: STG58-ADV-IB (standard, Advanced · Iberia, TPS branded and VirtuaOrtho One linked, 6 %, 60 a month or 600 upfront); a second standard Advanced · Iberia refused; STG58-ADV-REU (Advanced · Rest of EU, TPS branded only); STG58-PREM-IB (Premium · Iberia, 12 %, +1 call and 35 minutes on `virtuaortho_one`, 120 or 1200, with internal notes); the manual STG58-ORMCO-PREM (Premium · Iberia, 20 %); STG58-BASIC-ZERO with a monthly amount of 0: saved free, without a currency.
- dev and dev-ae with the new snapshot, `price_catalogue`: ES Advanced offers TPS branded at 94.00 and VirtuaOrtho One at 100.00 (3 calls of 25 minutes); DE Advanced offers TPS branded only, and pricing the VirtuaOrtho product under it is refused; ES Premium is the standard one (TPS branded 88.00, VirtuaOrtho One with 4 calls of 35 minutes, its price shown), never the manual one; the manual one is read by its code and by its id (TPS branded 80.00, Spanish texts with `--language es`); the zero-amount Basic reads "free".
- Not checked: the admin pages in a browser (the long subscription form, the "no default" notice and the inactive marks rest on the suite). The staging rows stay on admindev as staging data.
- For epic-appointments: Premium's "two one-to-one sessions a year" (requirements ¶156) is not modelled (user decision 2026-11-11).
