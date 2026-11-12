---
title: 'Voucher definitions'
type: 'feature'
ticket: '10'
created: '2026-11-12'
status: 'built'
baseline_revision: 'c942b2871873cfaea6cba40fb1aa70608bc41962'
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

**Problem:** C9 (AD-20): staff cannot create the discount codes the requirements describe (subscription vouchers and try-me vouchers), and the regions do not know them, so epic-subscriptions and epic-ordering have nothing to redeem.

**Approach:** On the admin site, the `vouchers` area gets voucher definitions with a generated code; each is published in the configuration snapshot only to the portal of its region (mytp-core 0.24.0, a new optional `vouchers` section); the portal stores them read-only and reads one by its code.

## Boundaries & Constraints

**Always:**
- A voucher belongs to one region and is sent only to that region's portal (AD-20); a portal also skips a voucher that names another region.
- The amount is a fixed amount in EUR, AED or USD (the Money type, AD-6).
- Never hard-deleted: a voucher is deactivated, and a deactivated one is still sent (past orders read it).
- Every change audited and rebuilding the snapshot.
- Contract compatibility as in entries 5 to 9: the new section is optional, an absent one is not dumped, an older portal ignores it.
- Who edits is the permission matrix as it stands: Sysadmin, Financial Manager and Commercial Manager for every region; a Commercial Regional Manager for their own regions only.

**Never:** no redemption, reservation, use count or "already used" state (epic-subscriptions, AD-20); no application of a voucher to a price or currency-match refusal (entry 12); no usage report (epic-sales-finance); no change to how the other snapshot sections travel, no read-only test of the portal tables (entry 11); no client list or search page on the admin site.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| General try-me | type try-me, region eu, 50.00 EUR, valid from today | saved with a generated code; in the snapshot served to the eu portal; readable on dev by its code | an amount of 0 or less, an unknown currency or a region without a site refused |
| For one doctor | type subscription, region eu, a doctor of the eu region | saved with that doctor's client UUID; dev reads the voucher with the UUID | a doctor that is not found, not active, or of another region refused |
| Another region | a voucher of region ae | in the snapshot served to dev-ae; absent from the one served to dev | — |
| Code | two vouchers created | two different codes, unique across all regions | a collision is drawn again, never shown to the editor |
| Deactivate | an active voucher deactivated, then reactivated | sent as inactive, then active; the portal says it is not usable while inactive | — |
| Validity | today outside the voucher's dates | sent; the portal says it is not in force | an end date before the start date refused |
| Regional manager | a Commercial Regional Manager of eu | lists, creates and changes eu vouchers only | an ae voucher is not listed and its pages answer 404 |
| Unknown peer | a portal whose site has no region | its snapshot carries no voucher | — |
| Old snapshot | an admin site before this release | dev keeps its vouchers table empty and works | — |
| Old portal | a portal on mytp-core 0.23 and a snapshot with vouchers | applies the snapshot and ignores them | — |

## Decisions

- Decision (user, 2026-11-12): the code is generated only: 10 characters from letters and digits without the confusable ones (0, O, 1, I, L), shown as `XXXXX-XXXXX`; a doctor may type it with or without the hyphen and in lower case. The editor never types a code.
- Decision (user, 2026-11-12): for a doctor-specific voucher the editor types the doctor's email; the admin site finds the client record of the voucher's region and shows the name to confirm before saving.
- Decision (user, 2026-11-12): after creation only the validity dates, the internal note and active or not can change; code, type, region, amount, currency and doctor are fixed (a wrong voucher is deactivated and created again).
- Decision (user, 2026-11-12): both validity days are included, counted in Madrid time as product validity is; the start defaults to today and the end may be left empty (no end).
- Decision (user, 2026-11-12): no product restriction in this entry: a try-me voucher discounts any service order, a subscription voucher only a subscription.
- Decision (user, 2026-11-12): the definition carries no use limit: epic-subscriptions enforces the limits from the type (a subscription code once in total, a try-me code once per doctor).
- Decision (user, 2026-11-12): one plan, kept whole; mytp-core 0.24.0 → admin → portal. Merge and staging delegated.

</frozen-after-approval>

## Code Map

- Repositories: this plan is in the portal (`mytreatplan.com`); mytp-core is `../mytp-core` (baseline 92ab671bc4dfaef453aa4c00b346f0a8febf6817) and the admin site `../mytp-admin` (baseline 56c6a6d709964ceb4a7b83f05c4f4ee1584e59dd). The branch `story/5.10-vouchers` is checked out in all three: work there, commit nothing, push nothing, tag nothing. Do not build or copy a wheel: point both `requirements.txt` at `./vendor/mytp_core-0.24.0-py3-none-any.whl`, update the version tests (core `tests/test_hub.py:30`, both sites' `test_vendored_core.py:20`, admin `layout/tests/test_deploy.py`), and run the two site suites with `PYTHONPATH=../mytp-core` until the release (two admin tests then fail on the missing wheel: expected). Suites: each README; portal venv `../.venv`, admin venv `/private/tmp/claude-501/admin-venv2`. Search by name when a line number is off.
- mytp-core: `contracts/config.py` `OPTIONAL_SECTIONS` :56, `ConfigSnapshot` :69 (docstring :29 already promises vouchers as a new optional field); `contracts/provider_costs.py` (the model of a small top-level section); `contracts/catalogue.py` `PRODUCT_CODE_PATTERN` :344, `_unique` :969; `contracts/amounts.py` `Amount`; `money.py` `CURRENCIES`; schemas `SCHEMA_MODELS` `contracts/__init__.py:103`, drift test `tests/test_contracts.py:314`. `tests/test_catalogue.py:74-76` and `tests/test_snapshots.py:88-92` use `vouchers` as their example of an unknown field: give them another name.
- Admin: `permissions/areas.py:61` (the `vouchers` area exists, placeholder), `permissions/urls.py:11-25` `IMPLEMENTED_AREAS`, `permissions/matrix.py:83,104,120,125-127` (regional scope REGIONS: the model needs a `region` foreign key to `staff.Region`, `staff/models.py:55`); the `billing/` app is the template (models, `service.py:91-147` create/update/set-active with audit and `config_snapshot.rebuild()`, views, urls, templates, `tests/test_billing.py`); `lists/scopes.py` `scope_queryset(region_field=…)`, `lists/views.py:103-200`; `audit/registry.py:67-71`; `interface/config.py` `register_section` :69, `rebuild` :127-150 (one stored body, one version), `interface/views.py:40-60` `config()` (signs `row.body` for the caller; the caller's region is `Region.objects.filter(site=iss)`, as `interface/hub.py:57`); `clients/models.py:9` `ClientRecord` (`uid`, `region`, `active`, `email_index` blind index; erasure deletes the row); `staff/service.py:45,87` (`secrets.choice`, the only random-code precedent).
- Portal: `integration/config.py` `register_section_applier` :95; `catalogue/applier.py:121-196`, `catalogue/apps.py:19-20`, `catalogue/models.py` `ReadOnlyModel` :47, last migration `0005`; `catalogue/resolver.py` `subscription_by_code` :235 (the model for the read by code); `catalogue/management/commands/price_catalogue.py`; `settings.REGION` (`mytreatplan/settings.py:46-48`; `regions/tests/test_guard.py` forbids comparing against a region's name); the client UUID is `accounts/models.py:87` `User.uid`.

## Tasks & Acceptance

**Execution:**
- [x] mytp-core 0.24.0 (`mytp_core/contracts/vouchers.py`, `contracts/config.py`, `contracts/__init__.py`, `schemas/config_snapshot.json`, version in `pyproject.toml` and `__init__.py`, README, tests) -- the optional top-level `vouchers` section: a list of vouchers (id, code, type `subscription` or `try_me`, region code, amount, optional client UUID, valid from, optional valid to, active); codes unique; unknown types kept as text for later readers; a helper that says whether a voucher is in force on a day; a helper that returns a snapshot body with only one region's vouchers (the section left out when none) -- one contract and one filter for both sites.
- [x] Admin (`vouchers/` app as `billing/`, `permissions/urls.py`, `audit/registry.py`, `mytp_admin/urls.py`, settings, migration, README, tests) -- `Voucher` (code, type, region, amount, currency, optional client UUID, dates, internal note, active) with list (region filter, export), form, detail with history, deactivate and reactivate, scoped by region; the code generator; the doctor lookup; its snapshot section.
- [x] Admin (`interface/views.py`, tests) -- `config()` serves the stored body filtered to the caller's region with the core helper, then signs it; the stored body and the single version stay as they are.
- [x] Portal (`catalogue/models.py`, `applier.py`, `apps.py`, `resolver.py`, `management/commands/price_catalogue.py`, migration `0006`, `deploy/README.md`, tests) -- a read-only `Voucher` table replaced at each apply (a voucher of another region skipped with a warning; an absent section empties the table); `resolver.voucher_by_code(code)` and whether it is usable on a day; `price_catalogue --voucher CODE` prints it.
- [x] Both sites -- vendor the new core's wheel name and pinned versions.
- [x] Tests for every row of the matrix; a PostgreSQL-only test creates its own regions, country rows and groups.

**Acceptance Criteria:**
- Given a general try-me voucher and a doctor-specific subscription voucher of region eu on admindev, when dev pulls the configuration, then `price_catalogue --voucher` prints each with its code, amount and, for the second, the doctor's UUID.
- Given a voucher of region ae, when dev and dev-ae pull, then dev-ae reads it and dev does not have it in its table nor in the snapshot body it stored.
- Given 1,000 generated codes, when compared, then all differ and none holds a confusable character.

## Implementation Notes

- mytp-core: `contracts/vouchers.py`. On the wire a code is 4 to 32 uppercase letters and digits, wider than what the generator writes (10 characters of the safe alphabet), because a reader rule cannot be relaxed later. A typed code is read without spaces, hyphens and typographic dashes, in any case. `vouchers_for_region(body, region)` leaves the key out when no voucher is left or the region is None.
- Admin: a new `vouchers` app. The stored body lists every region's vouchers (an empty list before the first one, so the first write after the deploy builds one new version); `interface/views.config` serves the body filtered to the region whose site is the caller. The form shows the doctor's name and saves on the second submit; the service checks the doctor again. A doctor of another region and an unknown email get the same message.
- Portal: `catalogue.Voucher` (read-only, migration `0006`), its own section applier, `resolver.voucher_by_code`, `voucher_state` (`usable`, `inactive`, `not_in_force`, `unknown_type`, `unknown_currency`) and `voucher_usable`; `price_catalogue --voucher CODE [--on DATE]`. Whether a doctor matches the voucher's client is left to epic-subscriptions.
- The portal stores no snapshot body: the staging check of the filter reads dev's log (no "voucher(s) of another region skipped" warning, the applied line's count).

## Plan Change Log

## Review Triage Log

Quick lens, one reviewer per repository (2026-11-12). 7 findings: 1 medium, 6 low; 4 patched, 3 rejected.

| # | Repository | Finding | Verdict | Evidence | Route |
|---|-----------|---------|---------|----------|-------|
| 1 | admin | the doctor lookup tells a regional manager whether an email is a doctor of another region | medium | `find_client` searched every region and answered "another region" or "not found"; the reviewer got both texts as an eu Commercial Regional Manager | patch: one message, the search covers the voucher's region only; tests for both roles |
| 2 | core | a code pasted with a typographic dash is refused | low | `normalize_code` removed the ASCII hyphen and spaces only | patch: U+2010 to U+2015 and U+2212 removed too; test |
| 3 | portal | `price_catalogue --voucher` accepts `--language` and ignores it | low | the guard did not list `language` | patch: refused; test |
| 4 | portal | the README's staging check cannot prove the admin-side filter | low | the portal stores no body and skips another region's vouchers itself | patch: the README checks dev's log |
| 5 | portal | the "old portal" test only removes the applier | low | true; core's tests cover readers ignoring unknown fields and the signature over the body as sent, and staging runs it for real (the admin site deploys first) | rejected: unlikely to hurt, the fix needs a second core version in the suite |
| 6 | portal | the `Voucher` row repeats core's in-force and money checks | low | two three-line checks; core's are methods of the contract model | rejected: the fix adds public functions to core |
| 7 | admin | the PostgreSQL race test does not force the two inserts to overlap | low | true; `test_two_vouchers_have_different_codes_and_a_taken_code_is_drawn_again` forces the taken-code path | rejected: the fix adds test machinery for a path already covered |

## Design Notes

- Vouchers are a top-level section, not a list inside `catalogue`: the per-region filter then touches one key, and the catalogue section stays byte-identical for every region.
- One version for all regions is kept: an ae-only change makes dev apply the same content again under a new number, which is harmless. Per-region versions are entry 11's to decide.
- A doctor's erasure deletes the client record; the voucher keeps the UUID (not personal data on its own) and the admin page shows "doctor record removed". No portal user has that UUID any more, so the voucher cannot be used.

## Verification

**Commands:**
- mytp-core, admin and portal suites -- expected: all pass (the portal's 3 known PostgreSQL-only failures aside); CI on PostgreSQL.

**Manual checks (staging):**
- On admindev, as `staging_check_5_10` through the service: a general try-me voucher (eu, EUR), a subscription voucher for the staging sample doctor (eu), a try-me voucher of region ae (AED); one deactivated then reactivated. On dev: the first `pull_config` after the deploy prints `applied`; `price_catalogue --voucher` reads the two eu vouchers and not the ae one; the stored snapshot body holds no ae voucher. On dev-ae: the ae voucher only.
