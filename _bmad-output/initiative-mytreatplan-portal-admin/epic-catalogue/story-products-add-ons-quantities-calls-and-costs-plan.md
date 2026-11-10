---
title: 'Products: add-ons, quantities, calls and costs'
type: 'feature'
ticket: '6'
created: '2026-11-10'
status: 'built'
baseline_revision: '515ad691b88bdf0d9b2176473c0788b07f147207'
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

**Problem:** C5 (AD-6, AD-16): products have texts, a category and one net price, but the company also sells add-ons (Byte Sync, Junior, one arch, ODB DigitalTwin), prices Spark on Demand per aligner within a quantity range, grants calls per product, and needs each product's provider cost at a date for the margins of epic-sales-finance and the order lines of epic-payments.

**Approach:** On the admin site, add to the catalogue: add-ons, per-unit pricing with a minimum and maximum quantity, a call allowance and call length per product, and links from a product to provider products (5.4) whose costs at a date sum to the product's cost. mytp-core 0.21.0 carries them as optional parts of the catalogue section; the portal stores them and the resolver prices a product with its chosen add-ons and quantity.

## Boundaries & Constraints

**Always:**
- The resolver stays the only code that prices (AD-16); amounts are `Money`, rounded half up once, at the end of a line.
- Contract compatibility as in entry 5: new fields and parts optional, absent ones not dumped, `min_reader` unchanged; release order core → admin → portal.
- Never hard-deleted: add-ons and links are deactivated or ended, as products are.
- Provider details never leave the admin site; only what the provider costs section already carries.
- Every change audited and rebuilding the snapshot.

**Never:** no clone into another currency (5.7); no subscription-linked products, per-category discounts or Premium call extras (5.8); no billing rules (5.9); no order, appointment or margin report (their epics read what this entry stores).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Byte Sync | any case product + Byte Sync (+120.00 EUR) | the line is 120.00 EUR higher; the subscription discount applies to the product only | an add-on in another currency than the product is not offered |
| ODB DigitalTwin | an `odb` product + DigitalTwin (+320.00 EUR) | +320.00; not offered on other categories | — |
| One arch | a `tps_in_house` product of 100.00, discount 10 %, one arch −25 % | 67.50 (100 × 0.75 × 0.90, rounded once) | not offered on `tps_branded` or any other category |
| Junior | Junior I or II (amount 0) on a TPS of size medium | offered, price unchanged | not offered on short, long or no size |
| Per unit | Spark on Demand one arch, 50.00 per aligner, 4 to 9, quantity 6 | 300.00 | quantity 3 or 10 refused; a quantity other than 1 on a product not priced per unit refused |
| Calls | a product with 3 calls of 25 minutes | stored on the admin site and readable on the portal | both or neither; whole numbers above 0 |
| Cost at a date | links to two provider products (quantities 1 and 2), costs known on the date | cost = cost₁ + 2 × cost₂, complete | a link in another currency than the product is refused |
| Gap in costs | one linked provider product has no cost on the date | the partial sum, marked incomplete, with a warning naming the provider product without a cost | never shown as a complete cost |
| Monthly link | a link marked monthly (Dental Monitoring) | not in the sum; listed apart with its monthly cost | — |
| Who edits links | Financial Manager or Sysadmin | can add, change and end links | Commercial Manager sees them, cannot change them |
| Old snapshot | an admin before this release | dev keeps working, no add-ons, no costs | — |

## Decisions

- Decision (user, 2026-11-10): an add-on is its own kind of row (code, texts per language, a fixed amount in one currency or a percentage, the product categories it applies to, active), chosen next to a product. Byte Sync +120 EUR on every case category; ODB DigitalTwin +320 EUR on `odb`.
- Decision (user, 2026-11-10): TPS products get an optional size (short, medium, long); Junior I and Junior II are add-ons of amount 0 offered only on size medium.
- Decision (user, 2026-11-10): one arch is a −25 % add-on applied to the net price together with the subscription discount, rounded once. For now it is offered on TPS in-house only: TPS branded does not offer one arch. ODB one arch stays a separate product at its own price.
- Decision (user, 2026-11-10): a product can be priced per unit with a minimum and maximum quantity; Spark on Demand is two products (one arch, two arches) and the resolver takes a quantity.
- Decision (user, 2026-11-10): calls are two optional fields on a product, number of calls and minutes per call; the minutes for several cases and Premium's extras stay with epic-appointments and entry 8.
- Decision (user, 2026-11-10): a cost link has a quantity and a "monthly" mark; a product's cost at a date is the sum of its non-monthly links (provider cost × quantity); monthly links are listed apart; a link is in the product's currency. When a linked cost is missing on the date, the partial sum is shown with a warning that the cost is incomplete and what is missing; it is never a complete cost.
- Decision (user, 2026-11-10): the links travel in the catalogue section and the portal stores the provider costs section, so the resolver gives a product's cost at a date.
- Decision (user, 2026-11-10): the links are edited by the Financial Manager and the Sysadmin.
- Decision (user, 2026-11-10): one plan, kept whole; mytp-core 0.21.0 → admin → portal.

</frozen-after-approval>

## Code Map

- Repositories: this plan is in the portal (`mytreatplan.com`); mytp-core is `../mytp-core` (baseline a8f018917dc6eb73c6fae3e470d7ba52a0d95230) and the admin site `../mytp-admin` (baseline af1ee00be411c47b8fb8234fe261e51c13734e3e). The branch `story/5.6-products-add-ons` is checked out in all three: work there, commit nothing, push nothing, tag nothing. Both sites vendor a provisional 0.21.0 wheel built from the core branch, the way 0.20.0 is vendored (the release rebuilds it from the tag). Run each repository's full suite as its README says (the portal's venv is `../.venv`; the admin site's test venv is `/private/tmp/claude-501/admin-venv`). The line numbers below predate small edits on master; search by name when one is off.

- Requirements: `MyTP_Reqs_Def_V1_21Oct2026_v3.docx` ¶111-141 (sizes, Junior, one arch, Byte Sync, ODB, DigitalTwin, Spark on Demand), ¶238-242 (calls), ¶360 (costs). Epic Notes :60-61 (line order; standard discount only on plain TPS). AD-6 :128, AD-16 :193.
- Admin: `catalogue/models.py` `Product` :156-251; `service.py` `PRODUCT_FIELDS` :45, `DECIMAL_FIELDS` :58, `_create/_update/_set_active` :150-200, `_product_values` :245; `views.py` `ProductForm` :257-320, `_product_details` :429, Kinds :454; `snapshot.py` `product_entry` :26, `section` :51; `audit/registry.py` :33-57; `permissions/matrix.py` :81, :106-114. `providers/models.py` `ProviderProduct` :109, `ProviderPrice` :150; `providers/service.py` `cost_at` :269, `available_for_links` :279 (built for this entry).
- mytp-core: `contracts/catalogue.py` `_without_absent` :134, `PRODUCT_OPTIONAL` :164, `CatalogueProduct` :167-221, `CatalogueSection._consistent` :253 (a cross-row rule is a reader rule); `contracts/provider_costs.py` :104-122 (`cost_at`, `cost_money_at`, `section.product(id)`); `contracts/config.py` :51-81; `money.py:14` (`CurrencyMismatch`).
- Portal: `catalogue/models.py` `Product` :58; `applier.py` `apply` :39; `resolver.py` `discount_percent` :76, `price` :87, `product` :95, `offers` :106; `price_catalogue`; `provider_costs` is not applied today; `catalogue/tests/test_catalogue.py:310` pins the applier names.

## Tasks & Acceptance

**Execution:**
- [x] mytp-core 0.21.0 (`mytp_core/contracts/catalogue.py`, `schemas/config_snapshot.json`, version, README, tests): optional product fields (size, per-unit pricing with minimum and maximum quantity, calls and minutes, cost links: provider product id, quantity, monthly) and an optional `add_ons` list in the catalogue section (id, code, texts, amount or percent, categories, sizes, active); absent parts not dumped; helpers for a line price (product, add-ons, quantity, discount) and a cost at a date over the provider costs section (sum, complete or not, what is missing, monthly links apart).
- [x] Admin (`catalogue/models.py`, `service.py`, `views.py`, `snapshot.py`, `audit/registry.py`, `permissions/matrix.py`, migrations, README, tests): `AddOn` with its pages (catalogue permission); product size, per-unit pricing, calls; `ProductCostLink` (product, provider product from `providers.service.available_for_links`, quantity, monthly, ended not deleted) edited by whoever can change providers; the product page shows the cost today with the incomplete warning and the monthly links apart; snapshot entries.
- [x] Portal (`catalogue/models.py`, `applier.py`, `resolver.py`, `management/commands/price_catalogue.py`, a provider costs applier registered with `integration.config`, migrations, `deploy/README.md`, tests): store add-ons, the new product fields, links and provider costs; resolver: add-ons offered for a product, `price` with add-ons and quantity, `cost(product, day)`; `price_catalogue` options for add-ons, quantity and cost; update the test that pins the applier names.
- [x] Both sites: vendor the new core.
- [x] Tests for every row of the matrix.

**Acceptance Criteria:**
- Given Byte Sync, when it is added to any case product, then the line is 120.00 EUR higher.
- Given Junior I, when the product is not of size medium, then it is not offered.
- Given Spark on Demand, when the quantity is 4 to 9, then the price is quantity × the unit price of its arch option; 3 or 10 are refused.
- Given a product linked to two provider products, when its cost is asked at a date, then it is the sum of their costs on that date; when one has no cost on that date, then the partial sum comes with the incomplete warning.

## Implementation Notes

- Contract shapes (mytp-core 0.21.0): a product's `size` (a code), `per_unit` (`{min_quantity, max_quantity}`: its presence is what makes the price per unit), `calls` (`{count, minutes}`) and `cost_links` (`[{provider_product, quantity, monthly}]`, only the links in force, a provider product at most once); the section's `add_ons` (`{id, code, texts, amount | percent, categories, sizes?, active}`; ids and codes once). Whole numbers are 1 to 1 000 000. A percentage is signed, −100.00 to 100.00; a fixed amount is never negative.
- Line price: net price × quantity × (1 + p/100) per percentage add-on × (1 − discount/100) + the fixed amounts, exact, rounded once. A fixed add-on amount is added once per line, whatever the quantity (the plan did not say; no documented add-on applies to a per-unit product).
- The helpers are plain functions in `contracts.catalogue` (`line_price`, `check_quantity`, `add_on_applies`, `sum_costs` with `ProductCost` / `MonthlyCost`), wrapped by `CatalogueProduct.line_price` / `cost_at` and `CatalogueSection.add_ons_for`; the portal's resolver and the admin's `product_cost` call the plain ones on their own rows.
- Cost: a link whose provider product has no cost on the date, is not in the provider costs section, or is in another currency than the product counts as missing (named by its code; by its id when not listed). A monthly link without a cost is listed with no cost and does not make the sum incomplete. A deactivated provider product keeps its costs in the sum; only new links are limited to `available_for_links()`.
- Admin: per-unit is the two quantities, both or neither (no separate flag). An ended link stays ended (a new one is added); at most one link in force per product and provider product; a product with links in force cannot change currency. The link views are guarded by `providers` change plus `catalogue` view; the cost card shows to `providers` view (so not to the Commercial Regional Manager). `permissions/matrix.py` only gained comments: no grant changed. No add-on is seeded: they are created by hand (the staging check).
- Portal: cost links are a JSON column of `catalogue_product` and the new product columns are nullable or have database defaults, so the previous release keeps applying snapshots during the deploy. Migration 0003 clears the applied snapshot version, as 0002 did, so the first pull stores the add-ons and costs of the version already applied. `offers` lists a per-unit product at its unit price (`resolver.unit_price`).
- After review: a link's label carries the provider product's id when two links of a product share a code (`link_labels`); `ProductCost.empty` marks a product with no link to sum (none, or only monthly ones), shown as "no cost links", never 0.00; `line_price` refuses a negative net price or fixed amount; the edit page of an ended link is a 404. The date of a cost applies to the provider prices only: the links are those in force now.
- Released as mytp-core v0.21.0 (8ec0e35); both sites vendor the wheel built from the tag. Suites on SQLite: core 1415, admin 1243, portal 2476 (+ the 3 known PostgreSQL-only failures).

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, one reviewer per repository, 2026-11-10): 12 findings — 2 medium, 10 low (4 of them rejected), 0 false. No unmet acceptance criterion.

| # | Where | Finding | Verdict | Route | Evidence / action |
|---|-------|---------|---------|-------|-------------------|
| 1 | core `cost_at`, `ProductCost.warning` (and both sites' labels) | two linked provider products with the same code cannot be told apart in the warning | low | patch | the code is unique per provider only (`provider_costs.py`); labels carry the id when a code repeats |
| 2 | core `line_price` | a negative net price or fixed amount is accepted | low | patch | the rule held only in the contract models; refused with a ValueError |
| 3 | core `add_ons_for` | an add-on in a currency `Money` does not take is offered but cannot be priced | low | rejected | only reachable when a later admin release writes a new currency; the raise is documented |
| 4 | core README and module docstring | the example shows one arch on size medium and Byte Sync on two categories | low | patch | contradicted the decisions; the staging check creates add-ons from such examples |
| 5 | core `line_price` | any `Inexact` is reported as "too many percentages" | low | rejected | cosmetic; the input is still refused |
| 6 | core `contracts/__init__.py` `__all__` | new names break the alphabetical order | low | patch | reordered |
| 7 | portal migration 0003, `deploy/release.sh` | the "first pull applies the snapshot again" guarantee is lost when the previous release pulls between `migrate` and the switch, or after a failed deploy that is retried | medium | patch (docs) + defer | the timer still runs the previous release in that window; the exceptions, the check and the remedy are now in the migration docstring and the README; a mechanism that does not depend on the migration is deferred (deferred-work.md), the pattern being entry 5's |
| 8 | portal `apply_provider_costs`, `resolver.cost` | a linked provider product in an unknown currency is named by its id, not its code | low | rejected | only reachable with a future currency; the sum is still incomplete and correct |
| 9 | admin `_costs.html`, `product_cost` | a product with nothing to sum is shown as costing 0.00, complete | medium | patch | an unknown cost read as a known zero; `ProductCost` now says whether anything was summed, and the pages say there are no cost links |
| 10 | admin `product_cost`, README (and the portal's) | the date applies to the provider prices, not to the links | low | patch | documented in the four places; a true past cost is the order line's snapshot (AD-6) |
| 11 | admin `add_cost_link` | the availability check is not under a lock | low | rejected | the resulting state is one the code accepts (a deactivated provider product keeps its links); the fix adds locking across apps |
| 12 | admin `cost_link_edit` | an ended link still has an edit page | low | patch | 404 for an ended link |

## Verification

**Commands:**
- mytp-core, admin and portal suites — all pass; CI on PostgreSQL.

**Manual checks (staging):**
- On admindev: Byte Sync, DigitalTwin, one arch, Junior I and II; a TPS in-house of size medium; the two Spark on Demand products; calls on a product; a product linked to two provider products and one monthly link. On dev: `price_catalogue` shows Byte Sync +120, Junior only on the medium product, one arch only on TPS in-house, Spark on Demand for 4 to 9 aligners (3 and 10 refused), the product's cost as the sum of its links, and the incomplete warning when a provider cost is ended.
