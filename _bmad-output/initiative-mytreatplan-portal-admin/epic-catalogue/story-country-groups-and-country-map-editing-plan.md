---
title: 'Country groups and country-map editing'
type: 'feature'
ticket: '3'
created: '2026-11-07'
status: 'built'
baseline_revision: 'portal 8243282, core 96c6919, admin 9c261f1'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** C3 (AD-14): country groups are seeded and read-only on the admin site (5.2), and each region's country map (eu, ae, banned per country) is a frozen seed (2.1); nobody can change either without a release.

**Approach:** On the admin site, make country groups editable (create, edit, deactivate, reactivate) and add a country-map editor (one row per ISO country with its state), both audited and published in the configuration snapshot; a new optional `country_map` section in mytp-core; the portal applies it in `regions/`, replacing the seeded rows by code, while keeping its seed until the first section arrives.

## Boundaries & Constraints

**Always:**
- Groups: the 5.2 cross-row rules stay exactly as they are (a country in at most one active group, at most one active catch-all) and are now enforced on the admin site too (service + form, group rows locked during a write), so the admin never publishes a snapshot the portals refuse. "Rest of world" stays the catch-all (any country in no other active group).
- Country map on the admin site: `CountryState(code pk ISO alpha-2, name, state)` seeded by a migration from the portal's frozen 2.1 seed (codes, states, English names); edited one country at a time (list filtered by state, search by code or name, an edit form); every change audited and rebuilding the snapshot.
- Contract (mytp-core): `country_map` optional section `{countries: [{code, state}]}`, unique codes; `_without_absent_sections` extended so a snapshot without it signs as before; `min_reader` unchanged; the catalogue's rules unchanged.
- Portal: `regions/applier.py` registered for `country_map`: present → replace the `Country` rows by code in the snapshot transaction (unknown states skipped, counts-only summary); absent → leave the current rows untouched (the seed is the baseline). Readers keep using `lookup_country` unchanged (signup refusal of banned, 2.3; suggestion bar, 2.5).
- Group editing lives under the existing `catalogue` area; the country map under its own area (decision below); every change view in `AUDITED_VIEWS`.

- Decision (user, 2026-11-07): only the Sysadmin edits the country map: a new `country_map` area (Sysadmin change; Commercial Manager, Commercial Regional Manager and Financial Manager view), its own URLs; country groups stay under `catalogue` (Commercial Manager and Sysadmin change).
- Decision (user, 2026-11-07): the contract's state is an open lowercase code; a portal keeps a country's current row when its state is one it does not know (skipped, counted).
- Decision (user, 2026-11-07): release order 4.5 → 4.9 → 5.3; this story ships mytp-core 0.15.0 (1.12 has no core change).
- Decision (user, 2026-11-07): kept whole (~2000 tokens).

**Never:** no change to `lookup_country`'s signature or answers for an unchanged map, the 2.3 banned message, the resolver, signing/`sha`/`min_reader`, the regions guard rule; no bulk editing; no per-region different maps (one map for all regions).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Group edited | add FR to Iberia on admindev | FR priced as Iberia on dev and dev-ae after one sync | — |
| Overlap | add ES to Rest of EU while Iberia (active) has it | form error; nothing saved, no snapshot | — |
| Second catch-all | mark another group catch-all | form error | — |
| No group | US (in no named group) | Rest of world's subscription applies | — |
| Banned | set RU (or a test country) to banned on admindev | dev's signup refuses it after one sync | — |
| Moved | set a country from eu to ae | dev's `lookup_country` says ae after sync | — |
| Older admin | snapshot without `country_map` | portal keeps its current rows | — |
| Deactivated group | Iberia deactivated | ES falls to Rest of world (tested) | — |

</frozen-after-approval>

## Code Map

Paths relative to `.../code/`.
- Admin `catalogue/`: `models.py` `CountryGroup` :34 (docstring :35-38 "read-only" to update, `countries_text` :60); `migrations/0002_seed_country_groups.py`; `service.py` `_validate` :51, `_save` :55, `_create` :68, `_update` :79, `_set_active` :99, subscription functions :140-158 (pattern for `*_country_group`); `snapshot.py` `country_group_entry` :15, `section()` :33, registered in `apps.py` `ready()`; `views.py` `CountryGroupFilter` :81, `CountryGroupTable` :150, `CountryGroupListView` :194, `GROUPS = Kind(...)` (no form = read-only), generic `_create`/`_edit`/`_state_change` :354/:374/:398, `SubscriptionForm.clean` :240 (cross-field pattern); `urls.py` :34-38; `tests/test_catalogue.py` :363 `test_country_groups_are_read_only` (replace), :59 seed test (keep); `interface/config.py` `register_section` :67 (no change); `audit/registry.py` catalogue block :32-40; `lists/views.py` `AdminListView` :96; matrix `catalogue` rows unchanged (Commercial Manager change; Financial and Commercial Regional Managers view; Sysadmin all). The admin has no django-countries: names come from the seed migration.
- mytp-core: `contracts/catalogue.py` `CountryCode` :52, `CatalogueCountryGroup` :78, `CatalogueSection._consistent` :134 (rules :139-146; reader contract docstring :14-29 — unchanged); `contracts/config.py` `SNAPSHOT_READER` :41, `ConfigSnapshot` :56, `_without_absent_sections` :64 (extend), docstring :17-22; export list `contracts/__init__.py` (`SCHEMA_MODELS` :52), schema drift test `tests/test_contracts.py:314`; version bump.
- Portal `regions/`: `models.py` `Country` :18, `STATE_CHOICES` :15; `countries.py` `lookup_country` :94 (reads `Country` :102), seed sets :21/:40, `seed_state` :50; migration `0002_seed_country_map.py` (frozen `ISO_CODES`, 251 rows; copy codes/states/names for the admin seed); readers `signup/forms.py:304`, `signup/views.py:503`, `regions/suggestion.py:27`; guard `regions/tests/test_guard.py` (keep `eu`/`ae` literals inside `regions/`); `integration/config.py` `register_section_applier` :75 (names must be `ConfigSnapshot` fields), apply transaction :167; pattern `catalogue/applier.py` (but do NOT empty on None); `catalogue/resolver.py` `country_group` :34 (unchanged; add the deactivated-group test).
- Release: mytp-core 0.15.0 after 4.5 (0.13.0) and 4.9 (0.14.0), vendored in both sites; admin first. The new `country_map` area needs `permissions/areas.py`, `matrix.py` rows, `permissions/urls.py` `IMPLEMENTED_AREAS` and the matrix tests.

## Tasks & Acceptance

**Execution:**
- [x] mytp-core: `country_map` section model, `ConfigSnapshot` field, absent-section handling, schemas, tests, version, README.
- [x] Admin: group editing (service, form with the cross-row rules, views, urls, audit), `CountryState` model + seed migration + editor + snapshot section, README, tests.
- [x] Portal: `regions/applier.py` + registration, runbook, tests (matrix rows; absent section keeps rows; signup refuses a newly banned country).
- [x] Both sites: vendor the new core.

**Acceptance Criteria:**
- Given two Commercial Managers edit groups at once, when both save, then the published groups never break the cross-row rules.
- Given the first snapshot with a `country_map` equal to the seed, when dev applies it, then no `lookup_country` answer changes.

## Implementation Notes

Three repos, all on `story/5.3-country-groups`, nothing committed.

**mytp-core 0.15.0**
- New `contracts/country_map.py`: `CountryMapEntry {code: CountryCode, state}` (state: open lowercase code, `^[a-z][a-z0-9_]*$`, at most 32) and `CountryMapSection {countries}` (each code once). Exported from `contracts/__init__.py`.
- `ConfigSnapshot.country_map: CountryMapSection | None = None`. `_without_absent_sections` now loops over `OPTIONAL_SECTIONS = ('catalogue', 'country_map')`, so a snapshot without it dumps and signs as before. `SNAPSHOT_READER`, `min_reader` and the catalogue rules are unchanged.
- `config_snapshot.json` re-exported. New `tests/test_country_map.py` covers: absent means the same sha, the parse/dump round trip, unknown states accepted, invalid forms refused, the signed round trip and the schema. Version 0.15.0 in `pyproject.toml` and `__init__.py`; README updated (module list, contracts, schema files, snapshot, a new "Country map section", version history).

**Admin site**
- Country groups (area `catalogue`):
  - `catalogue.service` adds `create_/update_/deactivate_/reactivate_country_group`.
  - Every group write locks every group row by pk first (`_lock_groups`, then the row), then validates under that lock.
  - `group_rule_errors(countries, catch_all, others)` holds the two cross-row rules and is shared by the form and the service. A concurrent name clash is mapped to a `name` error.
  - `CountryGroup.clean()` uses `check_countries()`: alpha-2 codes, each once, all present in `CountryState`; a catch-all lists no countries.
  - `CountryGroupForm` takes codes typed as text ("ES, PT" or space-separated), normalises them to uppercase, sorted and unique, and enforces the same rules.
  - Views: create, edit, deactivate and reactivate through the existing generic `_create`/`_edit`/`_state_change`. URLs are under `/catalogue/country-groups/`, and all four views are in `AUDITED_VIEWS`.
- Country map:
  - New `country_map` app. `CountryState(code pk, name max 100, state max 32 with choices eu/ae/banned)`.
  - `0001_initial` comes from makemigrations. `0002_seed_country_map` holds the 251 portal seed rows (codes, states, django-countries 9.1.0 English names, plus AC "Ascension Island" and TA "Tristan da Cunha"); the longest name is 52 characters.
  - `service.set_state` writes in one transaction with the audit entry and `rebuild()`, and does nothing when the state is unchanged.
  - `snapshot.section()` returns `{countries: [{code, state}]}` by code and is registered in `apps.ready()`.
  - Pages: the list (`AdminListView`: search by code or name, state filter, CSV), the detail page with audit history, and the edit form with hx-confirm. URLs are `/country-map/`, `/country-map/<CC>/` and `/country-map/<CC>/edit/`. `country_map_edit` is in `AUDITED_VIEWS`.
- New area `country_map` ("Country map", Sales menu group, after Catalogue). In the matrix the Sysadmin has every action; the Commercial Manager, Commercial Regional Manager and Financial Manager have view only (scope all). It is added to `IMPLEMENTED_AREAS`, and the matrix, menu and audit-exercise tests are updated.
- Snapshot sections are now `('admin_directory', 'catalogue', 'country_map')`. As with the catalogue, a stored body gets the section on the next write after deploy.
- README: app table, mytp-core paragraph, configuration snapshot, catalogue (country group editing) and a new "Country map" section.

**Portal**
- `regions/applier.py`, registered in `regions.apps.ready()`:
  - Section present: listed rows are bulk-updated by code; missing codes are created; unknown states are skipped (row kept) and counted. The summary is `country map: <n> listed, <n> changed, <n> added, <n> skipped`.
  - Section absent (None): the rows are left untouched and no summary is added.
- Docstrings updated in `regions/models.py`, `regions/countries.py` and `integration/config.py`. The `deploy/README.md` runbook gets a new "Country map" section, and the configuration snapshot Apply row now says what an absent section means for each applier.
- Tests:
  - `regions/tests/test_applier.py`:
    - a seed-equal map changes no `lookup_country` answer, across all 251 codes;
    - a moved country (FR to ae);
    - a newly banned country is refused at signup step 1, then set back;
    - an absent section keeps the rows, both the seed and applied changes;
    - an unknown state is skipped;
    - a new code is added;
    - invalid maps give `invalid_body`;
    - a failing apply rolls back;
    - registration;
    - the log line holds counts only.
  - `catalogue/tests`: a deactivated Iberia sends ES to Rest of world; FR added to Iberia is priced as Iberia. The registered-names assertion becomes `('country_map', 'catalogue')`.

**Both sites:** the 0.15.0 wheel was built from mytp-core's working tree (`pip wheel`, `SOURCE_DATE_EPOCH` = HEAD commit time). It replaces 0.14.0 in `vendor/`, `requirements.txt` points to it, and it is installed in both venvs. The vendored-core tests pin 0.15.0 and check the `country_map` section. The admin `layout/tests/test_deploy.py` pin is updated too.

**Deviations / choices not spelled out in the plan**
1. The country map lives in a new admin app, `country_map` (model, service, views, snapshot, templates), not inside `catalogue`. The catalogue imports `CountryState` only to check group codes.
2. The `country_map` area sits in the Sales menu group, right after Catalogue.
3. New single-row checks on admin country groups (not cross-row, contract unchanged): codes must exist in the admin country map, and a catch-all group must list no countries. The detail page already shows a catch-all as "Every country in no other group".
4. Admin states are a fixed choice list (eu, ae, banned). The contract stays open.
5. The portal applier also creates rows for listed codes it has no row for (counted as "added"). It never deletes rows.
6. An absent section adds no log summary, so the existing applied-line format is unchanged when the admin site is older.
7. Lock order: all group rows by pk first, then the target row. This avoids deadlocks between two concurrent group edits.
8. The two-Commercial-Managers acceptance test (`test_concurrent_group_edits_never_break_the_rules`) is PostgreSQL-only, like the existing row-lock tests. It is skipped on SQLite. It was also run against a throwaway local PostgreSQL 17 (see Verification).
9. Portal section-applier order is `country_map` then `catalogue` (INSTALLED_APPS order). They are independent.

## Plan Change Log

## Review Triage Log

Opus reviews, one per repo (2026-11-08); no blocking or medium code defects:
- patch (core, low): docs said ISO alpha-2 validation; the contract checks the shape only → docs fixed; state length vs the portal column noted.
- patch (admin, low): group writes on an empty table locked nothing → transaction advisory lock first on PostgreSQL.
- patch (admin, low): race tests extended to reactivate-vs-update and create-vs-update; country search test made able to fail; ASCII-only code input.
- patch (portal, medium test strength): failing-apply test now proves the map is rolled back with the snapshot; seed-equal test asserts the summary; partial-map and unknown-state-new-code tests.
- patch (portal, low): INSTALLED_APPS comment; runbook check covers moving a country between regions on dev and dev-ae.
- defer (portal, low): a registration that passed step 1 before a ban can still finish within 24 h; the signup readers are frozen here → follow-up story.
- accepted deviation: the portal applier adds a row for a listed code it has none for (upsert; never deletes).

## Verification

**Review patches (2026-11-08, coordinator's review of the first pass)**
1. Core: the `country_map.py` docstring and the README "Country map section" now say the contract checks the code's shape (two uppercase letters), not the ISO list. The admin restricts codes to its seeded rows, where AC and TA are reserved codes.
2. Core: a comment by `StateCode`, and a README line, note that the portal's `Country.state` column is 16 characters, so a longer future state needs a portal migration first. Only the docs changed; the wheel was rebuilt and re-vendored in both sites and reinstalled in both venvs. The vendored files' sha256 is `5d74b614…dfdf1` in both.
3. Admin: every group write first takes `pg_advisory_xact_lock(GROUP_WRITE_LOCK = 5_303_001)` (PostgreSQL only; a no-op on SQLite), then the group row locks. This covers a write when no group rows exist. The ConfigSnapshot row is not locked early: `rebuild()` still takes it last.
4. Admin: the PostgreSQL barrier test is parametrized over three races: update vs update, reactivate (an inactive group holding US) vs update adding US, and create with US vs update adding US. Each asserts exactly one saved, one `ValidationError`, `errors == []`, a single active holder of US, and a valid snapshot.
5. Admin: the country map search test proves code search (`'FR'` finds France, not Spain) and that the search filters.
6. Admin: `normal_countries` requires ASCII `[A-Za-z]{2}` before uppercasing (`ß` and `ıd` are refused). `CountryGroupForm.clean_countries` turns that error into a field error. New tests cover the service and the form.
7. Portal: `test_a_failing_apply_keeps_the_previous_map` keeps the real `country_map` applier and makes the `catalogue` applier raise. The DE change is rolled back and the version stays 1.
8. Portal: the seed-equal test asserts `country map: 251 listed, 0 changed, 0 added, 0 skipped`. A new partial-map test (only FR changed) asserts the other 250 rows are unchanged and the line reads `1 listed, 1 changed, 0 added, 0 skipped`.
9. Portal: the unknown-state test also sends `('XK', 'quarantined')` and asserts no XK row is created.
10. Portal: the INSTALLED_APPS comment in `mytreatplan/settings.py` now names `regions.applier` and the snapshot's `country_map`.
11. Portal: the `deploy/README.md` country map staging check uses AQ (Antarctica), says one map hits every region at once, and has three steps: banned on dev, moved to ae and checked with `lookup_country` on both dev and dev-ae, then set back to eu.
12. The signup readers are unchanged (frozen).

**Follow-up (later story):** a pending registration that passed step 1 before its country was banned can still finish within its 24 h life, because the ban is checked only at step 1 (`AccountForm.clean_country`). A later story should re-check `lookup_country(...).banned` at the final step, or when the account is created.

**Verification results (2026-11-08, after the review patches, sandbox off):**
- mytp-core (`PYTHONPATH=. ../.venv/bin/python -m pytest`): 1131 passed.
- Portal on SQLite: 2191 passed, 5 skipped (PostgreSQL row-lock tests), 3 failed (the known PostgreSQL-only failures in `accounts/tests/test_crypto.py`).
- Portal on a throwaway local PostgreSQL 17: 2199 passed, 0 skipped, 0 failed.
- Portal `manage.py check`: no issues. `makemigrations --check`: no changes.
- Admin on SQLite: 963 passed, 8 skipped (PostgreSQL row-lock tests, including the 3 new group-race tests).
- Admin on a throwaway local PostgreSQL 17: 971 passed, 0 skipped. The 3 group-race tests also passed 5 repeated runs.
- Admin `manage.py check`: no issues. `makemigrations --check`: no changes.
- Column lengths: the longest seeded name is 52 characters (column 100, checked by a test). Every test state goes through `full_clean`, a form or the portal applier's known-state filter before a write, so nothing longer than its column is written. The portal `Country.state` (16) only ever receives eu/ae/banned. The PostgreSQL runs above enforce every varchar limit.

**Commands:**
- mytp-core, admin and portal suites — all pass.

**Manual checks (staging):**
- On admindev: add a country to Iberia → dev and dev-ae price it as Iberia (`price_catalogue`); an overlapping edit is refused; set a test country to banned → after one sync dev's signup country check refuses it (test client), then set it back; a country in no named group falls in Rest of world.
