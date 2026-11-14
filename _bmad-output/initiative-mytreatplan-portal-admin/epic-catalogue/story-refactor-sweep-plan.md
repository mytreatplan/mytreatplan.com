---
title: 'Refactor sweep (epic-catalogue)'
type: 'refactor'
ticket: '15'
created: '2026-11-13'
status: 'built'
baseline_revision: '442f745ff5c3868024613c7eeb2a0941d7b39f5a'
route: 'full'
route_source: 'auto'
risk: 'low'
review: 'quick'
review_source: 'auto'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Sixteen catalogue entries left duplicated test fixtures, a double lazy import, one dead name, helpers that billing, providers and vouchers import from `catalogue` (one of them private), stale comments, and deferred findings that `deferred-work.md` does not show as closed.

**Approach:** A behaviour-neutral cleanup of the portal and mytp-admin, with no mytp-core release: move shared test fixtures to conftest, simplify the pricing import, remove dead code, give the shared admin helpers a public home, fix stale comments, and append status entries to `deferred-work.md` for what is closed and what is recorded and left.

## Boundaries & Constraints

**Always:** No behaviour change, no wire or snapshot change, no migration, no mytp-core change. Both suites pass with the same test count as the baseline, except tests removed with dead code, each named in the notes. `deferred-work.md` is append-only.

**Never:** Removing the legacy `tps` / `other` reading or the two former TPS discount fields (they need a core contract change); splitting `price_catalogue`; squashing migrations; moving the staging seed; removing resolver readers that only tests use today (they are the reader API for ordering and payments).

**Decisions (user, 2026-11-14):**
- The copied audited-write and view helpers of mytp-admin (catalogue, billing, providers) are recorded in `deferred-work.md` and left.
- Of the shared admin helpers, only `_fixed_region` becomes public (`fixed_region`); the rest stay in `catalogue`.
- The core contract cleanup (the two former TPS discount fields, the legacy `tps` code, the stale `money.py` docstring, the portal's copy of core's voucher checks) is one `deferred-work.md` entry; no ticket now.
- The two small portal items are done: `BillingRule.vat_text` goes (with its one test assert) and the add-on percents/amounts lists are built in one place.
- Merge and the staging smoke check are delegated.

</frozen-after-approval>

## Code Map

Portal:
- `integration/tests/test_config.py:41,57,112` (`admin`, autouse `interface_settings`, `snapshot`, `ADMIN`) imported by ten test modules with `# noqa F401/F811`; `pull()` copied in `catalogue/tests/test_catalogue.py:77`, `test_snapshot_rules.py:44`, `test_vouchers.py:42`, `regions/tests/test_applier.py:36`; `ae_portal` in `catalogue/tests/test_billing.py:135`, `test_regions.py:96`; `integration/tests/test_snapshot_tables.py:23-25` imports catalogue tests -- move to conftest / `integration/tests/snapshot_rows.py`. Caution: `interface_settings` is autouse only where it is imported today; keep it scoped to the same modules.
- `catalogue/resolver.py:166-178` (`__getattr__`, `_PRICING_NAMES`) and `catalogue/pricing.py:193-195` (`_resolver()`) -- lazy on both sides; keep one, the `resolver.X` names stay.
- `catalogue/models.py:54` -- `READ_ONLY_MESSAGE` re-export, unused.
- `catalogue/models.py:465-470`, `management/commands/price_catalogue.py:625`, `pricing.py:333-337` -- VAT text built three ways (question 4); `resolver.py:411-415`, `pricing.py:243-246` -- add-on lists.

Admin:
- `interface/tests/test_published.py:17,20,21`, `catalogue/tests/test_add_ons_costs.py:26`, `catalogue/tests/test_regions.py:26`, `catalogue/tests/test_seed_staging_catalogue.py:20` -- cross-app test imports (fixtures `dev`, `dev_ae`, `vouchers`, `AE_PORTAL`), against 3.13's rule -- move to conftest.
- `billing/service.py:142,152,368` import `region_of`, `_fixed_region`, `plain_text` from `catalogue.service`; `catalogue/views.py:167,180,196,346,372,397,839` shared view helpers (question 2).
- `catalogue/models.py:8` -- stale comment "VAT comes at checkout, a later entry".

Docs:
- `_bmad-output/initiative-mytreatplan-portal-admin/deferred-work.md` -- append: 5.6 finding #7 closed by `plan-reapply-snapshot-after-deploy.md`; 5.3's banned-country re-check closed by `plan-signup-banned-country-recheck.md`; the items recorded and left (questions 1 and 3, `price_catalogue` size).
- Model: `epic-admin-access` 3.13's sweep plan.

## Tasks & Acceptance

**Execution:**
- [x] Portal `integration/tests/` + `catalogue/tests/` + `regions/tests/` -- move `admin`, `interface_settings`, `snapshot`, `ADMIN`, `pull`, `ae_portal` to conftest files or `integration/tests/snapshot_rows.py`; remove the cross-module imports and their `noqa` -- one copy of each; `interface_settings` stays autouse for exactly the modules that have it today.
- [x] Portal `catalogue/resolver.py`, `catalogue/pricing.py` -- keep one lazy side; every `resolver.<pricing name>` still resolves.
- [x] Portal `catalogue/models.py` -- remove `READ_ONLY_MESSAGE` re-export and `BillingRule.vat_text` (and its assert in `catalogue/tests/test_billing.py:220-221`).
- [x] Portal `catalogue/resolver.py`, `catalogue/pricing.py` -- one helper for the add-on percents/amounts lists.
- [x] Admin `interface/tests/`, `catalogue/tests/` -- move the fixtures `dev`, `dev_ae`, `vouchers`, `AE_PORTAL` and the providers helper to conftest; no test module imports another app's test module.
- [x] Admin `catalogue/service.py`, `billing/service.py` -- rename `_fixed_region` to `fixed_region` at every use.
- [x] Admin `catalogue/models.py:8` -- correct the stale VAT comment (VAT is the portal's pricing function, entry 12).
- [x] `_bmad-output/initiative-mytreatplan-portal-admin/deferred-work.md` (portal repo) -- append: 5.6 finding #7 closed; 5.3 re-check closed; recorded and left: the admin helper copies, the core contract cleanup, `price_catalogue`'s size.

**Acceptance Criteria:**
- Given the baseline test counts of both suites, when the suites run after the sweep, then the same tests pass (the count differs only by what the notes name).
- Given both repos, when `grep` looks for a test module importing from another test module in the touched apps, then it finds none.
- Given `git diff --stat` of each repo, when read, then no migration, no template, no `vendor/` file and no mytp-core file changed.

## Working notes for the implementer

Branch `story/5.15-refactor-sweep` already exists in the portal and in `../mytp-admin`: do not create another branch, do not commit. mytp-core is not touched. Baselines: portal 442f745ff5c3868024613c7eeb2a0941d7b39f5a, mytp-admin e1e435a3c63f31197f842fe79cb0bb6270e1b9a4. Record the baseline test counts before the first change. Fill Implementation Notes for both repos in this plan.

## Implementation Notes

**Baselines (before the first change, SQLite, the Verification commands):** portal 2894 passed, 8 skipped, 3 failed (the known PostgreSQL-only `accounts/tests/test_crypto.py` three); mytp-admin 1582 passed, 12 skipped.

**After:** portal 2894 passed, 8 skipped, the same 3 known failures (one run also failed `mytp_publicsite/tests/test_home.py::test_home_loads_only_local_assets`, untouched by this change: it asserts `'cdn' not in html.lower()` on a page with a random token, passes alone and on the re-run); mytp-admin 1582 passed, 12 skipped. No test removed: the only removal is the two `vat_text` asserts inside `test_each_country_resolves_to_its_company_vat_and_gateways` (test_billing), with the property. `makemigrations --check --dry-run`: no changes in either repo. No migration, template, `vendor/` or mytp-core file changed.

**Rule applied for "test module importing another test module":** 3.13's, as the admin task states it: no test module imports another app's test module (`tests/test_*.py`); helper modules (`integration/tests/snapshot_rows.py`, `signup/tests/helpers.py`, the root conftests) may be imported. Imports between test modules of one app stay (e.g. `catalogue.tests.test_catalogue.run`).

Portal:
- `integration/tests/snapshot_rows.py` now holds the fake admin site and the snapshot (`ADMIN_SEED`, `PORTAL_SEED`, `ADMIN`, `PORTAL`, `CONFIG_URL`, `ADA`, `BOB`, `snapshot`, `FakeAdmin`, `state`, one `pull(admin, version, section=None, *, key='catalogue', **changes)`), and the catalogue sections more than one app's tests send (moved, unchanged, from catalogue's test modules: `IBERIA`, `WORLD`, `group`, `product`, `full_subscription`; billing's catalogue `EU` … `COMPANIES`, `rule`, `rules`, `billed`; the add-ons and provider costs `BYTE_SYNC` … `ADD_ONS`, `cost`, `provider_product`, `COSTS`; `TRY_ME`, `voucher`, `GENERAL`). They had to leave the catalogue test modules because `integration/tests/test_snapshot_tables.py` imported them.
- Root `conftest.py`: the `admin` fixture and a non-autouse `interface_settings`. `catalogue/tests/conftest.py` (new) makes it autouse for every catalogue module (all eight imported it) and holds the one `ae_portal`; `integration/tests/test_config.py`, `test_snapshot_tables.py` and `regions/tests/test_applier.py` use it module-wide with `pytest.mark.usefixtures`. Modules with their own `interface_settings` / `admin` (test_hub, test_ping, test_outbox, test_remote) are unchanged and override them.
- `pull`: one copy; test_vouchers binds `key='vouchers'` and test_applier `key='country_map'` with `functools.partial`; test_snapshot_rules calls `pull(admin, n, **sections)`. Every `# noqa: F401/F811` that the imported fixtures needed is gone.
- `catalogue/pricing.py` imports `catalogue.resolver` at module level (`_resolver()` and its six calls removed); the resolver keeps its PEP 562 `__getattr__`, so `resolver.<pricing name>` still resolves (`test_the_resolver_gives_every_pricing_name`).
- `resolver.add_on_terms(add_ons)` (in `__all__`) builds the `percents` / `amounts` lists for `resolver.price` and pricing's list amount.
- `catalogue/models.py`: `READ_ONLY_MESSAGE` re-export and `BillingRule.vat_text` removed; the module docstring's stale "VAT comes at checkout, a later entry" (also an over-long line) corrected like the admin's.
- `deferred-work.md`: five entries appended (two closed, three recorded and left).

mytp-admin:
- Root `conftest.py`, following 3.13 (fixtures, and plain functions imported with `from conftest import …`): the portals as readers (`ADMIN_SEED`, `PORTAL_SEED`, `ADMIN`, `PORTAL`, `AE_PORTAL`, `CONFIG`, `ACTOR`, a non-autouse `interface_settings`, the fixtures `dev`, `dev_ae`, `vouchers`, and `signed_get`, `admin_ring`, `served`), and the rows written through the services (`CASE_CATEGORIES`, `FISCAL`, `SPAIN`, `EXEMPT`, `group`, `english`, `product`, `subscription`, `add_on`, `provider`, `provider_product`, `price`, `costed`, `company`, `rule`), moved unchanged from interface's test_config, catalogue's test_catalogue and test_add_ons_costs, providers' and billing's test modules.
- `interface/tests/test_config.py` and `catalogue/tests/test_regions.py` (the two modules where `interface_settings` was autouse) use it with `pytest.mark.usefixtures`.
- `fixed_region` (was `_fixed_region`) in catalogue.service, at its three uses and billing.service's import and use.
- `catalogue/models.py`: the VAT sentence corrected.

Left (outside the touched apps, recorded here rather than changed): cross-app test imports `audit/tests/test_audit.py:1221` (from `interface.tests.test_remote`) and `contact_messages/tests/test_contact_messages.py:17,147` (from `clients.tests.test_clients`) in mytp-admin; `accounts/tests/test_login_code.py:643` (from `signup.tests.test_twilio_verify`) and `eventlog/tests/test_retention.py:19` (from `mytp_publicsite.tests.test_monitoring`) in the portal.

## Plan Change Log

- 2026-11-14 (implementer): the shared catalogue sections moved to `integration/tests/snapshot_rows.py` too (not only the fake admin site), since test_snapshot_tables imported them from catalogue's test modules; mytp-admin's cross-app builders of `interface/tests/test_published.py` (billing's `company`/`rule`, catalogue's `ACTOR`/`group`/`product`/`subscription`/`add_on`/`costed`) and catalogue test_regions' `interface_settings`/`served` moved to the root conftest with the named fixtures, to meet "no test module imports another app's test module". The portal `catalogue/models.py` docstring got the same VAT correction as the admin's.

## Review Triage Log

Quick lens, one reviewer per repository, one pass (2026-11-14): 2 findings (portal), 0 high, 0 medium, 2 low, 0 false; 1 patched, 1 rejected. The admin reviewer found nothing (same 503 tests collected in the touched modules at the baseline and after; no `_fixed_region` left).

| # | Repo | Finding | Verdict | Action |
|---|------|---------|---------|--------|
| 1 | portal | Read word for word, the second acceptance criterion is not met: test modules of `catalogue/tests` still import plain helpers (`run`, builders, `offered`) from `test_catalogue` and `test_billing` of the same app | low | rejected: the rule applied is 3.13's (no test module imports another app's test module; same-app helper imports stay), which the Implementation Notes state; moving those helpers is more than a direct correction. Told to the user |
| 2 | portal | `catalogue/tests/test_vouchers.py:8`: `from functools import partial` out of alphabetical order | low | patched |

Full suites after the patch (SQLite): portal 2894 passed, 8 skipped, 3 failed (the known PostgreSQL-only ones in `accounts/tests/test_crypto.py`); mytp-admin 1582 passed, 12 skipped; `makemigrations --check --dry-run`: no changes. Same counts as the baselines.

## Verification

**Commands:**
- Portal, from the repo root (browser tests need an unsandboxed shell): `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU="; export DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3"; ../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: the baseline count passes; 3 known PostgreSQL-only failures in `accounts/tests/test_crypto.py` (non-secret test keys).
- Admin, from `../mytp-admin`: `SECRET_KEY=ci-only-not-a-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,127.0.0.1,testserver DATABASE_URL=sqlite:///$TMPDIR/adm.sqlite3 FIELD_ENCRYPTION_KEYS=<same test value> BLIND_INDEX_KEY=<same test value> ALLOW_FAKE_VERIFICATION=True /private/tmp/claude-501/admin-venv2/bin/python -m pytest -q -p no:cacheprovider` -- expected: the baseline count passes.
- `manage.py makemigrations --check --dry-run` in both, same environments -- expected: no changes.
