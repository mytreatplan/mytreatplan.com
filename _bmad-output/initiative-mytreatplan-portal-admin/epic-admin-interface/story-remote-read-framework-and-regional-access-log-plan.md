---
title: 'Remote read framework and regional access log'
type: 'feature'
ticket: '5'
created: '2026-11-07'
status: 'done'
baseline_revision: 'abdb57f974ebb32bc958e8658cc338ab6a090485'
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

**Problem:** I6 (AD-3 item 3, AD-4, AD-17, AD-22, AD-23): admin users must read regional data live without the admin site ever storing it, and each region must decide access itself and log every access before data leaves; nothing does this yet.

**Approach:** Three repos, three PRs (core 0.13.0 → admin → portal). **mytp-core**: request tokens can carry the admin actor (`sub`, `role`, `regions`) as extra signed claims; a shared access module states, per remote resource, which roles may read, at which scope and which fields each sees; refusal codes for remote reads. **Portal**: a fail-closed regional access log, signed admin-only read endpoints that check the claims against the shared rules, omit fields by role, log first and answer no-store; a staging-only sample record. **Admin**: a client that calls a region with the user's access scope and renders the answer without persisting it, and a page reading the sample record.

## Boundaries & Constraints

**Always:**
- Token: `sign_request(..., extra=)` (reserved claim names refused) through `signed_get`/`signed_post`; the admin passes `access_scope(user)` (`{"sub","role","regions"}`, permissions/matrix.py) unchanged. The portal checks: issuer is the admin site, `sub` a UUID, `role` a known role, this site's region in `regions`.
- Rules in code, shared: `mytp_core.access` — role values and, per remote resource, `{role: (scope ALL|REGIONS|ASSIGNED, visible fields)}` (allow-list); AD-17: clinical roles see no financial fields, sales and finance roles see no patient names, a Clinical User only records assigned to their UUID. The admin's matrix test asserts the admin matrix and `mytp_core.access` agree for the remote resources.
- Portal access log `RemoteAccess` (own table, append-only, not eventlog): `at`, `actor_type` (admin/doctor/system), `actor_uuid`, `role`, `source` (issuer site and IP), `resource`, `object_id`, `fields` returned (names only), `outcome` (granted/refused + reason). Written with a plain create **before** the data is built; any failure → 503 `access_log_unavailable`, no data. Refusals (403 `forbidden`) are logged too (best effort, never blocking the 403).
- Responses: `Cache-Control: no-store`, JSON envelope from `mytp_core.contracts.remote`; errors as `error_body`. The admin client returns the dict only: no model, cache, task argument, log line or Sentry breadcrumb with response data; the admin page is `no-store`.
- Sample (staging only, behind `INTERFACE_SAMPLE_RECORDS=True`, refused by `integration.E006` on public sites, removed when epic-ordering's patients exist): portal `SampleRecord(uuid, patient_name (encrypted), amount (Money), assignee_uuid, note)`, command `seed_sample_record`; resource `sample_record` with the `patients` area rules; admin page under the `patients` area to pick a region and read it.
- 1.11's download hook (`storage.service._record`) writes to `RemoteAccess` instead of eventlog (actor = the portal user; same fail-closed behaviour).

- Decision (user, 2026-11-07): the access rules live in code in mytp-core (`mytp_core.access`); the admin's test asserts they agree with the admin matrix.
- Decision (user, 2026-11-07): `RemoteAccess` rows are kept 5 years, like the activity records, then purged by the existing retention job.
- Decision (user, 2026-11-07): the storage download record moves to `RemoteAccess` only (no new eventlog `file` records).
- Decision (user, 2026-11-07): one plan, three PRs (mytp-core 0.13.0 → admin, deployed first → portal), kept whole (~2300 tokens, above the size target).

**Never:** no remote writes (entry 6), no admin-side copy, cache or audit row of response data (AD-23), no change to `Role` values, `access_scope`'s shape, `InterfaceCall` semantics, eventlog's never-raise contract or the existing refusal codes' meanings.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Clinical Manager reads | sample on dev | 200, name shown, amount omitted; one `RemoteAccess` granted on dev | — |
| Commercial Manager reads | same | 403 (`role`): sales roles do not read patient records (user, 2026-11-07) | — |
| Clinical User, assigned | assignee = their UUID | 200 | — |
| Clinical User, not assigned | other assignee | 403 `forbidden`, refusal logged | — |
| Role without the area | Financial Manager on `patients` (matrix: none) | 403 | — |
| Region not permitted | regional user without `ae` reads dev-ae | 403 | — |
| Log unwritable | `RemoteAccess` insert fails | 503 `access_log_unavailable`, no data | — |
| Not the admin site | another portal signs | 401 | — |
| Admin database | after reads | no row holds response data | — |

</frozen-after-approval>

## Code Map

Repos (branch `story/4.5-remote-reads` in each, already created; do not push, tag or open PRs). Baselines: mytp-core 8ddc118aef6c4d8226094a35dc8d4ba2ed465fa2 (v0.12.0), portal abdb57f974ebb32bc958e8658cc338ab6a090485, admin 6fe7b4cad671be6892fca8a26e21399e69f5e19f. This plan lives in the planning worktree `.../code/mytreatplan-planning` (write Implementation Notes there); code goes in the main checkouts. Bump mytp-core to 0.13.0; the vendored wheel may be built from the untagged core tree for local tests (rebuilt from tag v0.13.0 at release); update both sites' `test_vendored_core.py` and the admin's `layout/tests/test_deploy.py`. Tests: portal venv `../.venv` (env: FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU=" DATABASE_URL=sqlite:///$TMPDIR/x.sqlite3; 3 known PostgreSQL-only failures in accounts/tests/test_crypto.py); admin venv `/private/tmp/claude-501/admin-venv` (same two keys plus SECRET_KEY=ci-only-not-a-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,127.0.0.1,testserver DATABASE_URL=sqlite:///$TMPDIR/adm.sqlite3 ALLOW_FAKE_VERIFICATION=True; if missing, rebuild with `uv venv -q --python 3.13 /private/tmp/claude-501/admin-venv --clear && uv pip install -q --no-cache --python /private/tmp/claude-501/admin-venv/bin/python -r requirements-dev.txt` from mytp-admin; install wheels with `uv pip install --no-cache --python ... --reinstall --no-deps`); mytp-core `../.venv`. Sandbox must be disabled for the suites. If a local PostgreSQL 17 can be started in the scratchpad, run the PostgreSQL-only tests on it.

Paths relative to `.../code/`.
- mytp-core (0.12.0): `interface.py` `sign_request` :368 (fixed claims; add `extra`), `request_hash` :360, `verify_request` :421 → `Claims` :413 (extra claims pass unchecked; add `remote_actor(claims)`), `_signed_call` :551, `signed_get` :598, `signed_post` :607, `PeerCallFailed` :124, `REASONS` :90, `refusal_body` :145; `contracts/errors.py` `REFUSAL_CODES` :14-21 (401), `UNAVAILABLE` :24, `error_body` :32-36 — add `forbidden` (403) and `access_log_unavailable` (503) as separate codes; new `contracts/remote.py` (+ `SCHEMA_MODELS` :52); new `access.py`; version → 0.13.0.
- Admin: `permissions/matrix.py` VIEW/CHANGE/EXPORT :33-36, scopes :38-41, `Grant` :48, `_MATRIX` :81-126, `grant_for` :157, `has_access` :165, `scope_for` :172, `access_scope(user)` :184-196; `permissions/areas.py` :43-75 (`patients`); `permissions/decorators.py` `area_required` :8; `staff/models.py` `Region` :55, `Role` :80-89, `REGIONAL_ROLES` :94; outbound pattern `interface/config.py` `nudge_portals` :161-176, `ping_peer.py` :29-37 → new `interface/remote.py`; no-store precedents `lists/views.py:311`, `lists/sample.py:163`; detail template precedent `lists/sample.py:130`; area urls precedent `interface/area_urls.py`; audit is for writes only (no audit row for a read).
- Portal: `integration/calls.py` `keyring` :43, `_outcome` :48, `_unavailable` :53, `signed_endpoint` :65-92 (csrf_exempt, never_cache, `request.interface_claims`), `record_call` :95 (best effort; keep); `integration/views.py` `_not_the_admin_site` :35, endpoint precedents :52, :73; `integration/urls.py` (`api/v1/`); `integration/checks.py` (E004 retired, E005 used → E006 next); `integration/models.py` `AdminDirectoryEntry` (actor names); `eventlog.service.client_ip` :34; `storage/service.py` `_record` :43-46, `download_url` :56-71 (`AccessRecordFailed`), `actor_of` :74, docstring :3-6; sample precedents `storage/sample.py`, `storage/checks.py:12,23-37`; settings `ROBOTS_NOINDEX` :123.
- Release as 4.8: tag v0.13.0, vendor both sites; admin deploys first.

## Tasks & Acceptance

**Execution:**
- [x] mytp-core: `extra` claims, `remote_actor`, `access.py`, `contracts/remote.py` + schemas, error codes, tests, 0.13.0, README.
- [x] Portal: `RemoteAccess` + migration, remote endpoint(s) + `SampleRecord` + `seed_sample_record` + E006, storage hook moved to the log, runbook, tests (every matrix row; log-first order; no-store).
- [x] Admin: `interface/remote.py`, sample page (area `patients`), matrix-agreement test, README, tests (nothing persisted, no-store).
- [x] Both sites: vendor 0.13.0 (wheel built from the untagged core tree; rebuild from tag v0.13.0 at release).

**Acceptance Criteria:**
- Given any granted read, when the response is built, then the `RemoteAccess` row was committed first and lists only the fields returned.
- Given the admin matrix changes a remote resource's grant without `mytp_core.access`, when the admin suite runs, then the agreement test fails.

## Implementation Notes

- Commercial Manager (resolved, see the Plan Change Log): refused with 403 (reason `role`); no permission widened. The sales-side "no patient names" rule is proved by mytp-core's unit tests.
- mytp-core 0.13.0: `sign_request(..., extra=)` (refuses `RESERVED_CLAIMS` = the fixed claims plus `nbf`, non-string names and non-JSON values); `signed_get`/`signed_post` pass `extra`; `interface.RemoteActor` + `remote_actor(claims)` (shape only: canonical UUID `sub`, known `role`, distinct two-letter `regions`, at most 50). `mytp_core.access`: `ROLES`, `SCOPES`, `RESOURCES` (`Resource(area, fields, financial, patient_names, rules)`), AD-17 validated at import (clinical: no financial field; sales/finance: no patient name; Clinical User: `assigned` only; `regions` only for the regional role), `decide(resource, actor, region, assignee)` → `Decision(granted, reason, fields)` (reasons `actor`, `region`, `role`, `assigned`, `resource`), `visible`. `contracts.remote`: `RemoteRecord` (`fields` sorted, exactly `data`'s keys, no float anywhere), `remote_record()`, `SampleRecordData`, `RESOURCE_READERS`; schemas `remote_record.json`, `remote_sample_record.json`. `contracts.errors`: `FORBIDDEN` 403, `NOT_FOUND` 404 (added: an unknown record needed a code), `ACCESS_LOG_UNAVAILABLE` 503, `remote_read_error(code)`; `REFUSAL_CODES`/`REASONS` unchanged.
- Portal: `integration.RemoteAccess` (append-only: instance `save` of an existing row, instance `delete` and queryset `update` raise `AppendOnly`; the retention purge deletes in bulk). Columns as the plan, with `source` split into `source_site` and `source_ip` (encrypted: a doctor's IP is personal, as it was in eventlog), plus three extra columns: `user_agent` (encrypted), `purpose` (a download's stored object purpose) and `access_reason` (a download's reason code), so the RemoteAccess row keeps what 1.11's download record had. `integration.access_log.record_granted` writes in `transaction.atomic(durable=True)` (committed before data; inside an outer transaction it refuses, so fail closed), `record_refused` best effort. `integration.remote.remote_read` is the reusable flow (admin issuer → actor/region/role checks → load → assigned check → log → build exactly the logged fields → `remote_record`, `no-store`). Endpoint `GET /api/v1/remote/sample-record` reads the newest sample record (no id in the URL: `seed_sample_record` keeps exactly one per region). With the setting off it answers 404 `not_found`, unlogged. `SampleRecord.amount` is `amount_value` + `currency` (Money property), `patient_name` encrypted. Retention: `eventlog.retention.delete_old_access` (5 calendar years from `at`), called by the existing `eventlog_retention` command. Storage downloads now log `RemoteAccess` rows (resource `stored_object`, actor `doctor` + the user's `uid`, `purpose` = the object's purpose, `access_reason` = the reason, IP and User-Agent, fields `['download_url']`, refusals reason `download_refused`); old eventlog `file` rows stay in `ACTIVITY_KINDS` until they age out.
- Admin: `interface/remote.py` `read(user, region, resource, path)` → plain dict; failures are `RemoteReadFailed(code)` with fixed texts (pydantic errors, which quote values, are swallowed). The Patients area is now implemented (`IMPLEMENTED_AREAS` + `interface.area_urls`, URL name `patients`, `/patients/`): GET shows the regions with a `site` within the user's `access_scope` regions; POST reads (CSRF, `area_required('patients')` view, not a change view so no audit entry). Both answers `no-store`. `permissions.matrix.remote_access_mismatches()` + tests (agreement, and a changed matrix is caught: removed, added, scope changed, change-without-view).
- The admin page and portal endpoint take the resource name from `mytp_core.access.SAMPLE_RECORD`; the portal's E006 mirrors storage.E002.
- Review patches (2026-11-07): core `contracts/remote.py` docstring names the real route; admin `test_nothing_of_the_record_is_kept` reads every cache key back with `cache.get` (`everything_cached()`) and uses an ASCII marker too, with `test_the_cache_check_finds_a_cached_record` as a permanent proof (also checked by hand: a deliberate `cache.set` of the record in `remote.read` made the test fail); admin LOGGING has `interface.remote`; portal `remote_sample_record` runs the admin-site check before the setting (401 for another peer even with the setting off, tested); runbook route and staging check (Sysadmin, Clinical Manager, Commercial Manager expecting 403); `eventlog.models.Attempt.FILE` comment. Migration 0006 regenerated (still uncommitted) with the new columns. Wheel rebuilt (0.13.0) and re-vendored in both sites.

## Verification results (2026-11-07, local)

- mytp-core: 1093 passed (SQLite).
- Portal: 2060 passed, 5 skipped, 3 failed: the known PostgreSQL-only `accounts/tests/test_crypto.py` trio (SQLite).
- Admin: 906 passed, 5 skipped (SQLite). `manage.py check` and `makemigrations --check` clean on both sites.
- Local PostgreSQL 17.11 (scratchpad cluster): mytp-core 1093 passed; admin 911 passed; portal 2068 passed (no skips; the SQLite-only failures pass there).
- After the review patches (SQLite): mytp-core 1093 passed; admin 907 passed, 5 skipped; portal 2061 passed, 5 skipped, the known 3 PostgreSQL-only failures. `check` and `makemigrations --check` clean on both sites. (Not re-run on PostgreSQL.)

## Plan Change Log

- 2026-11-07 (user renegotiated the frozen matrix): the Commercial Manager row changed from "200, amount shown, name omitted" to 403 (`role`); the matrix gives sales roles no `patients` area and none is added. Staging check reads as a Sysadmin, a Clinical Manager and a Commercial Manager (expects 403).

## Review Triage Log

Opus reviews, one per repo (2026-11-07):
- intent_gap → resolved by the user: the Commercial Manager row stays 403 (see Plan Change Log). The runbook uses the Commercial Manager expecting 403, not a Clinical User substitute.
- patch (core): `contracts/remote.py` docstring names `/api/v1/remote/<resource>`; the route is `/api/v1/remote/sample-record`.
- patch (admin): `interface/tests/test_remote.py::test_nothing_of_the_record_is_kept` cannot fail (pickled LocMemCache values, non-ASCII name) → inspect via `cache.get` / ASCII markers.
- patch (admin): logger `interface.remote` missing from `mytp_admin/settings.py` LOGGING → add with console INFO.
- patch (portal): `storage/service.py` `_record` drops the stored object's purpose (and user agent) that the 1.11 record kept → keep them in the RemoteAccess row.
- patch (portal): `integration/views.py` `remote_sample_record` checks `sample.enabled()` before `_not_the_admin_site` → a non-admin peer learns the setting (404 vs 401) and the call is logged accepted → admin-site check first.
- patch (portal): `deploy/README.md` documents `/api/v1/remote/<resource>` → `/api/v1/remote/sample-record`.
- patch (portal): stale comment above `FILE = 'file'` in `eventlog/models.py`.

## Verification

**Commands:**
- mytp-core, admin and portal suites — all pass; CI runs PostgreSQL.

**Manual checks (staging):**
- `INTERFACE_SAMPLE_RECORDS=True` on dev and dev-ae; `seed_sample_record` on each; on admindev a Sysadmin, a Clinical Manager and a Commercial Manager read the sample on dev and dev-ae (fields per role); each read is in that region's `RemoteAccess`; a Clinical User not assigned gets 403; admindev's database holds no record data; with the log table's insert revoked on dev (test database role or a monkeypatched check), the read answers 503.

## Staging

2026-11-07, run by Claude with the user's delegation. mytp-core v0.13.0 (tag CI green); admin PR #19 merged (efb3c9f), deployed to admindev; portal PR #74 merged (c22f2d5), CI and deploy green on dev and dev-ae. Local suites: core 1093, admin 907 passed / 5 skipped, portal 2061 passed / 5 skipped + the 3 known PostgreSQL-only failures.
- `INTERFACE_SAMPLE_RECORDS=True` on dev and dev-ae, `check` clean, `seed_sample_record` on each (made-up name, assignee set).
- From admindev on both regions: Sysadmin 200 with every field; Clinical Manager 200 with the amount omitted; Clinical User assigned 200; Commercial Manager 403 (`role`); Financial Manager 403 (`role`); Clinical User not assigned 403 (`assigned`). Non-Sysadmin roles were in-memory users (admindev has only a Sysadmin).
- Each region's `RemoteAccess` holds exactly those six reads with the matching outcome and reason.
- admindev's database: no row holds the sample's name.
- Not run live: the 503 when the log cannot be written (no safe way to revoke the insert on staging); covered by the portal tests.
