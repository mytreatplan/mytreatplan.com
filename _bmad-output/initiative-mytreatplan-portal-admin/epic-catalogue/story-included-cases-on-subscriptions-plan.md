---
title: 'Included cases on subscriptions'
type: 'feature'
ticket: '17'
created: '2026-11-15'
status: 'built'
baseline_revision: 'd26fc1e'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 1
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Paid subscriptions are to include free branded-TPS cases (decision 2026-11-15, initiative Notes), but a subscription row has nowhere to hold that number, so neither the admin site nor the portal can know it (C13).

**Approach:** Add an optional `included_cases` map `{category: number}` to each subscription, the way 5.8 added `discounts` and `call_extras`: edited and checked on the admin site, carried in the catalogue snapshot as an optional field of mytp-core 0.27.0, stored read-only on the portal's copy with a resolver helper. The staging seed gets the owner's numbers. Counting and spending the allowance is 8.13 and epic-ordering, not this story.

**Decisions (user, 2026-11-15):**
- Initial numbers: 12 included cases on Advanced, 25 on Premium (per 12-month term).
- Shape: a JSON map `included_cases: {category: n}` on the subscription, like `discounts`.
- Every subscription category may carry a number (the full `SUBSCRIPTION_CATEGORIES` list, like the discount map); only branded TPS is used by 8.13 for now.
- Zero is always the default: each category's input is a required whole number from 0 to 1,000, shown as 0 on a new subscription and on any category without a number; 0 means none. Stored and sent, a 0 is left out of the map, so "no entry" and 0 read the same.
- Any subscription, standard or manual, may carry a number; staff leave Basic at 0 (the "paid subscriptions only" rule is applied by 8.13, not refused by the form).
- The snapshot carries the field only when the map has an entry (like `call_extras`): deploying the admin site changes no snapshot until someone sets a number.
- Portal helper `included_cases(subscription, category)` returns 0 for none.
- Nothing is shown on the portal in this story (8.13 owns the remaining count on the home).
- Staging seed: 12 on every seeded Advanced and 25 on every seeded Premium subscription, in both regions.
- Prepaid branded-TPS packages are not built now; 8.13 is to be a source-tagged ledger of case-credit grants (recorded in the initiative and epic-subscriptions Notes).
- Merge and staging delegated: I release core v0.27.0, vendor it in both sites, admin PR → admindev deploy → seed → portal PR → check dev's copy and the resolver on the server.

## Boundaries & Constraints

**Always:** Follow the `discounts`/`call_extras` path in every repo. The core field is optional (`dict | None = None`, in `_without_absent`), so a snapshot without it signs byte-identically and a 0.26 reader ignores it; `SNAPSHOT_READER` is unchanged. The core helper maps the legacy `tps` category like `subscription_discount`. The portal column is nullable (the previous release still applies snapshots during a deploy). Every staff-facing string has its Spanish translation where the admin site translates its forms.

**Never:** No counting, reserving or spending of included cases; no doctor-facing text; no change to prices or to the pricing function; no `SNAPSHOT_READER` bump.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| New subscription form | staff opens "new subscription" | every category row shows an included-cases input with 0 | none |
| Set a number | Advanced, branded TPS = 12 | stored `{"tps_branded": 12}`; details and table show it; audit records the change | none |
| All zero | every category 0 | stored map empty; snapshot entry has no `included_cases` | none |
| Out of range | -1, 1001, 2.5 or text | form error on that category | none |
| Empty input | a category left blank | form error: a whole number is required (0 for none) | none |
| Manual subscription | manual row with 25 on branded TPS | stored and published in `manual_subscriptions` | none |
| Edit | edit page of a row with 12 | shows 12 on branded TPS and 0 elsewhere; saving 0 removes the entry | none |
| Snapshot with the field | a number set | entry carries `included_cases`; validated by the vendored 0.27.0 | none |
| Old snapshot | snapshot built before this change | validates and applies on 0.27.0 unchanged; signature identical | none |
| Portal apply | snapshot with the field | the portal's copy stores the map; `included_cases(sub, 'tps_branded')` = 12 | none |
| Portal none | subscription without the field, or category not in the map | helper returns 0 | none |
| Legacy category | map or call with `tps` | read as `tps_branded` | none |
| Seed | `seed_staging_catalogue` | every seeded Advanced = 12, Premium = 25 on branded TPS, both regions; re-run idempotent | none |
| Deploy order | portal on 0.26 reads a 0.27 snapshot | ignores the field, applies the rest | none |


</frozen-after-approval>

## Code Map

- Precedent: 5.8 `epic-catalogue/story-subscriptions-in-full-plan.md` (discounts, call extras).
- **mytp-admin** (latest migration `catalogue` 0009 → new 0010): `catalogue/models.py` `Subscription` :456-672 (`discounts` :518, `call_extras` :528, `clean()` :578-583, `check_discounts` :420, `check_call_extras` :438, `discount_for` :597, `discounts_text` :642, `SUBSCRIPTION_CATEGORIES` :677, `Product.Category.TPS_BRANDED` :287; manual subscriptions differ only by `kind` :498-506). Service `catalogue/service.py` `SUBSCRIPTION_FIELDS` :94, `normal_discounts` :696-722, `_subscription_values` :760-812, `create_subscription` :814, `update_subscription` :836. Form `catalogue/views.py` `SubscriptionForm` :646-781 (`_category_field` :640, prefix tuples :730/:740, `clean()` :769-780, edit initials :1088-1095, `_subscription_details` :926, `SubscriptionTable` :254); template `catalogue/templates/catalogue/_form.html:26-40` (`category-table`). Snapshot `catalogue/snapshot.py` `subscription_entry` :100-129; `interface/config.py` `snapshot_body` :129-141 validates with the vendored core. Seed `catalogue/seed_staging.py` `TIERS` :184-186, `_subscriptions()` :195-217, codes `SEED-ADV-*`/`SEED-PREM-*` :331-341. Tests pinning names: `catalogue/tests/test_subscriptions.py:485-535`, `test_catalogue.py:42,317,759`, `audit/tests/test_audit.py:944,960`, `interface/tests/test_vendored_core.py:21,146-150` (version + exact field set), `catalogue/tests/test_seed_staging_catalogue.py:209-225`. README :1311-1314, :1384, :1843-1848.
- **mytp-core** (v0.26.0 → 0.27.0): `contracts/catalogue.py` `SUBSCRIPTION_OPTIONAL` :931, `CatalogueSubscription` :935-1014, helpers `subscription_discount` :828 / `calls_under` :862, `_LEGACY_CATEGORIES` :823 (`tps`→`tps_branded`), `Quantity` :341, `_without_absent` :373 (absent optional fields stay absent, old snapshots sign byte-identically), docstring :248-266 (`SNAPSHOT_READER` unchanged: an optional row field needs no bump). Schema `schemas/config_snapshot.json` (re-export; `tests/test_contracts.py:320`). Version `pyproject.toml:7-8`, `__init__.py:36,48`, `tests/test_hub.py:29`. Tests `tests/test_catalogue.py:789-1095`. README catalogue section ~:659-704, compatibility like :1141.
- **Portal** (latest migration `catalogue` 0008 → new 0009): `catalogue/models.py` `Subscription` :191-260 (new columns nullable or `db_default`: the previous release still applies snapshots during a deploy, :228-239); `catalogue/applier.py` `_subscription` :119-145; `catalogue/resolver.py` `discount_percent` :341, `calls` :359, `__all__` :147-160. A new release re-applies the current snapshot version (pinned `catalogue/tests/test_subscriptions.py:163-182`), so dev's copy fills itself after the deploy. `integration/tests/test_vendored_core.py:21,146-150`. `deploy/README.md` "Subscriptions in full" :3075-3110.
- Deploy order: core tag v0.27.0 → vendor in both sites → admin PR, admindev deploy, numbers set → portal PR. A 0.26 portal ignores the field (`extra='ignore'`), so order is safe either way.

## Tasks & Acceptance

**Execution:**
- [x] mytp-core `contracts/catalogue.py` -- optional `included_cases` on `CatalogueSubscription` (category → whole number 1..1,000, zeros refused or dropped as discounts do), in `SUBSCRIPTION_OPTIONAL` and `_without_absent`; helper `included_cases(entry, category)` beside `subscription_discount` (legacy `tps` mapped; 0 for none); docstring "Since 0.27.0"; schema re-export; version 0.27.0 (`pyproject.toml`, `__init__.py`, `tests/test_hub.py`); README catalogue + compatibility paragraphs; tests (old snapshot signs identically, new field round-trips, bad values refused). Do NOT tag or push: I release.
- [x] mytp-admin `catalogue/models.py` + migration 0010 -- `included_cases` JSONField (default `{}`), `check_included_cases` (category in `SUBSCRIPTION_CATEGORIES`, whole number 0..1,000), in `clean()`; `included_cases_for`, `included_cases_text`.
- [x] mytp-admin `catalogue/service.py` -- in `SUBSCRIPTION_FIELDS`, normalised like `normal_discounts` (0 dropped), `create_subscription`/`update_subscription` keyword.
- [x] mytp-admin `catalogue/views.py` + `_form.html` -- an `included_cases` column in the category table, required, initial 0, edit initials from the row; details page and table show the numbers; Spanish where the admin translates.
- [x] mytp-admin `catalogue/snapshot.py` -- `subscription_entry` adds `included_cases` only when the map has an entry.
- [x] mytp-admin `catalogue/seed_staging.py` -- Advanced 12, Premium 25 on `tps_branded`, both regions; idempotent re-run.
- [x] mytp-admin tests and README -- the matrix rows on the admin side; the pinned field lists (`test_subscriptions.py`, `test_vendored_core.py` version + field set, audit tests); README subscriptions and seed paragraphs.
- [x] Portal `catalogue/models.py` + migration 0009 -- nullable `included_cases` JSON column; `catalogue/applier.py` stores it (absent → None); `catalogue/resolver.py` `included_cases(subscription, category)` (0 for none, legacy `tps` mapped) in `__all__`; tests incl. `integration/tests/test_vendored_core.py`; `deploy/README.md` paragraph.
- [x] Vendor the core wheel in both sites (a local build during the work; I rebuild from the tag before the PRs).

**Acceptance Criteria:**
- Given an Advanced subscription with 12 on branded TPS saved on admindev, when dev pulls the next snapshot, then dev's copy holds the map and `included_cases(sub, 'tps_branded')` returns 12.
- Given a subscription with every category at 0, when the snapshot is built, then its entry has no `included_cases` and the resolver returns 0.
- Given a snapshot built before 0.27.0, when it is validated and applied on 0.27.0, then it applies unchanged and its signature is identical.
- Given the three suites, when they run, then they pass with no new skip; `makemigrations --check` reports no change in both sites; the core schema export matches.

## Design Notes

Keep the helper name source-neutral (`included_cases`) and the map keyed by category, so a later prepaid package product could carry the same shape. Nothing in this story counts or spends cases.

## Verification

Suites of the three repos; `makemigrations --check` in both sites; core schema export check.

## Implementation Notes

- Core: `included_cases: dict[Code, IncludedCases] | None` (StrictInt 1..`MAX_INCLUDED_CASES` = 1,000; a 0 is refused, the writer drops it); helper `included_cases(included, category)` takes the map (or None) and the category (the plan's "entry" read as the map, so the portal passes its stored column); `tps` is mapped in the category and in the map's keys (exact key first); it re-checks the value (whole 0..1,000). Method `CatalogueSubscription.included_cases_for(category)`. `SNAPSHOT_READER` unchanged.
- Admin: the form field is an `IntegerField(required=True, initial=0, min 0, max 1,000)` per category, errors "A whole number is required (0 for none)" (empty, text, 2.5) and "Included cases are a whole number from 0 to 1,000 (0 for none)" (-1, 1001). The admin site does not translate its forms (English staff UI; its `LANGUAGES` are the public-text languages), so no Spanish strings were added. Test POSTs carry `conftest.ZERO_CASES`. The snapshot also filters a stored 0 defensively.
- Local wheel: built from a copy of the core working tree's tracked files (`git ls-files | rsync`), `SOURCE_DATE_EPOCH` = core HEAD commit time, `pip wheel --no-deps`, copied to both sites' `vendor/` with the `requirements.txt` line moved to 0.27.0. Rebuild from the tag with `scripts/vendor-mytp-core.sh v0.27.0` before the PRs.
- Deploy-order row: checked by hand with the shared `../.venv` (mytp-core 0.26.0) verifying and validating a signed snapshot that carries `included_cases`: accepted, field dropped.
- Results: core 1598 passed, 4 skipped; admin 1608 passed, 12 skipped; portal 3307 passed, 12 skipped; `makemigrations --check` no changes in both sites; `export_contract_schemas --check` up to date.
- Review fixes (2026-11-15): (1) the admin column has `db_default=models.Value({}, models.JSONField())` (model and migration 0010, edited in place: unreleased), so the previous release can still insert subscriptions during the migrate; test `test_a_row_written_without_the_column_gets_an_empty_map` inserts a row by raw SQL without the column (works on SQLite: the test database is built by the migrations, so the column has its DEFAULT). (2) Core test `test_a_reader_ignores_a_subscription_field_it_does_not_know` pins `extra == 'ignore'` on `CatalogueSubscription` and `CatalogueSection` (the deploy-order row). (3) `MAX_INCLUDED_CASES` sorted in core's `contracts/__init__.py` import and the line wrapped; the portal's `__all__` line and an admin service docstring line wrapped. The wheel was rebuilt (sha256 `c52bc146…c67c`) and re-vendored in both sites. Results after the fixes: core 1599 passed, 4 skipped; admin 1609 passed, 12 skipped; portal 3307 passed, 12 skipped; `makemigrations --check` no changes in both sites; schemas up to date.

## Review Triage Log

Quick lens (Opus reviewer, 2026-11-15) over the three repos: backward compatibility, admin form, seed, core helper and version bump all ok.

| # | Severity | Finding | Verdict |
|---|----------|---------|---------|
| 1 | low | Admin column NOT NULL with only a Python default: the previous release, still serving during migrate, fails on a subscription INSERT | patched: `db_default` `{}` on the field and 0010; raw-INSERT test |
| 2 | low | Admin snapshot entry drops only falsy values; a bad value written outside the service would block every snapshot build | rejected: same exposure as `discounts`/`call_extras`; form, service and model refuse bad values |
| 3 | low | "A 0.26 reader ignores the field" had no automated test | patched: core test pins `extra='ignore'` on `CatalogueSubscription` and `CatalogueSection` |
| 4 | low | Long lines / import order | patched |

Suites after the patches, run by me on the tagged v0.27.0 wheel (sha256 e759698f…): core 1599 passed / 4 skipped (CI green on master and the tag); admin 1609 / 12; portal 3307 / 12; `makemigrations --check` clean in both sites.
