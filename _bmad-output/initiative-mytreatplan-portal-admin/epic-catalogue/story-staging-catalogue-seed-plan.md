---
title: 'Staging catalogue seed'
type: 'feature'
ticket: '13'
created: '2026-11-13'
status: 'done'
baseline_revision: '6430d06362573d7a70c0909759d140d00af595f3'
route: 'full'
route_source: 'auto'
risk: 'low'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Staging holds leftovers of each catalogue story (STG55…, STG58…), not the catalogue of the requirements document, so nobody can check on dev and dev-ae that a doctor of each country group sees the documented prices. The ae region has no subscription at all.

**Approach:** Add a command to the admin site, `seed_staging_catalogue`, that loads the document's products, add-ons, subscription grid and the billing rules they need through the existing services, marked as staging data, and refuses to run on a production site. Admin repo (`../mytp-admin`) only.

## Boundaries & Constraints

**Always:**
- Every write goes through the app's service functions as `audit.command_actor('seed_staging_catalogue')`: audited and validated.
- The whole run is one transaction: all of it or nothing; each intermediate state passes the contract (deactivate before creating what would collide).
- The command refuses to run where `interface.checks.public_deploy()` is true.
- Seeded products and subscriptions carry a note starting `STAGING SEED` and a code starting `SEED-`.
- Prices the document states are typed as the document states them (its AED prices are not peg conversions).
- The country groups (Iberia, Rest of EU, Rest of world in eu; UAE, Rest of Middle East in ae) and the companies (ES, IE, AE) must already exist; a missing one is an error naming it, never created.

**Never:**
- No vouchers, no providers or cost links, no education products, no wholesale prices, no Spark Advanced 5-call product, no country-group or company edits.
- No change to the portal or mytp-core; no change to services, models or the contract.
- No hard delete.

## Decisions

- Decision (user, 2026-11-13): the seed deactivates every active product and subscription that is not one of its own (the STG… rows, the legacy Basic · Iberia, "Staging TPS 5.2"); the five add-on codes BYTE-SYNC, ODB-DT, ONE-ARCH, JUNIOR-1, JUNIOR-2 are reused; vouchers stay.
- Decision (user, 2026-11-13): a re-run finds each row by its code (rules by group and category) and updates it to the seed's value if it differs; with nothing changed it writes nothing.
- Decision (user, 2026-11-13): Basic everywhere, and Advanced and Premium outside Iberia, offer ICE, TPS branded and TPS in-house. Iberia's Advanced and Premium add VirtuaOrtho One, DigitalTwin, ODB, retainers and Spark on Demand.
- Decision (user, 2026-11-13): AED prices the document does not give are EUR × 3.6725 rounded to whole units, typed in the seed.
- Decision (user, 2026-11-13): USD prices are EUR × 1.12 rounded to whole units, typed in the seed; Rest of world's seeded rules are in USD.
- Decision (user, 2026-11-13): the 6 % (Advanced) and 12 % (Premium) apply to TPS branded only.
- Decision (user, 2026-11-13): Rest of Middle East is billed by the UAE company in USD, VAT 0 % with a staging placeholder export text, no gateway, with the same offers and tiers as the UAE.
- Decision (user, 2026-11-13): merge and staging (running the seed on admindev) are delegated.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First run | groups and companies exist; older staging rows active | the grid below is active and published; older products and subscriptions inactive; a summary counts created, updated, deactivated per kind | None |
| Second run | nothing changed | "nothing to change"; no write, no new snapshot version | None |
| A seeded row was edited by hand | e.g. a price changed | the row is back at the seed's value; one update counted | None |
| A seeded row was deactivated by hand | — | active again with the seed's values | None |
| `--dry-run` | any | the same summary, marked as not written; nothing written, no version, no nudge | None |
| Production site | `public_deploy()` true | nothing written | CommandError |
| A group or company is missing | — | nothing written | CommandError naming it |
| A write is refused midway | a service raises | nothing written (the transaction rolls back) | CommandError with the service's message |

</frozen-after-approval>

## Code Map

- `../mytp-admin/catalogue/service.py` -- `create_product` :434, `update_product` :452, `deactivate_product` :464, `create_add_on` :580 (and its update), `create_subscription` :813, `update_subscription` :835, `deactivate_subscription` :845. A write that changes nothing writes nothing. Do not use `clone_product` (amounts are typed).
- `../mytp-admin/billing/service.py` -- `create_rule` :274, `update_rule` :292; a rule's company is of the group's region (:92); one active rule per (group, category).
- `../mytp-admin/catalogue/models.py` -- texts `{en, es}` (:102); `size` only on the two TPS categories (:387); one active standard subscription per tier and group (:536); codes unique globally; `notes` on Product and Subscription only.
- `../mytp-admin/interface/config.py:167` -- `rebuild()` inside every write validates the whole body per region and queues a nudge on commit.
- `../mytp-admin/interface/checks.py:22` -- `public_deploy()`.
- `../mytp-admin/audit/service.py:62` -- `command_actor(name)`.
- Command conventions: `interface/management/commands/nudge_config.py`; tests as `interface/tests/test_config.py:470`. Test helpers: `catalogue/tests/test_catalogue.py` (`group`, `product`, `subscription`), `catalogue/tests/test_regions.py` (`company`, `rule`), `conftest.py` `region(code)`. A `django_db(transaction=True)` test must create its own regions, country map rows and groups.
- Portal constraints: a fixed-amount add-on applies only to products in its currency; a product is offered only in its rule's currency; a priced subscription's currency equals its group's `subscription` rule currency.

## Tasks & Acceptance

**Execution:**
- [x] `../mytp-admin/catalogue/seed_staging.py` (new) -- the seed's data (Design Notes) as plain structures, and the function that applies it and returns the counts -- data apart from the command, so tests read the same table.
- [x] `../mytp-admin/catalogue/management/commands/seed_staging_catalogue.py` (new) -- the guard, `--dry-run`, one transaction, the summary, errors as `CommandError`.
- [x] `../mytp-admin/catalogue/tests/test_seed_staging_catalogue.py` (new) -- every matrix row; the served snapshot of each region validates and holds the grid (per group and tier: the offered product codes, prices, discounts, subscription prices); every Design Notes amount asserted from literal numbers written in the test, not read back from the seed module.
- [x] `../mytp-admin/README.md` -- the command, what it deactivates, the guard, the staging checks.

**Acceptance Criteria:**
- Given the seed has run, when the portal contract's reader reads each region's served body, then every seeded subscription's linked products are of its region and currency, and every (group, category) it offers has an active rule in that currency.
- Given the seed has run, when it runs again, then the snapshot version is unchanged.
- Given the existing admin suite, when it runs, then no existing test changed.

## Implementation Notes

All four tasks done in mytp-admin (PR #37): `catalogue/seed_staging.py`, the command, `catalogue/tests/test_seed_staging_catalogue.py` (16 tests), the README section. 39 products, 7 add-ons, 25 rules, 15 subscriptions.

- JUNIOR-1 and JUNIOR-2 are 0 % add-ons with no currency, so one of each serves EUR, USD and AED; an existing amount-0 EUR row is converted.
- Codes look like `SEED-TPSB-MEDIUM-EUR`, `SEED-AE-ICE-AED`, `SEED-ADV-IBERIA`, `SEED-PREM-ROME`; names in English and Spanish, descriptions empty.
- A rule with no active row for its pair reuses the latest inactive one and reactivates it.
- A reactivation and a value change both count as one "updated"; the counts come from the audit entries each service call writes.
- An inactive group or company is refused like a missing one.
- A row with a `SEED-` code in the wrong region makes the command fail (a region never changes).

## Plan Change Log

## Review Triage Log

Quick lens, one reviewer (2026-11-13). 2 findings, both low, both patched. The reviewer checked every amount, the grid, the write order, the guard and the counting against the plan and found them right.

| # | Finding | Verdict | Evidence | Route |
|---|---------|---------|----------|-------|
| 1 | `--dry-run` on a database with no snapshot row saves version 1 after the rollback | low | `config.current()` creates the row; admindev already has one | patch: the command only reads the version; test |
| 2 | any `ValueError` is reported as the snapshot contract refusing a write | low | the `except ValueError` wrapped every write and lookup | patch: that message only for the contract's error; test |

## Design Notes

**Products** (texts en + es; note `STAGING SEED (epic-catalogue entry 13)`; calls of 25 minutes). Codes: `SEED-<NAME>-<CUR>` for eu, `SEED-AE-<NAME>-<CUR>` for ae.

| Product (category) | EUR (eu) | USD (eu and ae) | AED (ae) |
|---|---|---|---|
| ICE (`initial_case_evaluation`) | 80 | 90 | 294 |
| TPS branded Short / Medium / Long (size; calls 2 / 3 / 4) | 199 / 249 / 359 | 223 / 279 / 402 | 799 / 1045 / 1499 |
| TPS in-house Short / Medium / Long (size; calls 2 / 3 / 4) | 125 / 170 / 230 | 140 / 190 / 258 | 459 / 624 / 845 |
| VirtuaOrtho One Short / Medium / Long (calls 2 / 3 / 4) | 800 / 1200 / 1699 | — | — |
| VirtuaOrtho DigitalTwin Short / Medium / Long (calls 2 / 3 / 4) | 1149 / 1549 / 1999 | — | — |
| Retainers, set of 3 (`retainers`) | 180 | — | — |
| ODB two arches / one arch (`odb`) | 559 / 445 | — | — |
| Spark on Demand one arch / two arches (per aligner, 4 to 9) | 50 / 45 | — | — |

**Add-ons** (shared by the regions): BYTE-SYNC +120 EUR, plus BYTE-SYNC-USD +134 and BYTE-SYNC-AED +441, on the TPS, VirtuaOrtho, ODB and Spark on Demand categories; ODB-DT +320 EUR on `odb`; ONE-ARCH −25 % on `tps_in_house`; JUNIOR-1 and JUNIOR-2 at no charge on medium TPS branded, selectable in every seeded currency (by whatever the services allow with the fewest add-ons).

**Subscriptions** (standard, one per tier and group; codes `SEED-<TIER>-<GROUP>`, e.g. `SEED-ADV-IBERIA`, `SEED-PREM-ROME`):

| Tier | Discounts | EUR (Iberia, Rest of EU) | USD (Rest of world, Rest of Middle East) | AED (UAE) |
|---|---|---|---|---|
| Basic | none | free | free | free |
| Advanced | `tps_branded` 6 % | 60 a month, 600 upfront | 67, 672 | 220, 2204 |
| Premium | `tps_branded` 12 % | 120, 1200 | 134, 1344 | 441, 4407 |

Linked products: ICE, TPS branded and TPS in-house in the group's currency; Iberia's Advanced and Premium add the VirtuaOrtho One, DigitalTwin, ODB, retainers and Spark on Demand products. Iberia's Premium gives +1 call of 35 minutes on `virtuaortho_one` and `virtuaortho_digitaltwin`.

**Billing rules** (for `initial_case_evaluation`, `tps_branded`, `tps_in_house`, `subscription` in every group; Iberia also `virtuaortho_one`, `virtuaortho_digitaltwin`, `odb`, `retainers`, `spark_on_demand`; other existing rules are left alone):

| Group | Company | Currency | VAT | Text | Gateways |
|---|---|---|---|---|---|
| Iberia | ES | EUR | 0 % | exemption: `STAGING TEXT: exempt from VAT (placeholder, not the tax advisor's wording).` | redsys, bank_transfer |
| Rest of EU | IE | EUR | 23 % | reverse charge: `STAGING TEXT: reverse charge (placeholder, not the tax advisor's wording).` | revolut |
| Rest of world | IE | USD | 0 % | exemption: `STAGING TEXT: export, outside the scope of EU VAT (placeholder).` | revolut |
| UAE | AE | AED | 5 % | — | none |
| Rest of Middle East | AE | USD | 0 % | exemption: `STAGING TEXT: export, outside the scope of UAE VAT (placeholder).` | none |

Order that keeps every intermediate state valid: deactivate the old subscriptions, then the old products; rules; add-ons; products; subscriptions.

## Verification

**Commands:**
- the admin suite from `../mytp-admin` with the admin test venv (SQLite) -- expected: all passing
- `python manage.py makemigrations --check --dry-run` -- expected: no changes
- Suite command, from `../mytp-admin`: `SECRET_KEY=ci-only-not-a-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,127.0.0.1,testserver DATABASE_URL=sqlite:///$TMPDIR/adm.sqlite3 FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU=" ALLOW_FAKE_VERIFICATION=True /private/tmp/claude-501/admin-venv2/bin/python -m pytest -q -p no:cacheprovider` (non-secret test keys).

**Working notes:** the code goes in the `../mytp-admin` checkout, on its existing branch `story/5.13-staging-seed` (already checked out; do not create another branch, do not commit). This plan file stays where it is.

## Staging

Merged 2026-11-13: mytp-admin PR #37, merge e1e435a. CI: the PR's test checks passed; master run 43482660372 passed (tests, deploy to admindev). Local suite: 1582 passed, 12 skipped (PostgreSQL-only). Merge and staging delegated by the user.

1. admindev, `seed_staging_catalogue --dry-run`: products 39 created, 19 deactivated; add-ons 2 created, 4 updated; billing rules 5 created, 3 updated; subscriptions 15 created, 6 deactivated; snapshot still v111. Passed.
2. admindev, the real run: the same counts, snapshot v204; dev and dev-ae applied v204. Passed.
3. dev, ES: Basic TPS branded Medium 249.00 EUR, Spanish company, 0 % with the exemption text; Advanced 234.06 (6 %) and VirtuaOrtho One Medium 1200.00 with no discount; Premium 219.12 (12 %). Basic lists 7 offers, Advanced and Premium 18. Passed.
4. dev, DE: Basic TPS branded Medium 249.00 EUR, Irish company, 23 % (57.27); Advanced lists 7 offers, no VirtuaOrtho. Passed.
5. dev, US: Basic TPS branded Medium 279.00 USD, Irish company, 0 % with the export text. Passed.
6. dev-ae, AE: Basic TPS branded Medium 1045.00 AED, UAE company, 5 % (52.25), "no payment method"; buying Advanced upfront 2204.00 AED. Passed.
7. dev-ae, SA (Rest of Middle East): TPS branded Medium 279.00 USD, UAE company, 0 % with the export text, "no payment method". Passed.
8. admindev, a second run: "nothing to change", snapshot v204. Passed.
