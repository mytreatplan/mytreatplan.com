---
title: 'Catalogue tracer: one product priced on dev'
type: 'feature'
ticket: '2'
created: '2026-11-06'
status: 'done'
baseline_revision: '5618ec238cd0099c8a1443899fd7c036c6ace3d0'
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

**Problem:** C2, C7, C10, C11 (AD-3, AD-6, AD-16, AD-22): nothing yet lets a Commercial Manager define a product and a subscription on the admin site and have a region price it; every later catalogue entry needs that shared setup.

**Approach:** Tracer bullet over three repos, three PRs (core → admin → portal). **mytp-core 0.12.0**: an optional `catalogue` section in the snapshot contract (products, country groups, subscriptions; amounts as the `Amount` contract), `min_reader` unchanged. **Admin**: a section registry in `interface/config.py` (each section a builder; `rebuild()` compares all sections), a `catalogue` app (country groups, products, subscriptions) with lists, forms, audit and the `catalogue` area, its writes calling `rebuild()`. **Portal**: a `catalogue` app with read-only tables filled only by the snapshot apply (a section-applier registry in `integration/config.py`), and `catalogue.resolver` — the only code that prices.

## Boundaries & Constraints

**Always:**
- Admin models (never hard-deleted; deactivate, AD-6):
  - `CountryGroup(name, countries: ISO alpha-2 list)`, seeded by a migration (Iberia = ES, PT; Rest of EU = the other EU states; UAE = AE; Rest of world = catch-all: every country in no other group) and shown read-only (list + detail); editing comes with entry 3;
  - `Product(name, category (fixed choices: `tps` "TPS (plain)", `other` "Other"), net_price: amount Decimal(12,2) + currency EUR/AED/USD, active)`;
  - `Subscription(tier basic/advanced/premium, country_group FK, tps_discount_percent Decimal(5,2) 0–100, active)`; one active subscription per (tier, country group).
- Prices are net (VAT at checkout, later entries). Money through `mytp_core.money.Money`; no floats.
- Admin UI under the existing `catalogue` area (matrix unchanged: Commercial Manager and Sysadmin change, Commercial Regional Manager and Financial Manager view): lists with the 3.7 conventions (search, status filter, CSV), create / edit / deactivate / reactivate, all writes in a service under `transaction.atomic()` with `audit.record` and `config_snapshot.rebuild()`; every CHANGE view in `AUDITED_VIEWS`.
- Snapshot: section registry `register_section(name, builder)` on the admin; the directory becomes the first section (body unchanged); `catalogue` holds active and inactive rows with ids (UUIDs as strings). Portal: `register_section_applier(name, apply)` called inside `_apply`'s transaction; a missing section leaves that section's tables empty.
- Portal tables (`catalogue_*`): no write path outside the snapshot apply.
- Resolver `catalogue.resolver.offers(country, tier)` → list of `(product, Money)`: the active subscription of that tier whose active country group contains `country`; for each active product, `net_price × (1 − discount/100)` (half up) when its category is plain TPS, else the net price; no matching subscription → empty list. Command `price_catalogue --country ES --tier basic` prints the offers.

- Decision (user, 2026-11-06): product category is a fixed choice for now (TPS (plain), Other); a later entry makes categories a managed list.
- Decision (user, 2026-11-06): country groups are seeded by a migration and read-only here; entry 3 (country map) adds editing.
- Decision (user, 2026-11-06): built after 4.8's mytp-core release (0.11.0); this story ships mytp-core 0.12.0.
- Decision (user, 2026-11-06): kept whole (~2000 tokens, above the size target), one implementer through the three repos.

**Never:** no VAT, billing company, VIES, ICE, vouchers, currency clones, doctor subscription, checkout or portal UI (later entries); no per-region snapshot payload (entry 11); no change to the 4.4 contract's version, `min_reader`, `sha` or refusal semantics.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| ES doctor | Iberia group (ES, PT), basic subscription 10 %, TPS product 100.00 EUR | `offers('ES','basic')` → product at 90.00 EUR | — |
| US doctor | same | `offers('US','basic')` → empty | — |
| Non-TPS product | category other, 50.00 EUR | listed at 50.00 EUR | — |
| Deactivated | product or subscription deactivated on admindev | gone from offers after the next snapshot | — |
| Rounding | 33.33 EUR, 15 % | 28.33 EUR (half up) | — |
| Invalid input | discount 120, negative price, second active subscription for the same tier and group | form error; nothing saved | — |
| Audit | create, edit, deactivate on admindev | audit entries with before/after | — |
| No change | edit that changes nothing | no new snapshot version | — |

</frozen-after-approval>

## Code Map

Repos (branch `story/5.2-catalogue-tracer` in each, already created; do not push, tag or open PRs). Baselines: mytp-core 6c47da32e75a8f9a4269f9a9f3256821f542d883 (v0.11.0), portal 5618ec238cd0099c8a1443899fd7c036c6ace3d0, admin 9635f76984ef88618a694ff5cd3d750ec4acdbf0. Since this plan was written, entry 4.8 (client records) and 1.11 (object storage) merged: line numbers below may have moved — search by symbol. This plan lives in the planning worktree `.../code/mytreatplan-planning` (write Implementation Notes there); code goes in the main checkouts. Bump mytp-core to 0.12.0; the vendored wheel may be built from the untagged core tree for local tests (rebuilt from tag v0.12.0 at release); update both sites' `test_vendored_core.py` and the admin's `layout/tests/test_deploy.py` wheel name. Tests: portal venv `../.venv` (env: FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU=" DATABASE_URL=sqlite:///$TMPDIR/x.sqlite3; 3 known PostgreSQL-only failures in accounts/tests/test_crypto.py); admin venv `/private/tmp/claude-501/admin-venv` (same two keys plus SECRET_KEY=ci-only-not-a-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,127.0.0.1,testserver DATABASE_URL=sqlite:///$TMPDIR/adm.sqlite3 ALLOW_FAKE_VERIFICATION=True; install wheels with `uv pip install --no-cache --python ... --reinstall --no-deps`); mytp-core `../.venv`. Sandbox must be disabled for the suites. If a local PostgreSQL 17 can be started in the scratchpad, run the PostgreSQL-only tests on it.

Paths relative to `.../code/`.
- mytp-core: `contracts/config.py` `ConfigSnapshot` :50, `SNAPSHOT_READER` :35 (stays 1); `contracts/amounts.py` `Amount`; `money.py` `Money` :105, `*` :209, `to_contract`/`from_contract` :238; `snapshots.py` `verify_snapshot` returns the model (:168), so the section must be a model field; `contracts/__init__.py` `SCHEMA_MODELS` + drift test.
- Admin: `interface/config.py` `snapshot_body` :58, `directory` :50, `_locked_row` :72, `rebuild` :92 (comparison :103); callers `staff/service.py` :78, :190, :208, :224, :245, :320; `permissions/areas.py` (`catalogue` area exists), `permissions/urls.py:11` `IMPLEMENTED_AREAS`, `permissions/decorators.py` `area_required`, `matrix.py` (no change); list template `lists/sample.py` (:41 filter, :77 table, :86 view, :101 form, :182/:195/:218 views), `lists/urls.py`, templates `lists/templates/lists/*`, `layout/htmx.py`; service/audit `lists/service.py:29-82`, `audit/service.py:238`, `audit/registry.py:15`; fuller example `staff/admin_users.py`; `mytp_admin/urls.py` ~:40-44; `INSTALLED_APPS` ~:165-195.
- Portal: `integration/config.py` `_apply` :125 (after :136), `pull()` log :195; `integration/models.py`; `regions/` (Country states, 2.1; not changed); `accounts/models.py:104` `country`; new app `catalogue/` (`models.py`, `resolver.py`, `management/commands/price_catalogue.py`), `INSTALLED_APPS` ~:353.
- Release as 4.4; built after 4.8's core release (0.11.0) → this ships 0.12.0.

## Tasks & Acceptance

**Execution:**
- [x] mytp-core: `CatalogueSection` models (+ schemas), optional field on `ConfigSnapshot`, tests, version, README.
- [x] Admin: section registry (directory moved into it), `catalogue` app (models, migration with seed groups, service, lists/forms/views/templates, urls, area implemented, audit registry), README, tests.
- [x] Portal: section-applier registry, `catalogue` app (tables + migration, applier, resolver, `price_catalogue`), runbook, tests for every matrix row.
- [x] Both sites: vendor the new core (wheel built from the untagged tree; rebuild from tag v0.12.0 at release).

**Acceptance Criteria:**
- Given a snapshot without a `catalogue` section (an older admin site), when the portal applies it, then the directory applies and the catalogue tables are empty.
- Given a Commercial Regional Manager, when they open the catalogue, then they can view and export but not change.

## Implementation Notes

2026-11-06, one implementer through the three repos (uncommitted on `story/5.2-catalogue-tracer` in each).

- **mytp-core 0.12.0**: `contracts/catalogue.py` (`CatalogueSection`, `CatalogueCountryGroup`, `CatalogueProduct`, `CatalogueSubscription`); `ConfigSnapshot.catalogue: CatalogueSection | None = None`, with a wrap serializer that drops a None catalogue, so a snapshot without one dumps and signs byte-for-byte as in 0.11.0 (`sign_snapshot(model)` would otherwise have added `"catalogue": null` and changed the `sha`). `SNAPSHOT_READER`, `min_reader`, the signature and its refusals are unchanged. Cross-row rules: unique ids per list, a subscription's group is listed, one active subscription per (tier, group), a country in at most one active group, at most one active catch-all. Percent: 2-place decimal string `"0.00"`–`"100.00"`; net price never negative. `category` and `tier` are lowercase code strings (not closed enums) so a later managed list does not break readers. `config_snapshot.json` regenerated; README, version in `pyproject.toml` and `__init__.py`. The vendored wheel in both sites is built from the untagged tree: rebuild it from tag v0.12.0 at release.
- **Admin**: `interface.config.register_section(name, builder)`; `admin_directory` is the first section; `rebuild()` compares every section with the stored body (a missing one counts as changed). `catalogue` app: models, migration 0001 + 0002 (seed), `service.py` (atomic + `audit.record` + `config_snapshot.rebuild()`; `full_clean` so bad values are refused in the service too; a savepoint turns a concurrent duplicate into a ValidationError), `snapshot.py` (the section builder, rows sorted by id), `views.py` (three lists as tabs, detail pages with audit history for the Sysadmin, create/edit/deactivate/reactivate for products and subscriptions), templates, urls; area added to `IMPLEMENTED_AREAS`, 8 views to `AUDITED_VIEWS` (with exercises in `audit/tests`). `lists.views.AdminListView` gained `url_name` (an area with several lists; export `<url_name>_export`) and `lists/list.html` a `list_nav` block. `pytest.ini` testpaths gained `catalogue`.
- **Portal**: `integration.config.register_section_applier(name, apply)`, called in `_apply`'s transaction with `getattr(snapshot, name, None)`. `catalogue` app: read-only models (`save()`/`delete()` raise; the applier uses `bulk_create` and queryset deletes; not in the Django admin), `applier.py` (replaces the tables; None empties them; unknown category/currency/tier skipped), `resolver.py` (`offers`, `country_group`, `subscription`, `price`), `price_catalogue`. Runbook: `deploy/README.md` "Catalogue". `pytest.ini` testpaths gained `catalogue`.
- After the admin deploy the stored snapshot has no `catalogue` field until the next staff or catalogue write, which builds it as a new version (documented in both READMEs).

- **Review fixes (2026-11-06):** core: the catalogue docstring and README say the cross-row rules are part of the reader contract (a snapshot breaking one is refused whole, the last good one stays; relaxing a rule needs a `min_reader` bump); README "Configuration snapshot" (example and text), "Contracts" intro and the JSON Schema bullet updated for 0.12.0; product currency documented (writers send EUR/AED/USD; readers skip others); `_decimal_percent` has its own "a percentage …" error; `tests/test_catalogue.py` cases have readable ids and the cross-row cases are parametrized (the dead 4th-case setup is gone). Wheel rebuilt and re-vendored in both sites. Admin: `Product.Currency` labels are the codes (0001 edited in place, `makemigrations --check` clean); the service quantizes price and discount to 0.01 before saving (object, DB, audit and snapshot agree; more places are still refused); the edit view shows "saved" only when something changed; tests for both. Portal: the applied log line ends with the catalogue's stored row counts or "no catalogue section" (an applier returns a counts-only summary); `register_section_applier` refuses a name that is not a `ConfigSnapshot` section field; the resolver strips and lowercases the tier; `price_catalogue` has `choices` on `--tier` and prints the normalised country; the `integration/config.py` docstring is rewrapped; a `{'amount': 1.0, 'currency': 'EUR'}` case is refused; tests for each.

**Verification run (local):** mytp-core 1015 passed (SQLite and PostgreSQL 17); admin 878 passed, 5 skipped (SQLite) and 884 passed (PostgreSQL 17, local server in the scratchpad); portal 2025 passed + the 3 known PostgreSQL-only failures (SQLite) and 2033 passed (PostgreSQL 17). `makemigrations --check` clean in both sites; `export_contract_schemas --check` clean.

- Release (2026-11-06): review fixes applied (all `patch` rows); full suites: mytp-core 1021, admin 881 (5 PostgreSQL-only skips), portal 2027 (+3 known PostgreSQL-only failures, 5 skips); the implementer's PostgreSQL 17 runs passed before the fixes. mytp-core 8ddc118 tagged v0.12.0 (CI green on master and tag); both sites vendor the wheel of the tag (sha256 045222c87016…). PRs: mytp-admin (deploy first), then the portal.

## Plan Change Log

- 2026-11-06, review pass 1: the acceptance criterion "a Commercial Regional Manager can view and export but not change" contradicted the frozen boundary "matrix unchanged" (that role and the Financial Manager have view only on `catalogue`). Read as: they can view but neither change nor export. KEEP: the matrix as it is.

- 2026-11-06 (implementer): `CountryGroup` gained `catch_all` (model and contract). "Rest of world = every country in no other group" needs either that flag or a hard-coded list of every ISO country; the flag keeps the group correct when entry 3 edits the others. The resolver uses it as the fallback.
- 2026-11-06 (implementer): **open question for the human.** The acceptance criterion says a Commercial Regional Manager can "view and export", but the frozen boundary says "matrix unchanged", and the matrix gives that role (and the Financial Manager) VIEW only on `catalogue`. The matrix was left unchanged (frozen section wins): those roles see the lists and pages but get no CSV button and a 403 on the export URLs (`test_view_only_roles_see_but_never_change`). Giving them export is a one-line matrix change (`'catalogue': Grant(_VE, ALL)`) plus `permissions/tests/test_matrix.py` expectations, if wanted.
- 2026-11-06 (implementer): the product's price fields are `net_amount` (Decimal(12,2)) and `currency`, with the `net_price` property returning `Money`.

## Review Triage Log

Pass 1 (quick lens, opus, one reviewer per repo, 2026-11-06): 18 findings. medium 1, low 15, rejected 2.

| # | Repo | Finding | Verdict | Route | Evidence / action |
|---|------|---------|---------|-------|-------------------|
| 1 | admin | Acceptance criterion "Commercial Regional Manager can view and export" not met (view only) | low | rejected | The criterion was the plan's own error: the frozen boundary keeps the matrix unchanged, which gives that role and the Financial Manager view only (Plan Change Log). The code follows the frozen text. |
| 2 | core | A catalogue that breaks the reader's cross-row rules makes portals refuse the whole snapshot (directory too); relaxing those rules later would break 0.12.0 readers | medium | patch | Safe failure (the last good snapshot stays); document in the contract and README that the cross-row rules are part of the reader contract and relaxing them needs a `min_reader` bump. |
| 3 | admin | Currency choice labels "Eur"/"Aed"/"Usd" (TextChoices title-cases the names) | low | patch | Explicit labels = codes; edit the unreleased 0001 migration. |
| 4 | admin | Audit "after" values keep the typed precision ("100", "5") | low | patch | Quantize to 0.01 before saving. |
| 5 | admin | "Saved" shown for an edit that changes nothing | low | patch | Message only when something changed, as `_state_change`. |
| 6 | portal | `pull()` success log says nothing about the catalogue | low | patch | Log the catalogue's row counts, or that the section was absent. |
| 7 | portal | `register_section_applier` accepts a name that is not a `ConfigSnapshot` field (a typo would empty tables silently) | low | patch | Refuse at registration. |
| 8 | portal | Tier not normalised; `price_catalogue --tier` has no choices; country printed unstripped | low | patch | Normalise the tier (strip, lower) and use argparse choices; print the normalised country. |
| 9 | portal | A 117-character docstring line in `integration/config.py` | low | patch | Rewrap. |
| 10 | portal | Test case `float` replaces the whole Amount, not a float inside it | low | patch | Add `{'amount': 1.0, 'currency': 'EUR'}`. |
| 11 | portal | A deactivated group falls through to the catch-all, untested | low | rejected | Groups are read-only in this story (no deactivation path); entry 3 owns group editing. |
| 12 | core | README "Configuration snapshot" still calls the catalogue a future unknown field | low | patch | Update. |
| 13 | core | README "Contracts" intro and schema bullet not updated for 0.12.0 | low | patch | Update. |
| 14 | core | Product currency limited to EUR/AED/USD not documented; readers must skip unknown currencies | low | patch | Document in the contract and README. |
| 15 | core | `_decimal_percent` errors call the percentage an "amount" | low | patch | Own message. |
| 16 | core | Test ids `case0`…`case17` | low | patch | Readable ids. |
| 17 | core | Dead setup in the 4th cross-row case | low | patch | Simplify; name the cases. |

## Verification

**Commands:**
- mytp-core, admin and portal suites (local dev notes) — all pass.

**Manual checks (staging, after both deploys):**
- As a Commercial Manager on admindev: create the TPS product (100.00 EUR) and a basic Iberia subscription (10 %); both in the audit log. On dev: `pull_config`, then `price_catalogue --country ES --tier basic` → 90.00 EUR; `--country US` → nothing.

## Staging

2026-11-06, run by Claude on the staging host with the user's permission (the user delegated merging and staging; admin #18 merged, run 42243576368 green; portal #73 merged, run 42244358306 green on dev and dev-ae):
1. On admindev, through `catalogue.service` (as the catalogue pages do; actor a Sysadmin, since admindev has no Commercial Manager user): product "Staging TPS 5.2", TPS (plain), 100.00 EUR; subscription basic · Iberia, 10.00 % → the stored snapshot went from v2 to v4 with the catalogue section (4 country groups, 1 product, 1 subscription).
2. Audit log: `create catalogue.product "Staging TPS 5.2"` and `create catalogue.subscription "Basic · Iberia"`, actor Antonio de Andres, 15:44:22 UTC.
3. dev and dev-ae had already applied v4 through the nudge (`pull_config`: v4 unchanged); `price_catalogue --country ES --tier basic` → "Staging TPS 5.2 tps 90.00 EUR" on both; `--country US` → no offers.
Not checked in a browser: the catalogue pages on admindev (covered by the view tests).
