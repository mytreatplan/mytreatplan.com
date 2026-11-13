---
title: 'Snapshot rules for the catalogue'
type: 'feature'
ticket: '11'
created: '2026-11-12'
status: 'done'
baseline_revision: '2e3041e404c37ed1f635eb1e432b60baaac53877'
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

**Problem:** C10 (AD-3, AD-6, AD-16, AD-19): the rules by which the catalogue travels are only partly enforced. The portal's catalogue tables refuse an instance `save()` or `delete()`, but a queryset `update()`, `delete()` or `bulk_create()` outside the snapshot apply goes through, and the country map and admin directory tables are not guarded at all. "Never deleted" on the admin site is a habit of the services, not something the models refuse. Which objects are region-specific, and what a peer without a region gets, is written nowhere.

**Approach:** Write the rules down and enforce each one with code and a test: one guard on the portal that refuses every write to a snapshot-fed table outside the snapshot apply; one guard on the admin site that refuses deleting a published object; the per-region rule stated once (an object with a region goes only to that region's portal) with its tests; a reader for every object type whatever its state.

## Boundaries & Constraints

**Always:**
- An object that carries a region is served only to the portal of that region, and a portal skips one that names another region (today: vouchers).
- A published object is deactivated, never deleted; a deactivated one is still sent and the portal can read it by its id or code.
- The portal writes a snapshot-fed table only inside the snapshot apply; every other write path fails.
- The snapshot contract, its version rules, `min_reader` and the section names stay as they are.

**Never:** no change to prices or to what is offered (entry 12); no new region field on groups, products, subscriptions, companies or rules; no redemption; no change to the order of the sections or to the pull schedule.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Voucher of ae | a voucher of region ae on admindev | in the body served to dev-ae; absent from the one served to dev | — |
| Deactivated object | a product, subscription, add-on, group, company, rule, provider product or voucher deactivated | still in the snapshot, inactive; no longer offered on dev; read by id or code on dev | — |
| Write outside the apply | `save`, `delete`, `objects.create`, queryset `update` / `delete`, `bulk_create`, `bulk_update` on any snapshot-fed portal table | — | refused with the read-only error |
| Write inside the apply | the snapshot apply | rows replaced as today | — |
| Delete on the admin site | instance or queryset delete of a published object | — | refused; the row stays |
| Peer without a region | a signed request from a site no region names | — | refused with 403 and logged; the peer keeps its last snapshot |
| Past orders | a deactivated country group or billing rule | read on the portal by its id, whatever its state | an unknown id gives nothing |
| Tests that need rows | a test of the portal that needs catalogue rows | written through the snapshot apply or a helper that only tests can import | — |

## Decisions

- Decision (user, 2026-11-12): one stored body and one version for every region, filtered when served; a portal applies the same content again when only another region changed. No handoff to 4.4.
- Decision (user, 2026-11-12): every region must have its own country groups, companies, products and subscriptions as well as its vouchers, and each portal must receive only its own. This needs a region written on each country group (subscriptions and rules follow their group, companies their rules, products their subscriptions; Rest of world becomes one group per region) and mytp-core 0.25.0: it is built as its own story, after this entry and before the staging seed (entry 13). This entry enforces the rule for what carries a region today (vouchers) and leaves the filter ready for that story.
- Decision (user, 2026-11-12): a peer whose site no region names is refused (403, logged).
- Decision (user, 2026-11-12): the portal refuses queryset `update`, `delete` and the bulk methods on a snapshot-fed table unless the snapshot apply is running, on top of the instance guard.
- Decision (user, 2026-11-12): the guard covers the nine catalogue tables, the country map (`regions.Country`) and the admin directory.
- Decision (user, 2026-11-12): by-id readers that ignore state for country groups and billing rules.
- Decision (user, 2026-11-12): on the admin site the published models refuse instance and queryset deletes; a test lists every published model.
- Decision (user, 2026-11-12): replace-at-apply is kept: a row absent from the snapshot is deleted on the portal; the admin-side guard is what keeps rows from vanishing.
- Decision (user, 2026-11-12): one plan; admin PR, then portal PR; no mytp-core release. Merge and staging delegated.

</frozen-after-approval>

## Code Map

- Repositories: this plan is in the portal (`mytreatplan.com`); the admin site is `../mytp-admin` (baseline 53d93f3733708cda2575eb9ae0157c9d195b08ca); mytp-core `../mytp-core` (4cc569cd84fa1bdb038a6dedf8e8102832d6b743, v0.24.0) is not changed: do not touch it. The branch `story/5.11-snapshot-rules` is checked out in the portal and the admin site: work there, commit nothing, push nothing, tag nothing. Suites: each README; portal venv `../.venv`, admin venv `/private/tmp/claude-501/admin-venv2`. The portal has 3 known PostgreSQL-only failures on SQLite. A test marked `django_db(transaction=True)` (PostgreSQL only, skipped locally) must create its own rows (`get_or_create`): the migration-seeded rows are gone there. Search by name when a line number is off.
- Portal: `catalogue/models.py` `ReadOnlyModel` :47-62 (blocks `save` / `delete` only); `catalogue/applier.py` `apply` :131-203, `apply_provider_costs` :206-224, `apply_vouchers` :227-245; `integration/config.py` `_apply` :194-222 (admin directory :207-212); `regions/applier.py:22-40`, `regions/models.py:19` `Country`; `integration` `AdminDirectoryEntry`; `catalogue/resolver.py` (active-only: `country_group` :164, `billing_rule*` :190-215). Tests: `catalogue/tests/test_catalogue.py:264` `test_no_write_path_outside_the_snapshot_apply`, `test_add_ons_costs.py:375`, `test_vouchers.py:138`, `test_billing.py:162`; `test_billing.py:348-349` deletes rows with a queryset outside the apply.
- Admin: `interface/views.py:41-68` `config()` (region :54); `interface/config.py` `rebuild` :129-152; published models `catalogue` (`CountryGroup`, `Product`, `Subscription`, `AddOn`, `ProductCostLink`), `billing` (`BillingCompany`, `BillingRule`), `providers` (`ProviderProduct`, `ProviderPrice`), `vouchers.Voucher`, `country_map.CountryState`; reverse migrations `catalogue 0002`, `country_map 0002` delete rows (historical models, not guarded). Tests: `interface/tests/test_config.py:332-353`.

## Tasks & Acceptance

**Execution:**
- [x] Admin (`interface/views.py`, tests) -- `config()` refuses a peer whose site no region names with 403 and a log line, before signing anything; the per-region filter stays one call so the later story adds its lists in one place.
- [x] Admin (a shared guard, the published models of `catalogue`, `billing`, `providers`, `vouchers`, `country_map`, README, tests) -- instance `delete()` and queryset `delete()` refused on every published model; rows that are by design replaced (a subscription's product links) keep working; a test walks the snapshot builders' models and fails when a published model lacks the guard; a test per section that a deactivated row is still in the body.
- [x] Portal (`integration/config.py`, a shared read-only base used by `catalogue/models.py`, `regions/models.py` `Country`, `integration` `AdminDirectoryEntry`, the three appliers) -- a marker that is set only while `_apply` runs; a manager and queryset that refuse `update`, `delete`, `bulk_create`, `bulk_update` (and the instance paths) outside it, with the read-only message; no migration with a schema change expected.
- [x] Portal (tests) -- one parametrised test over every snapshot-fed model and every write path, outside the apply (refused) and inside (allowed); a test that fails when a model fed by an applier lacks the guard; none of them registered in the Django admin; existing tests that write these tables directly (`catalogue/tests/test_billing.py:348-349`, tests that create `Country` or directory rows) move to the snapshot apply or to a helper under a `tests` package that production code cannot import.
- [x] Portal (`catalogue/resolver.py`, `price_catalogue`, tests) -- `country_group_by_id` and `billing_rule_by_id`, whatever the state; a test per object type that a deactivated row is read by id or code and is not offered.
- [x] Both READMEs -- the rules of the snapshot in one short section each: what is region-specific, never deleted, read-only on the portal, a peer without a region refused.

**Acceptance Criteria:**
- Given a voucher of region ae, when dev pulls, then dev has no row for it and logs no skipped voucher.
- Given a product, a billing rule and a country group deactivated on admindev, when dev pulls, then each is read by id on dev and none is offered.
- Given any snapshot-fed portal table, when a row is written by any ORM path outside the snapshot apply, then the read-only error is raised and the table is unchanged.
- Given a published object on the admin site, when it is deleted by instance or queryset, then the delete is refused.
- Given a signed request from a site no region names, then 403 and no body.

## Implementation Notes

- Portal guard: `integration/snapshot_tables.py` (`SnapshotFedModel`, `SnapshotQuerySet`, `ReadOnlyTable`). It lives in `integration` because the three apps import it and it imports only Django. `catalogue.models.ReadOnlyModel` is gone; the nine models, `Country` and `AdminDirectoryEntry` subclass the new base. No schema migration; the base-manager options of review P1 need options-only migrations (below).
- `ReadOnlyTable` subclasses `TypeError`, so the existing `pytest.raises(TypeError)` tests keep their meaning. The message is now one for every table and still says "read-only". `catalogue.models.READ_ONLY_MESSAGE` is re-exported.
- The marker is a `contextvars.ContextVar` set by `_snapshot_apply()`, a context manager with `try/finally`. Each thread starts with it unset, and a failing apply resets it. `_apply` sets it only around the admin-directory writes and the section appliers. The version check and the state row's save sit outside it.
- Inside the apply every path is allowed, instance `save`/`delete` included, so the parametrised test can show "allowed inside" for all nine paths.
- Not guarded, and written in the module docstring and the README: raw SQL, private queryset methods (`_update`, `_raw_delete`, `_insert`), raw deserialisation (`save_base(raw=True)`: `loaddata`, `serialized_rollback`; review P2, pinned by `test_raw_deserialisation_is_not_guarded`) and historical models.
- Review P1: each of the eleven models sets `Meta.base_manager_name = 'objects'` in its own Meta. An abstract base's Meta is not inherited by a child that declares its own Meta, and every one of them does. This closes the reverse related managers' `add()` / `set()` and `Model._base_manager` writes. Migrations: `catalogue 0007`, `integration 0010` and `regions 0003` (`guarded_base_manager`), AlterModelOptions only; `sqlmigrate` prints `(no-op)` for each. Historical models keep a plain base manager. Checked as still working: instance save and cascading delete inside the apply, the three appliers (a full pull), `refresh_from_db`, related-object reads, raw deserialisation, and migrating the test database from empty. The related-manager test wraps the refused write in `transaction.atomic()`: `add(bulk=False)` writes in an atomic block without a savepoint, so its refusal would otherwise break the test's transaction. Production callers would see the same (the refusal aborts the surrounding transaction), which is the intended outcome.
- The test-only helper is `integration/tests/snapshot_rows.py` (`snapshot_writes`, `put_country`, `ensure_country`). `test_production_code_never_imports_a_tests_package_or_sets_the_marker` checks it by parsing every module outside the `tests` and `migrations` folders. The test fails if any of them imports a `tests` package or `snapshot_rows`, or names `_snapshot_apply`/`_applying`; `integration/config.py` and the guard module are the only allowed exceptions.
- Moved tests: `test_billing.py`, where the simulated earlier release's empty tables and its raw group delete now run under `snapshot_writes()`; `signup/tests/test_country_check.py::_ban`; `accounts/tests/test_user.py`; and the two browser fixtures (`signup/tests/browser/test_country_check.py`, `mytp_publicsite/tests/browser/test_region_bar.py`). No test wrote admin-directory rows directly.
- The "every applier-fed model has the guard" test records the INSERT/UPDATE/DELETE statements a full pull runs while the marker is set. It maps their tables to models and requires that set to equal the listed eleven, each one guarded. A second test requires every `SnapshotFedModel` subclass to be on that list.
- Resolver: `country_group_by_id`, `billing_rule_by_id` (with the company loaded) and `provider_product_by_id` (with costs). The last one was added so that every object type has a by-id-or-code reader. `price_catalogue` gained `--country-group <id>` and `--billing-rule <id>`, each used alone.
- Admin guard: `interface/published.py` (`PublishedModel`, `PublishedQuerySet`, `PublishedDeleteRefused`), in `interface` because it owns the snapshot. Guarded: `CountryGroup`, `Product`, `Subscription`, `AddOn`, `ProductCostLink`, `BillingCompany`, `BillingRule`, `Provider`, `ProviderProduct`, `ProviderPrice`, `Voucher`, `CountryState`. `Provider` is guarded too, because the providers builder reads it (its state makes its products inactive) and the walk test found it. No migration.
- The admin walk test records the tables `build_sections()` reads (FROM/JOIN). It leaves out the auto-created many-to-many table (a subscription's product links, replaced by design) and allows `staff.User` under its own `HardDeleteRefused`. Every other model it finds must be guarded, and the set must equal the listed twelve.
- `test_clones.py`: the source product's delete is now refused by the guard. The test still checks that the clone's PROTECT foreign key would stop it, through Django's `Collector`.
- The 403: `interface.views.config` looks up the region before `current()`, so nothing is built or signed for that peer. The answer is `forbidden` with "No region names this site.". The view writes a warning line on `interface.config`, and the call log records `no_region` (iss, kid). `for_region(body, region)` is the one filter call.
- Review A1/A2: a site no region names gets 401 `unknown_key` at the signature check, because `calls.keyring()` holds only keys of regions with a site and mytp-core's `verify_request` refuses an unknown (iss, kid). New test `test_a_site_no_region_names_gets_401_at_the_signature_check`. The 403 branch is kept for a region that loses its site between the keyring and the lookup. Its test simulates exactly that by clearing the site inside the keyring call. It deletes the stored snapshot row first and records calls to `views.config_snapshot.current`, then asserts no call and no row afterwards.
- Ended cost links: still not sent (Design Notes), so the admin's "deactivated row is still in the body" tests cover groups, products, subscriptions, add-ons, companies, rules, provider products (also through a deactivated provider), vouchers and a banned country, not ended links.

## Plan Change Log

- 2026-11-13 (review): the acceptance criterion "a signed request from a site no region names, then 403" is met earlier than written: such a site is not in the keyring and gets 401 at the signature check; the 403 stays for a region that loses its site during a request. The portal guard needs three options-only migrations (no SQL), where the task expected none.

## Review Triage Log

Quick lens, one reviewer per repository (2026-11-12). 7 findings: 1 medium, 6 low; 6 patched, 1 rejected.

| # | Repository | Finding | Verdict | Evidence | Route |
|---|-----------|---------|---------|----------|-------|
| 1 | portal | a related manager's `add()` / `set()` writes past the guard | medium | Django writes through `_base_manager`, a plain manager; the reviewer moved a provider cost to another product outside the apply | patch: `base_manager_name = 'objects'` on the eleven models, three options-only migrations, tests per reverse relation and for the base manager |
| 2 | portal | `loaddata` writes past the guard, not documented | low | `save_base(raw=True)` skips `save()`; a fixture stored two rows | patch: documented and pinned by a test; not guarded, Django's `serialized_rollback` uses the same path |
| 3 | portal | the README says every other ORM write is refused | low | it left out the documented exceptions | patch: the README names them |
| 4 | admin | the 403 cannot be reached by a site no region names | low | the keyring holds only keys of regions with a site: such a site gets 401 `unknown_key` | patch: README and docstrings corrected, a test of the 401, the 403 test reproduces the race |
| 5 | admin | the 403 test does not prove nothing was built | low | `current()` builds only when no row exists, and a row existed | patch: the test deletes the row and records the calls |
| 6 | admin | the comment listing the refusal codes misses `no_region` | low | `interface/models.py` | patch |
| 7 | admin | deletes through `_base_manager` or a future cascade pass the guard | low | true; every foreign key into a published model is PROTECT today | rejected: no path exists; written in the module |

## Design Notes

- Ended cost links are not sent (only the links in force); an order line keeps its own cost (AD-6). Unchanged.
- A row the portal skips (unknown category, currency or tier) is not readable there. Unchanged.

## Verification

**Manual checks (staging):**
- A voucher of ae never appears on dev (table and log); an object deactivated on admindev is read on dev by id or code and no longer offered; a write through `manage.py shell` on dev to a catalogue table is refused.

## Staging

Checked 2026-11-13 (merge and staging delegated by the user).

- Release: no mytp-core release (v0.24.0 stays). Admin PR #35 (merge e77d45b; master run 43340101847, tests and deploy green). Portal PR #93 (merge c18e566; master run 43340877098, tests and both deploys green). Suites: admin 1529, portal 2815 plus the 3 known PostgreSQL-only failures on SQLite.
- After the portal deploy: the first `pull_config` on dev and dev-ae printed `applied` (v103), the second `unchanged`; no migration left to apply.
- admindev, in `manage.py shell`: deleting a voucher, a product, a billing rule and a country group, by row or by queryset, was refused (`PublishedDeleteRefused`); the counts did not change.
- admindev, as `staging_check_5_11` through the service: the product STG55-EN, the rule Rest of world × odb and the group Rest of EU deactivated (v104 to v106); each stayed in the snapshot body as inactive.
- dev (v106): `price_catalogue --product`, `--billing-rule` and `--country-group` read each by its id with "status: inactive"; STG55-EN was not among the ES basic offers, US odb had no billing rule and DE basic had no offers.
- dev, in `manage.py shell`: `save`, `delete`, queryset `update` and `delete`, `bulk_create`, the base manager's `update` and a related manager's `add()` on catalogue tables, the country map and the admin directory were each refused (`ReadOnlyTable`); the row counts did not change.
- The voucher of ae: not in dev's table (3 rows, the three eu codes), "no voucher with this code" for the resolver, and no "voucher(s) of another region skipped" line in the host's log; dev-ae has it, with the doctor's UUID, and no eu code.
- Reactivated on admindev (v107 to v109): dev shows the three as active, DE basic offers again and STG55-EN is back among the ES basic offers.
- Not checked on staging: the 403 for a region that loses its site during a request and the 401 for a site outside the keyring (tests only); the admin pages in a browser (2FA).
- Staging data: nothing new left; the three objects are as before.
